"""One read-only scan of any public channel, with every ceiling declared.

This is the function behind the public web page. It exists so the web layer can
stay pure presentation: it fetches, checks, scores and ranks by calling exactly
the same engine the CLI calls — `fetch_catalog`, `run_all`, `score.compute`,
`score.rank` — and adds nothing of its own to the verdict. There is no second
scoring model here, and there is deliberately nowhere to put one.

Three things make this different from `pitstop scan`, and all three are about
running inside a single HTTP request for a stranger:

  1. **It cannot write.** The client is constructed with `owner=False`, so it
     holds an API key and nothing else. This module does not import `applier`
     or `planner`, so no code path from here reaches a write endpoint at all.
     Repairs need OAuth on a channel you own; they stay in the CLI.

  2. **It is bounded.** A scan of someone's 4,000-video channel cannot run for
     four minutes. Videos, playlists and link resolution each get a ceiling.

  3. **It says so.** Every ceiling that actually bit comes back in `limits`,
     and the caller is expected to put them on screen. A truncated scan
     presented as a complete one is a lie about the one number the whole page
     exists to deliver, so silent truncation is treated as a bug here.

The operator's own `pitstop.yaml` is deliberately *not* loaded. Those are one
creator's private conventions — "every description ends with my subscribe
link". Grading a stranger's channel against them would manufacture findings out
of a config file they have never seen.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from . import score as scoring
from .catalog import fetch_catalog
from .config import CONFIG
from .checks import CheckContext, SkippedCheck, all_checks, run_all
from .models import Catalog, Finding
from .quota import QuotaExceeded, QuotaLedger
from .youtube import YouTubeClient, YouTubeError

# --- ceilings ---------------------------------------------------------------
# Tuned against real channels rather than guessed. A 150-video fetch of a
# 13-playlist channel costs ~25 quota units and ~8s; link resolution is the
# only genuinely unbounded phase, so it gets a wall clock as well as a count.

DEFAULT_VIDEO_CAP = 150
# Each playlist is its own paged walk, which makes the playlist shelf — not the
# video count — the slowest phase for a big channel. 60 covers the overwhelming
# majority (MKBHD has 23, Veritasium 13) and bounds the worst case to about
# twenty seconds. @TED, at over a hundred, gets the truncation notice instead of
# a forty-second wait.
DEFAULT_PLAYLIST_CAP = 60
# The count cap is deliberately loose and the clock is the real ceiling. Tried
# the other way round first — a hard 160-link cap resolved 160 of Veritasium's
# 1,016 unique URLs in 17s. Raising the cap to 400 and letting the 18s budget
# bind resolves ~390 in the same wall time, because most links come back fast
# and only a few slow hosts ever hit the timeout. Same wait, 2.4× the coverage,
# and what doesn't finish is reported as unknown either way.
DEFAULT_LINK_CAP = 400
DEFAULT_LINK_TIME_BUDGET = 18.0
# Higher than the CLI's 12: a public scan is one burst a visitor is actively
# waiting on, not a background sweep from someone's laptop, and link resolution
# is pure I/O wait. Kept well under the platform's 1,024 shared file
# descriptors so several simultaneous scans cannot starve each other.
DEFAULT_LINK_CONCURRENCY = 64

# Playlist membership is what the orphan checks are computed from. If the
# playlist list was cut short, a video whose only playlist we never fetched
# looks like it is in none — so these are switched off rather than allowed to
# report a false positive.
PLAYLIST_DEPENDENT_CHECKS = ("playlist.orphan", "playlist.missing_series")


class PublicScanError(RuntimeError):
    """A failure worth showing a visitor verbatim.

    `kind` lets the transport pick a status code without parsing prose:
    "not_found", "quota", "config" or "upstream".
    """

    def __init__(self, kind: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.hint = hint


@dataclass
class Limit:
    """One ceiling that actually bit. Absent from the result when it did not."""

    key: str
    label: str
    detail: str


@dataclass
class PublicScanResult:
    catalog: Catalog
    findings: list[Finding]
    report: scoring.ScoreReport
    skipped: list[SkippedCheck]
    limits: list[Limit] = field(default_factory=list)
    quota_spent: int = 0
    elapsed_seconds: float = 0.0


_KEY_IN_URL = re.compile(r"([?&]key=)[^&\s\"']+")


def redact(text: str) -> str:
    """Strip credentials out of anything that might reach a visitor.

    googleapiclient puts the full request URL into the string form of an
    HttpError, and for this API that URL carries `key=<the API key>`. Those
    strings flow into error hints and into logs, so a bad request on a public
    endpoint will hand the deployment's API key to whoever typed it. Found
    exactly that way — a malformed key produced a 400 whose message quoted the
    key back over the wire.

    Belt and braces: blank the `key=` parameter by shape, and separately blank
    the configured key by value in case it appears somewhere else.
    """
    out = _KEY_IN_URL.sub(r"\1[redacted]", text or "")
    secret = (CONFIG.api_key or "").strip()
    if len(secret) >= 8:
        out = out.replace(secret, "[redacted]")
    return out


def _is_quota_error(exc: BaseException) -> bool:
    """Did YouTube refuse us because the day's units are gone?

    The Data API answers a blown quota with 403 and reason `quotaExceeded`,
    which is easy to confuse with 403 `forbidden` (a private resource) — so
    match on the reason, not the status.
    """
    text = f"{type(exc).__name__}: {exc}"
    return "quotaExceeded" in text or "dailyLimitExceeded" in text


def public_scan(
    channel_ref: str,
    *,
    video_cap: int = DEFAULT_VIDEO_CAP,
    playlist_cap: int = DEFAULT_PLAYLIST_CAP,
    link_cap: int = DEFAULT_LINK_CAP,
    link_time_budget: float = DEFAULT_LINK_TIME_BUDGET,
    link_concurrency: int = DEFAULT_LINK_CONCURRENCY,
    quota_budget: int = 9000,
    progress: Callable[[str, str, int, int | None], None] | None = None,
) -> PublicScanResult:
    """Audit a public channel. Never authenticates, never writes.

    `progress(phase, stage, done, total)` is called as the scan advances, so a
    caller can stream it. A public scan takes tens of seconds — most of it
    resolving links one at a time — and a blank spinner for that long reads as
    a hang rather than as work.
    """
    started = time.monotonic()

    def tick(phase: str, stage: str, done: int = 0,
             total: int | None = None) -> None:
        if progress:
            progress(phase, stage, done, total)

    if not (channel_ref or "").strip():
        raise PublicScanError(
            "not_found", "Enter a YouTube channel URL or @handle.")

    ledger = QuotaLedger(budget=quota_budget)
    try:
        # owner=False is the whole security story: no OAuth, no token, no
        # write scope. A PUBLIC-mode client physically cannot mutate anything.
        client = YouTubeClient(ledger=ledger, owner=False)
    except YouTubeError as exc:
        raise PublicScanError(
            "config", "This deployment has no YouTube API key configured.",
            hint=redact(str(exc))) from exc

    try:
        catalog = fetch_catalog(
            client, channel_ref,
            limit=video_cap, playlist_limit=playlist_cap,
            with_captions=False, with_analytics=False,
            progress=lambda stage, done, total: tick(
                "fetch", stage, done, total))
    except QuotaExceeded as exc:
        raise PublicScanError(
            "quota",
            "This scan would exceed the deployment's daily YouTube API quota.",
            hint=redact(str(exc))) from exc
    except YouTubeError as exc:
        raise PublicScanError(
            "not_found",
            f"No YouTube channel found for “{channel_ref}”.",
            hint="Try the full channel URL, or the @handle exactly as it "
                 "appears on the channel page.") from exc
    except Exception as exc:  # noqa: BLE001 — classify, then re-raise as ours
        if _is_quota_error(exc):
            raise PublicScanError(
                "quota",
                "YouTube's daily API quota for this deployment is used up. "
                "It resets at midnight Pacific time.",
                hint="Everything still works locally — the CLI uses your own "
                     "API key and your own quota.") from exc
        raise PublicScanError(
            "upstream", "YouTube's API did not answer that request.",
            hint=redact(f"{type(exc).__name__}: {exc}")) from exc

    if not catalog.videos:
        raise PublicScanError(
            "not_found",
            f"“{catalog.channel.title or channel_ref}” has no public videos to "
            f"scan.",
            hint="Pitstop audits published videos. A channel with only Shorts "
                 "playlists, private uploads or no uploads has nothing to "
                 "grade.")

    # --- which checks can honestly run ---------------------------------------
    runnable = [c.id for c in all_checks()]
    forced_skips: list[SkippedCheck] = []

    if catalog.playlists_truncated:
        by_id = {c.id: c for c in all_checks()}
        for check_id in PLAYLIST_DEPENDENT_CHECKS:
            check = by_id.get(check_id)
            if not check:
                continue
            runnable.remove(check_id)
            forced_skips.append(SkippedCheck(
                check_id, check.name,
                f"this channel has more than {playlist_cap} playlists and the "
                f"list was capped, so orphan detection would report false "
                f"positives"))

    ctx = CheckContext(
        has_network=True,
        has_llm=False,          # no LLM key on the public deployment
        rules={},               # never grade a stranger against local pitstop.yaml
        link_max_urls=link_cap,
        link_time_budget=link_time_budget,
        link_concurrency=link_concurrency,
    )

    try:
        findings, skipped = run_all(
            catalog, ctx, only=runnable,
            progress=lambda name, done, total: tick(
                "checks", name, done, total))
    except QuotaExceeded as exc:
        raise PublicScanError(
            "quota", "Ran out of API quota partway through this scan.",
            hint=redact(str(exc))) from exc

    skipped = forced_skips + list(skipped)
    report = scoring.compute(catalog, findings)

    return PublicScanResult(
        catalog=catalog,
        findings=findings,
        report=report,
        skipped=skipped,
        limits=_limits(catalog, ctx, video_cap, playlist_cap),
        quota_spent=client.ledger.spent,
        elapsed_seconds=round(time.monotonic() - started, 1),
    )


def _limits(catalog: Catalog, ctx: CheckContext,
            video_cap: int, playlist_cap: int) -> list[Limit]:
    """Only the ceilings that actually bit. An empty list means a full scan."""
    out: list[Limit] = []

    if catalog.videos_truncated:
        total = catalog.channel.video_count
        out.append(Limit(
            "videos", "Partial catalog",
            f"Scored the {len(catalog.videos)} most recent videos of "
            f"{total:,}. Every number on this page describes that slice, not "
            f"the whole channel."))

    if catalog.playlists_truncated:
        out.append(Limit(
            "playlists", "Playlists capped",
            f"Stopped after {playlist_cap} playlists — each one is a separate paged "
            f"walk and this is the slowest part of a scan. The two orphan checks "
            f"were switched off rather than allowed to report videos as "
            f"playlist-less when we simply never looked."))

    budget = (ctx.cache or {}).get("link_budget") or {}
    if budget.get("unreached"):
        reasons = []
        if budget.get("over_cap"):
            reasons.append(f"{budget['over_cap']} past the "
                           f"{budget['capped_at']}-link ceiling")
        if budget.get("timed_out"):
            reasons.append(f"{budget['timed_out']} still in flight when the "
                           f"{int(ctx.link_time_budget or 0)}s network budget "
                           f"expired")
        out.append(Limit(
            "links", "Some links unresolved",
            f"Actually resolved {budget['resolved']} of "
            f"{budget['unique_urls']} unique links — "
            f"{' and '.join(reasons)}. Those are reported as unknown, never "
            f"as dead. Highest-traffic videos are resolved first, so the "
            f"ceiling costs findings on the videos that matter least."))

    return out


# ---------------------------------------------------------------------------
# serialisation
# ---------------------------------------------------------------------------


def _finding_json(finding: Finding, catalog: Catalog) -> dict:
    video = catalog.video(finding.video_id) if finding.video_id else None
    return {
        "severity": finding.severity.value,
        "detail": finding.detail,
        "video_id": finding.video_id,
        "video_title": video.title if video else None,
        "video_url": (f"https://www.youtube.com/watch?v={finding.video_id}"
                      if finding.video_id else None),
        "impact_views": finding.impact_views,
        "auto_fixable": finding.auto_fixable,
    }


def result_json(result: PublicScanResult, *, max_groups: int = 12,
                max_instances: int = 6) -> dict[str, Any]:
    """The wire format for the web page.

    Findings are grouped by check, because "dead link ×18" is the sentence a
    creator can act on and eighteen separate rows is not. Groups are emitted in
    the engine's own ranked order, so the page cannot reorder the verdict.
    """
    catalog, report = result.catalog, result.report
    ranked = scoring.rank(result.findings, catalog)

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
        if len(bucket["instances"]) < max_instances:
            bucket["instances"].append(_finding_json(finding, catalog))

    descriptions = {c.id: c.description for c in all_checks()}
    for bucket in grouped.values():
        bucket["description"] = descriptions.get(bucket["check_id"], "")

    channel = catalog.channel
    return {
        "channel": {
            **asdict(channel),
            "url": (f"https://www.youtube.com/@{channel.handle}"
                    if channel.handle
                    else f"https://www.youtube.com/channel/{channel.id}"),
        },
        "score": {
            "score": report.score,
            "grade": report.grade,
            "headline": report.headline,
            "total_findings": report.total_findings,
            "critical": report.critical,
            "warning": report.warning,
            "notice": report.notice,
            "auto_fixable": report.auto_fixable,
            "affected_videos": report.affected_videos,
            "categories": [asdict(c) for c in report.categories],
            "priority_videos": report.priority_videos,
        },
        "groups": list(grouped.values())[:max_groups],
        "group_total": len(grouped),
        "limits": [asdict(limit) for limit in result.limits],
        "skipped": [asdict(s) for s in result.skipped],
        "videos_scanned": len(catalog.videos),
        "playlists_scanned": len(catalog.playlists),
        "checks_run": len(all_checks()) - len(result.skipped),
        "checks_total": len(all_checks()),
        "quota_spent": result.quota_spent,
        "elapsed_seconds": result.elapsed_seconds,
    }
