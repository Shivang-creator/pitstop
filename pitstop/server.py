"""FastAPI backend for the web UI.

The engine is shared verbatim with the CLI — this module adds transport, not
logic. Anything that decides *what* to do lives in checks/, planner.py and
score.py, so the CLI and the UI can never disagree about what a scan found.

Scans stream progress over Server-Sent Events. A 500-video catalog fetch plus
a link sweep takes 30–60 seconds, and a spinner with no detail for a minute
reads as broken. SSE (rather than WebSockets) because the traffic is strictly
one-directional and SSE reconnects on its own.

Scan results are held in an in-process store keyed by an opaque id. That is
deliberate for a hackathon-scale tool: no database to provision, and nothing
about a user's channel is written to disk unless they ask for JSON output.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import score as scoring
from .applier import apply_plan
from .catalog import fetch_catalog
from .checks import CheckContext, all_checks, run_all
from .config import CONFIG, ROOT
from .models import Catalog, Finding
from .planner import build_plan, split_by_budget
from .quota import QuotaLedger
from .rules import load_rules
from .youtube import AuthRequired, YouTubeClient, YouTubeError

app = FastAPI(title="Pitstop", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIST = ROOT / "web" / "dist"

# scan_id -> {catalog, findings, skipped, report, client, created_at}
_STORE: dict[str, dict[str, Any]] = {}
_STORE_TTL_SECONDS = 60 * 60


def _gc() -> None:
    cutoff = time.time() - _STORE_TTL_SECONDS
    for key in [k for k, v in _STORE.items() if v["created_at"] < cutoff]:
        _STORE.pop(key, None)


# ---------------------------------------------------------------------------
# serialisation
# ---------------------------------------------------------------------------


def _finding_json(finding: Finding, catalog: Catalog) -> dict:
    video = catalog.video(finding.video_id) if finding.video_id else None
    return {
        "check_id": finding.check_id,
        "severity": finding.severity.value,
        "title": finding.title,
        "detail": finding.detail,
        "video_id": finding.video_id,
        "video_title": video.title if video else None,
        "video_url": (f"https://www.youtube.com/watch?v={finding.video_id}"
                      if finding.video_id else None),
        "thumbnail_url": video.thumbnail_url if video else None,
        "impact_views": finding.impact_views,
        "auto_fixable": finding.auto_fixable,
        "evidence": finding.evidence,
    }


def _scan_json(scan_id: str, entry: dict) -> dict:
    catalog: Catalog = entry["catalog"]
    findings: list[Finding] = entry["findings"]
    report: scoring.ScoreReport = entry["report"]
    client: YouTubeClient = entry["client"]

    ranked = scoring.rank(findings, catalog)
    grouped: dict[str, dict] = {}
    for finding in ranked:
        key = f"{finding.check_id}|{finding.title}"
        bucket = grouped.setdefault(key, {
            "check_id": finding.check_id,
            "title": finding.title,
            "severity": finding.severity.value,
            "count": 0,
            "impact_views": 0,
            "auto_fixable": 0,
            "instances": [],
        })
        bucket["count"] += 1
        bucket["impact_views"] += finding.impact_views
        bucket["auto_fixable"] += 1 if finding.auto_fixable else 0
        if len(bucket["instances"]) < 25:
            bucket["instances"].append(_finding_json(finding, catalog))

    check_meta = {c.id: c.description for c in all_checks()}
    for bucket in grouped.values():
        bucket["description"] = check_meta.get(bucket["check_id"], "")

    return {
        "scan_id": scan_id,
        "channel": asdict(catalog.channel),
        "is_owner": catalog.is_owner,
        "mode": client.mode,
        "score": asdict(report),
        "groups": list(grouped.values()),
        "skipped": [asdict(s) for s in entry["skipped"]],
        "quota": {"spent": client.ledger.spent, "budget": client.ledger.budget,
                  "remaining": client.ledger.remaining},
        "video_count": len(catalog.videos),
    }


# ---------------------------------------------------------------------------
# scan (streaming)
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    channel: str = Field(..., description="URL, @handle or UC… id")
    limit: int | None = Field(None, ge=1, le=2000)
    owner: bool = False
    fixture: str | None = None


def _run_scan(req: ScanRequest, emit) -> str:
    """Blocking scan. Runs on a worker thread; `emit` is thread-safe."""
    ledger = QuotaLedger(budget=CONFIG.quota_budget)
    client = YouTubeClient(ledger=ledger, fixture=req.fixture, owner=req.owner)
    rules = load_rules()

    def on_fetch(stage: str, done: int, total: int | None) -> None:
        emit("progress", {"phase": "fetch", "stage": stage,
                          "done": done, "total": total})

    catalog = fetch_catalog(client, req.channel, limit=req.limit,
                            with_captions=req.owner and bool(req.limit),
                            with_analytics=req.owner, progress=on_fetch)

    emit("progress", {"phase": "checks", "stage": "Starting checks",
                      "done": 0, "total": len(all_checks())})

    def on_check(name: str, done: int, total: int) -> None:
        emit("progress", {"phase": "checks", "stage": name,
                          "done": done, "total": total})

    ctx = CheckContext(has_network=True, has_llm=CONFIG.has_llm, rules=rules)
    findings, skipped = run_all(catalog, ctx, progress=on_check)
    report = scoring.compute(catalog, findings)

    scan_id = secrets.token_urlsafe(12)
    _STORE[scan_id] = {"catalog": catalog, "findings": findings,
                       "skipped": skipped, "report": report,
                       "client": client, "created_at": time.time()}
    _gc()
    return scan_id


@app.post("/api/scan")
async def scan_stream(req: ScanRequest):
    """Run a scan, streaming progress as SSE, ending with the full result."""
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(event: str, data: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    async def worker() -> None:
        try:
            scan_id = await asyncio.to_thread(_run_scan, req, emit)
            await queue.put(("result", _scan_json(scan_id, _STORE[scan_id])))
        except (YouTubeError, AuthRequired) as exc:
            await queue.put(("error", {"message": str(exc)}))
        except Exception as exc:  # noqa: BLE001 — surface it, don't hang the UI
            await queue.put(("error", {"message": f"{type(exc).__name__}: {exc}"}))
        finally:
            await queue.put(("done", {}))

    async def stream():
        task = asyncio.create_task(worker())
        try:
            while True:
                event, data = await queue.get()
                yield f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
                if event == "done":
                    break
        finally:
            task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/scan/{scan_id}")
async def get_scan(scan_id: str):
    entry = _STORE.get(scan_id)
    if not entry:
        raise HTTPException(404, "Scan not found or expired")
    return _scan_json(scan_id, entry)


# ---------------------------------------------------------------------------
# plan / apply
# ---------------------------------------------------------------------------


class PlanRequest(BaseModel):
    only_checks: list[str] | None = None
    only_videos: list[str] | None = None


def _change_json(change) -> dict:
    return {
        "video_id": change.video_id,
        "video_title": change.video_title,
        "video_url": f"https://www.youtube.com/watch?v={change.video_id}",
        "field": change.field,
        "current": change.current,
        "proposed": change.proposed,
        "note": change.note,
        "check_id": change.check_id,
    }


@app.post("/api/scan/{scan_id}/plan")
async def make_plan(scan_id: str, req: PlanRequest):
    entry = _STORE.get(scan_id)
    if not entry:
        raise HTTPException(404, "Scan not found or expired")

    plan, conflicts = build_plan(entry["catalog"], entry["findings"],
                                 only_checks=req.only_checks,
                                 only_videos=req.only_videos)
    today, deferred = split_by_budget(plan, CONFIG.quota_budget)
    entry["plan"] = today
    entry["deferred"] = deferred

    return {
        "changes": [_change_json(c) for c in today.changes],
        "deferred": len(deferred.changes),
        "affected_videos": today.affected_videos,
        "quota_cost": today.quota_cost,
        "quota_budget": CONFIG.quota_budget,
        "conflicts": [asdict(c) for c in conflicts],
        "manual_only": len(today.skipped),
    }


class ApplyRequest(BaseModel):
    confirm: bool = False
    dry_run: bool = False


@app.post("/api/scan/{scan_id}/apply")
async def do_apply(scan_id: str, req: ApplyRequest):
    entry = _STORE.get(scan_id)
    if not entry:
        raise HTTPException(404, "Scan not found or expired")
    plan = entry.get("plan")
    if not plan:
        raise HTTPException(400, "Build a plan first")
    if not req.confirm and not req.dry_run:
        raise HTTPException(400, "confirm must be true to apply")

    client: YouTubeClient = entry["client"]
    if not client.is_owner and client.mode != "FIXTURE":
        raise HTTPException(
            403, "This scan was read-only. Re-scan with owner access to apply.")

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(stage: str, done: int, total: int) -> None:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            ("progress", {"stage": stage, "done": done, "total": total}))

    async def worker() -> None:
        try:
            result = await asyncio.to_thread(
                apply_plan, client, plan, dry_run=req.dry_run, progress=emit)
            await queue.put(("result", {
                "applied": len(result.applied),
                "videos": len({c.video_id for c in result.applied}),
                "failed": [{"video_id": c.video_id, "field": c.field,
                            "error": err} for c, err in result.failed],
                "quota_spent": result.quota_spent,
                "stopped_early": result.stopped_early,
                "dry_run": req.dry_run,
                "channel_url": _channel_url(entry["catalog"]),
            }))
        except AuthRequired as exc:
            await queue.put(("error", {"message": str(exc)}))
        except Exception as exc:  # noqa: BLE001
            await queue.put(("error", {"message": f"{type(exc).__name__}: {exc}"}))
        finally:
            await queue.put(("done", {}))

    async def stream():
        task = asyncio.create_task(worker())
        try:
            while True:
                event, data = await queue.get()
                yield f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
                if event == "done":
                    break
        finally:
            task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _channel_url(catalog: Catalog) -> str:
    ch = catalog.channel
    if ch.handle:
        return f"https://www.youtube.com/@{ch.handle}/videos"
    return f"https://www.youtube.com/channel/{ch.id}/videos"


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------


@app.get("/api/checks")
async def list_checks():
    return [{
        "id": c.id, "name": c.name, "description": c.description,
        "requires_owner": c.requires_owner,
        "requires_network": c.requires_network,
        "requires_llm": c.requires_llm,
    } for c in all_checks()]


@app.get("/api/capabilities")
async def capabilities():
    """What this install can actually do — drives the UI's empty states."""
    return {
        "has_api_key": CONFIG.has_api_key,
        "has_oauth": CONFIG.has_oauth,
        "has_token": CONFIG.token_file.exists(),
        "has_llm": CONFIG.has_llm,
        "quota_budget": CONFIG.quota_budget,
        "check_count": len(all_checks()),
        "fixtures": sorted(p.stem for p in (ROOT / "fixtures").glob("*.json")),
    }


@app.get("/api/health")
async def health():
    return {"ok": True, "version": "0.1.0"}


# ---------------------------------------------------------------------------
# static UI (built assets, when present)
# ---------------------------------------------------------------------------

if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"),
              name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        candidate = WEB_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")
