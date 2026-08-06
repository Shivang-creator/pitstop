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

# Words that describe the *shape* of a playlist rather than its subject.
# "Blender Basics" is a playlist about Blender; requiring a video to also
# contain the word "basics" would match nothing. Stripped before deciding
# which words a video must cover.
_PLAYLIST_QUALIFIERS = {
    "basics", "beginner", "beginners", "intro", "introduction", "advanced",
    "series", "tutorial", "tutorials", "guide", "guides", "tips", "tricks",
    "course", "crash", "complete", "full", "playlist", "videos", "video",
    "101", "deep", "dive", "dives", "explained", "masterclass", "essentials",
    "fundamentals", "walkthrough", "walkthroughs", "shorts", "clips",
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
        # Computed once for the whole catalog, not per video.
        generic = _generic_words(catalog)

        for video in catalog.videos:
            if video.id in in_playlists or video.privacy_status != "public":
                continue
            suggested = _suggest_playlist(video, catalog, generic)
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


def _generic_words(catalog: Catalog) -> set[str]:
    """Words so common across this channel that they carry no signal.

    Every Fireship video is tagged `webdev`, `app development`, `tutorial`.
    Those words match almost any playlist title and would make every
    suggestion look plausible while being meaningless. Which words are generic
    is channel-specific — `blender` is generic on a Blender channel and highly
    distinctive on a cooking channel — so it is measured, not hardcoded.
    """
    if not catalog.videos:
        return set()
    document_frequency: Counter[str] = Counter()
    for video in catalog.videos:
        document_frequency.update(
            set(_keywords(video.title)) | set(_keywords(" ".join(video.tags))))
    cutoff = max(2, int(0.25 * len(catalog.videos)))
    return {word for word, count in document_frequency.items()
            if count >= cutoff}


def _suggest_playlist(video, catalog: Catalog,
                      generic: set[str] | None = None) -> tuple[str, str] | None:
    """Pick the existing playlist this video clearly belongs in, or nothing.

    Deliberately dumb and explainable — token matching, not embeddings. A
    creator reviewing 60 proposed playlist additions in `plan` needs to see
    *why* each was suggested at a glance. An opaque similarity score would make
    that review impossible, and the review is the whole safety model.

    The rule is **full coverage of the playlist's distinctive words**. An
    earlier version accepted half coverage, which let the single generic word
    "development" put a video titled "The safest way to store Bitcoin was just
    hacked" into a playlist called "Backend Development". Since playlist adds
    are auto-fixable, that would have really moved it.

    Suggesting nothing is a perfectly good answer. The finding still reports
    the video as orphaned; it just doesn't pretend to know where it goes.
    """
    if not catalog.playlists:
        return None

    generic = generic if generic is not None else _generic_words(catalog)
    video_words = set(_keywords(video.title)) | set(_keywords(
        " ".join(video.tags)))
    if not video_words:
        return None

    best: tuple[int, str, str] | None = None
    for playlist in catalog.playlists:
        pl_words = set(_keywords(playlist.title))
        distinctive = pl_words - generic - _PLAYLIST_QUALIFIERS
        if not distinctive:
            # A playlist named only with generic words ("Tutorials", "Videos")
            # can never be matched confidently — it would become a dumping
            # ground for the whole catalog.
            continue
        if not distinctive <= video_words:
            continue
        # Prefer the most specific playlist that fully matches.
        if best is None or len(distinctive) > best[0]:
            best = (len(distinctive), playlist.id, playlist.title)

    return (best[1], best[2]) if best else None


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
