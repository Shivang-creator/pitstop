"""Playlist checks — the biggest cheap win on most channels.

Playlists are what turn one view into a session. A video in no playlist is a
dead end: the viewer finishes and YouTube has nothing of yours to autoplay.
On most channels this is the single most common finding *and* the one with the
highest ratio of impact to effort, because adding a video to a playlist costs
50 quota units and zero creative work.

The second check here catches something genuinely invisible: a playlist that
still contains videos which have since gone private or been deleted. YouTube
does not remove them and does not tell you. The playlist just quietly gets
shorter for viewers while still showing the old count to you.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from ..models import Catalog, Finding, Fix, Severity
from .base import BaseCheck, CheckContext, register

_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with",
    "my", "your", "how", "what", "why", "is", "it", "this", "that", "i",
    "we", "you", "at", "by", "from", "part", "ep", "episode", "vs",
}


@register
class OrphanVideoCheck(BaseCheck):
    id = "playlist.orphan"
    name = "Videos in no playlist"
    description = ("A video in no playlist is a dead end — nothing of yours "
                   "autoplays after it. Adding it to a playlist is the "
                   "cheapest watch-time win available.")

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        if not catalog.videos:
            return
        in_playlists = catalog.videos_in_playlists

        for video in catalog.videos:
            if video.id in in_playlists or video.privacy_status != "public":
                continue
            suggested = _suggest_playlist(video, catalog)
            fix = None
            if suggested:
                fix = Fix(field="playlist_add", current=None,
                          proposed=suggested[0],
                          note=f'add to "{suggested[1]}"')
            yield Finding(
                check_id=self.id,
                severity=Severity.WARNING,
                title="Video is in no playlist",
                detail=(f'suggested: "{suggested[1]}"' if suggested
                        else "no existing playlist is a clear match"),
                video_id=video.id,
                impact_views=int(video.views_per_day * 30),
                evidence={"suggested_playlist_id": suggested[0] if suggested else None,
                          "suggested_playlist_title": suggested[1] if suggested else None},
                fix=fix,
            )


@register
class BrokenPlaylistCheck(BaseCheck):
    id = "playlist.broken_items"
    name = "Playlists containing dead videos"
    description = ("Playlists silently keep videos that have gone private or "
                   "been deleted. Viewers see a shorter playlist than you do.")
    requires_owner = True

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        for playlist in catalog.playlists:
            if not playlist.broken_video_ids:
                continue
            yield Finding(
                check_id=self.id,
                severity=Severity.WARNING,
                title="Playlist contains private/deleted videos",
                detail=(f'"{playlist.title}" has '
                        f"{len(playlist.broken_video_ids)} dead item(s) of "
                        f"{len(playlist.item_video_ids)}"),
                video_id=None,
                impact_views=0,
                evidence={"playlist_id": playlist.id,
                          "playlist_title": playlist.title,
                          "broken": playlist.broken_video_ids},
            )


@register
class EmptySeriesCheck(BaseCheck):
    id = "playlist.missing_series"
    name = "Obvious series with no playlist"
    description = ("Several videos share a clear title prefix but no playlist "
                   "groups them — a series viewers cannot binge.")

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        if len(catalog.videos) < 4:
            return
        existing = {_normalise(p.title) for p in catalog.playlists}
        clusters = _cluster_by_keyword(catalog)

        for keyword, videos in clusters.items():
            if len(videos) < 3 or _normalise(keyword) in existing:
                continue
            uncovered = [v for v in videos
                         if v.id not in catalog.videos_in_playlists]
            if len(uncovered) < 3:
                continue
            yield Finding(
                check_id=self.id,
                severity=Severity.NOTICE,
                title="Series with no playlist",
                detail=(f'{len(videos)} videos share "{keyword}" but there is '
                        f"no playlist for them"),
                video_id=None,
                impact_views=sum(int(v.views_per_day * 30) for v in videos),
                evidence={"keyword": keyword,
                          "video_ids": [v.id for v in videos]},
            )


# --- helpers ---------------------------------------------------------------


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _keywords(text: str) -> list[str]:
    words = _normalise(text).split()
    return [w for w in words if w not in _STOPWORDS and len(w) > 2]


def _suggest_playlist(video, catalog: Catalog) -> tuple[str, str] | None:
    """Pick the existing playlist whose title best overlaps this video's.

    Deliberately dumb and explainable — token overlap, not embeddings. A
    creator reviewing 60 proposed playlist additions in `plan` needs to be able
    to see *why* each one was suggested at a glance. A similarity score from an
    opaque model would make that review impossible, and the review is the whole
    safety model.
    """
    if not catalog.playlists:
        return None
    video_words = set(_keywords(video.title)) | set(_keywords(
        " ".join(video.tags)))
    if not video_words:
        return None

    best: tuple[float, str, str] | None = None
    for playlist in catalog.playlists:
        pl_words = set(_keywords(playlist.title))
        if not pl_words:
            continue
        overlap = len(video_words & pl_words) / len(pl_words)
        if overlap > 0 and (best is None or overlap > best[0]):
            best = (overlap, playlist.id, playlist.title)

    # Require a real match. Half the playlist's title words must appear, so
    # "Tutorials" does not become a dumping ground for everything.
    if best and best[0] >= 0.5:
        return best[1], best[2]
    return None


def _cluster_by_keyword(catalog: Catalog) -> dict[str, list]:
    counter: Counter[str] = Counter()
    for video in catalog.videos:
        counter.update(set(_keywords(video.title)))

    clusters: dict[str, list] = {}
    for word, count in counter.items():
        if count < 3:
            continue
        clusters[word] = [v for v in catalog.videos
                          if word in set(_keywords(v.title))]
    # Keep only the most specific clusters — if "python" and "tutorial" both
    # match the same set, one entry is enough.
    seen: set[frozenset[str]] = set()
    deduped: dict[str, list] = {}
    for word, videos in sorted(clusters.items(),
                               key=lambda kv: -len(kv[1])):
        key = frozenset(v.id for v in videos)
        if key in seen:
            continue
        seen.add(key)
        deduped[word] = videos
    return deduped
