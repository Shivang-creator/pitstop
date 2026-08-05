"""Dead links in descriptions — the money leak.

This is the check that makes people care. A four-year-old video still pulling
400 views/day with a dead affiliate link is losing real revenue every day, and
nobody notices because nobody re-reads their own old descriptions.

Implementation notes worth knowing:

  * Every unique URL is resolved exactly once for the whole catalog and cached
    on the context. Creators paste the same five links into 200 descriptions;
    naively checking per-video would mean ~1,000 requests instead of ~40.
  * HEAD first, GET on 405/501 — a surprising number of hosts (Amazon among
    them) reject HEAD outright and would otherwise look dead.
  * Redirects are followed. A 301 to a live page is fine; a 301 into a
    "product unavailable" page still returns 200 and we cannot tell, so we
    don't pretend to.
  * Shorteners are flagged separately: bit.ly and friends resolving to 404 are
    the single most common form of this, but a *live* shortener is also a risk
    because the destination can change under you.

We are deliberately conservative. A link is only reported dead on an
unambiguous 4xx/5xx or a connection failure that reproduces on retry. Timeouts
alone are reported as "unverified", not dead — telling someone to edit 40
descriptions because their own network hiccuped would be worse than useless.
"""

from __future__ import annotations

import asyncio
from typing import Iterable

import httpx

from ..models import Catalog, Finding, Fix, Severity
from .base import BaseCheck, CheckContext, register

SHORTENERS = {
    "bit.ly", "tinyurl.com", "goo.gl", "t.co", "ow.ly", "buff.ly",
    "amzn.to", "youtu.be", "rebrand.ly", "cutt.ly", "shorturl.at",
}

# Hosts that reject HEAD but answer GET fine.
GET_ONLY_HOSTS = {"amazon.com", "amzn.to", "www.amazon.com"}

TIMEOUT = httpx.Timeout(8.0, connect=5.0)
CONCURRENCY = 12


class LinkStatus:
    ALIVE = "alive"
    DEAD = "dead"
    UNVERIFIED = "unverified"


async def _probe(client: httpx.AsyncClient, url: str,
                 sem: asyncio.Semaphore) -> tuple[str, int | None, str]:
    """Return (status, http_code, final_url)."""
    async with sem:
        host = httpx.URL(url).host or ""
        methods = ["GET"] if any(h in host for h in GET_ONLY_HOSTS) else ["HEAD", "GET"]
        last_exc: Exception | None = None
        for attempt, method in enumerate(methods):
            try:
                resp = await client.request(method, url,
                                            follow_redirects=True,
                                            timeout=TIMEOUT)
                if resp.status_code in (405, 501) and method == "HEAD":
                    continue  # fall through to GET
                if resp.status_code >= 400:
                    return LinkStatus.DEAD, resp.status_code, str(resp.url)
                return LinkStatus.ALIVE, resp.status_code, str(resp.url)
            except (httpx.ConnectError, httpx.ReadError) as exc:
                last_exc = exc
                continue
            except httpx.HTTPError as exc:
                last_exc = exc
                break
        # Connection-level failure. Retry once before calling it dead; a single
        # transient failure is not evidence enough to tell someone to edit 40
        # descriptions.
        try:
            resp = await client.get(url, follow_redirects=True, timeout=TIMEOUT)
            if resp.status_code >= 400:
                return LinkStatus.DEAD, resp.status_code, str(resp.url)
            return LinkStatus.ALIVE, resp.status_code, str(resp.url)
        except httpx.HTTPError:
            if isinstance(last_exc, httpx.ConnectError):
                return LinkStatus.DEAD, None, url
            return LinkStatus.UNVERIFIED, None, url


async def _probe_all(urls: list[str]) -> dict[str, tuple[str, int | None, str]]:
    sem = asyncio.Semaphore(CONCURRENCY)
    headers = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36 Pitstop/0.1"),
    }
    async with httpx.AsyncClient(headers=headers, http2=False) as client:
        results = await asyncio.gather(
            *(_probe(client, u, sem) for u in urls), return_exceptions=True)
    out: dict[str, tuple[str, int | None, str]] = {}
    for url, res in zip(urls, results):
        if isinstance(res, BaseException):
            out[url] = (LinkStatus.UNVERIFIED, None, url)
        else:
            out[url] = res
    return out


def probe_urls(urls: list[str]) -> dict[str, tuple[str, int | None, str]]:
    """Sync entry point. Safe to call from inside an existing loop via a thread."""
    if not urls:
        return {}
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_probe_all(urls))
    # Already inside a loop (FastAPI). Run in a private one on a worker thread.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _probe_all(urls)).result()


@register
class DeadLinkCheck(BaseCheck):
    id = "links.dead"
    name = "Dead links in descriptions"
    description = ("Every URL across the catalog is resolved. 4xx/5xx and "
                   "connection failures are reported with the video's traffic "
                   "so you can see what the breakage is actually costing.")
    requires_network = True

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        unique: dict[str, list] = {}
        for video in catalog.videos:
            for url in video.urls:
                unique.setdefault(url.rstrip(".,);"), []).append(video)

        if not unique:
            return

        cache = ctx.cache.setdefault("link_status", {})
        todo = [u for u in unique if u not in cache]
        if todo:
            cache.update(probe_urls(todo))

        for url, videos in unique.items():
            status, code, final = cache.get(url, (LinkStatus.UNVERIFIED, None, url))
            if status != LinkStatus.DEAD:
                continue
            for video in videos:
                monthly = int(video.views_per_day * 30)
                reason = f"HTTP {code}" if code else "connection refused"
                yield Finding(
                    check_id=self.id,
                    severity=Severity.CRITICAL,
                    title="Dead link in description",
                    detail=f"{url} → {reason}",
                    video_id=video.id,
                    impact_views=monthly,
                    evidence={"url": url, "http_status": code,
                              "final_url": final,
                              "views_per_month": monthly},
                    # We can remove a dead link automatically, but we cannot
                    # invent the correct replacement. So the proposed fix is a
                    # clearly-marked placeholder the creator edits in `plan`,
                    # never a silent deletion of something they wrote.
                    fix=Fix(
                        field="description",
                        current=video.description,
                        proposed=video.description.replace(
                            url, f"[PITSTOP: dead link removed — {url}]"),
                        note=f"flag dead link ({reason})",
                    ),
                )


@register
class ShortenerRiskCheck(BaseCheck):
    id = "links.shortener"
    name = "Links behind a shortener"
    description = ("Shortened links still resolve today but their destination "
                   "can change without warning, and expired ones are the most "
                   "common source of silent affiliate breakage.")

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        for video in catalog.videos:
            hits = [u for u in video.urls
                    if any(s in u for s in SHORTENERS) and "youtu.be" not in u]
            if not hits:
                continue
            yield Finding(
                check_id=self.id,
                severity=Severity.NOTICE,
                title="Link behind a shortener",
                detail=f"{len(hits)} shortened link(s): {', '.join(hits[:3])}",
                video_id=video.id,
                impact_views=int(video.views_per_day * 30),
                evidence={"urls": hits},
            )


@register
class SelfLinkRotCheck(BaseCheck):
    id = "links.self_rot"
    name = "Links to your own dead videos"
    description = ("Descriptions pointing at your own videos that are now "
                   "private or deleted. Viewers hit an error page and leave.")

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        live = {v.id for v in catalog.videos if v.privacy_status == "public"}
        known = {v.id for v in catalog.videos}

        for video in catalog.videos:
            for url in video.urls:
                vid = _extract_video_id(url)
                if not vid or vid not in known or vid in live:
                    continue
                target = catalog.video(vid)
                yield Finding(
                    check_id=self.id,
                    severity=Severity.CRITICAL,
                    title="Link to your own non-public video",
                    detail=(f"links to {vid} "
                            f"({target.privacy_status if target else 'unknown'})"),
                    video_id=video.id,
                    impact_views=int(video.views_per_day * 30),
                    evidence={"target_video_id": vid, "url": url},
                )


def _extract_video_id(url: str) -> str | None:
    import re

    m = re.search(r"(?:youtu\.be/|[?&]v=)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None
