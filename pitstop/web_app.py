"""The public, read-only web scanner. This is what gets deployed.

Deliberately a different application from `server.py`, not a mode of it.
`server.py` is the local UI: it plans, it applies, it holds OAuth tokens, and it
is meant to run on the machine of the person who owns the channel. This module
is the opposite in every respect — it is handed to strangers, so the safest
thing it can be is a program with no write capability anywhere in its import
graph.

That is a structural claim, not a promise. This file imports `public_scan`,
which imports the fetcher, the checks and the scorer. Nothing here imports
`applier` or `planner`, and the YouTube client it builds is constructed with
`owner=False`, so it holds an API key and has no OAuth token to write with.
There is no endpoint to remove, because there is no endpoint.

The split it presents is the real one:

    the web page   audits any channel and prices the damage   — no login
    the CLI        repairs a channel you own                  — needs OAuth

Progress streams over SSE. A public scan takes ~20-40 seconds, nearly all of it
resolving links one HTTP request at a time, and a spinner with no detail for
that long reads as broken. If a proxy buffers the stream the page still works —
the terminal `result` event carries the entire report, so buffering degrades
this to a plain slow POST rather than to a failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import CONFIG
from .public_scan import (
    DEFAULT_LINK_CAP,
    DEFAULT_LINK_CONCURRENCY,
    DEFAULT_LINK_TIME_BUDGET,
    DEFAULT_PLAYLIST_CAP,
    DEFAULT_VIDEO_CAP,
    PublicScanError,
    public_scan,
    redact,
    result_json,
)

app = FastAPI(title="Pitstop — public channel audit", version="0.1.0",
              docs_url=None, redoc_url=None)

PAGE = Path(__file__).resolve().parent / "web_public" / "index.html"


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, "")))
    except (TypeError, ValueError):
        return default


# Every ceiling is env-tunable so the deployment can be tightened without a
# code change if quota gets tight the night before judging.
VIDEO_CAP = _int_env("PITSTOP_PUBLIC_VIDEO_CAP", DEFAULT_VIDEO_CAP)
PLAYLIST_CAP = _int_env("PITSTOP_PUBLIC_PLAYLIST_CAP", DEFAULT_PLAYLIST_CAP)
LINK_CAP = _int_env("PITSTOP_PUBLIC_LINK_CAP", DEFAULT_LINK_CAP)
LINK_TIME_BUDGET = float(_int_env("PITSTOP_PUBLIC_LINK_SECONDS",
                                  int(DEFAULT_LINK_TIME_BUDGET)))
LINK_CONCURRENCY = _int_env("PITSTOP_PUBLIC_LINK_CONCURRENCY",
                            DEFAULT_LINK_CONCURRENCY)

# A repeated scan of the same channel costs the same quota and the same 30
# seconds for an identical answer. Judges paste the same famous handle more
# than once, so warm instances keep the last few results. Cached responses say
# so on the page — a stale number presented as live is the same dishonesty as
# a truncated scan presented as complete.
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 15 * 60
_CACHE_MAX = 24


def _cache_get(key: str) -> dict[str, Any] | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    stored_at, payload = hit
    age = time.time() - stored_at
    if age > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return {**payload, "cached_age_seconds": int(age)}


def _cache_put(key: str, payload: dict[str, Any]) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
    _CACHE[key] = (time.time(), payload)


class ScanRequest(BaseModel):
    channel: str = Field(..., max_length=300,
                         description="Channel URL, @handle or UC… id")


# HTTP status per failure kind. 404 for a channel that isn't there, 429 for a
# spent quota (it is a rate limit and it will recover), 503 for a deployment
# that was never configured.
_STATUS = {"not_found": 404, "quota": 429, "config": 503, "upstream": 502}


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))


@app.get("/api/healthz")
async def healthz() -> dict:
    """Liveness plus what this deployment is actually able to do."""
    return {
        "ok": True,
        "version": "0.1.0",
        "read_only": True,
        "has_api_key": CONFIG.has_api_key,
        "caps": {
            "videos": VIDEO_CAP,
            "playlists": PLAYLIST_CAP,
            "links": LINK_CAP,
            "link_seconds": LINK_TIME_BUDGET,
            "link_concurrency": LINK_CONCURRENCY,
        },
    }


@app.post("/api/scan")
async def scan(req: ScanRequest):
    """Audit a public channel, streaming progress as SSE.

    Always 200 at the transport level once the stream opens — failures arrive
    as an `error` event, because an SSE body cannot change its status code
    after the first byte. `/api/scan.json` is the non-streaming twin and does
    use real status codes.
    """
    channel = req.channel.strip()
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(event: str, data: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    def work() -> dict[str, Any]:
        cached = _cache_get(channel.lower())
        if cached:
            return cached
        result = public_scan(
            channel,
            video_cap=VIDEO_CAP, playlist_cap=PLAYLIST_CAP,
            link_cap=LINK_CAP, link_time_budget=LINK_TIME_BUDGET,
            link_concurrency=LINK_CONCURRENCY,
            progress=lambda phase, stage, done, total: emit(
                "progress", {"phase": phase, "stage": stage,
                             "done": done, "total": total}),
        )
        payload = result_json(result)
        _cache_put(channel.lower(), payload)
        return payload

    async def worker() -> None:
        try:
            await queue.put(("progress", {"phase": "fetch",
                                          "stage": "Resolving channel",
                                          "done": 0, "total": None}))
            payload = await asyncio.to_thread(work)
            await queue.put(("result", payload))
        except PublicScanError as exc:
            await queue.put(("error", {"kind": exc.kind,
                                       "message": exc.message,
                                       "hint": exc.hint}))
        except Exception as exc:  # noqa: BLE001 — never hang the page
            await queue.put(("error", {
                "kind": "upstream",
                "message": "Something went wrong running that scan.",
                "hint": redact(f"{type(exc).__name__}: {exc}")}))
        finally:
            await queue.put(("done", {}))

    async def stream():
        task = asyncio.create_task(worker())
        try:
            while True:
                event, data = await queue.get()
                yield (f"event: {event}\n"
                       f"data: {json.dumps(data, default=str)}\n\n")
                if event == "done":
                    break
        finally:
            task.cancel()

    return StreamingResponse(
        stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform",
                 "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"})


@app.post("/api/scan.json")
async def scan_json(req: ScanRequest):
    """The same scan, one JSON response, real status codes.

    Exists so the deployed audit is scriptable — `curl -d '{"channel":"@x"}'`
    — and so the failure paths can be tested without an SSE parser.
    """
    channel = req.channel.strip()
    try:
        cached = _cache_get(channel.lower())
        if cached:
            return cached
        payload = await asyncio.to_thread(
            lambda: result_json(public_scan(
                channel,
                video_cap=VIDEO_CAP, playlist_cap=PLAYLIST_CAP,
                link_cap=LINK_CAP, link_time_budget=LINK_TIME_BUDGET,
                link_concurrency=LINK_CONCURRENCY)))
        _cache_put(channel.lower(), payload)
        return payload
    except PublicScanError as exc:
        return JSONResponse(
            status_code=_STATUS.get(exc.kind, 400),
            content={"error": exc.kind, "message": exc.message,
                     "hint": exc.hint})
