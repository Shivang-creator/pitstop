"""What working channels actually do — measured, not guessed.

The problem this solves is the one a new creator actually has. It is not "what
should I make a video about" — they usually have ideas. It is **"I don't know
what good looks like, and I'm about to make the same ten mistakes everyone
makes."**

Pitstop already encodes what good looks like, in 19 checks. So instead of
asking a language model to opine about best practices, this points the same
checks at real videos that are demonstrably working right now, and reports what
those videos do:

    87% of trending videos in this category have chapters. You have none.
    Median description: 1,240 characters. Yours: 180.
    Median tag count: 14. Yours: 0.

That is a data-backed playbook derived from real trending data, and it costs
one quota unit. `videos.list(chart="mostPopular")` is the cheapest useful
endpoint YouTube exposes — 1 unit for 50 videos, versus `search.list` at 100
units per call *and* a separate 100-calls/day ceiling.

The same machinery serves established creators as `benchmark`: compare a
channel's practices against specific competitors instead of against the
trending chart.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from .models import Catalog, Channel, Video
from .youtube import YouTubeClient

# YouTube's assignable video categories. Only the ones creators actually pick.
CATEGORIES: dict[str, str] = {
    "1": "Film & Animation",
    "2": "Autos & Vehicles",
    "10": "Music",
    "15": "Pets & Animals",
    "17": "Sports",
    "19": "Travel & Events",
    "20": "Gaming",
    "22": "People & Blogs",
    "23": "Comedy",
    "24": "Entertainment",
    "25": "News & Politics",
    "26": "Howto & Style",
    "27": "Education",
    "28": "Science & Technology",
}

CATEGORY_ALIASES = {
    "tech": "28", "technology": "28", "science": "28", "coding": "28",
    "programming": "28", "software": "28",
    "education": "27", "learning": "27", "tutorial": "27",
    "gaming": "20", "games": "20",
    "howto": "26", "diy": "26", "style": "26", "beauty": "26",
    "music": "10", "comedy": "23", "entertainment": "24",
    "news": "25", "sports": "17", "travel": "19", "film": "1",
    "vlog": "22", "blogs": "22", "autos": "2", "cars": "2", "pets": "15",
}


def resolve_category(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lower()
    if value in CATEGORIES:
        return value
    if value in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[value]
    for cid, name in CATEGORIES.items():
        if value in name.lower():
            return cid
    return None


@dataclass
class Practice:
    """One measurable habit, and how a reference set compares to you."""

    key: str
    label: str
    unit: str
    reference: float           # what the reference set does (median or %)
    yours: float | None        # None when the user has no channel yet
    higher_is_better: bool = True
    detail: str = ""

    @property
    def gap(self) -> float | None:
        if self.yours is None or self.reference == 0:
            return None
        return (self.yours - self.reference) / self.reference

    @property
    def verdict(self) -> str:
        if self.yours is None:
            return "reference"
        if self.reference == 0:
            return "ok"
        ratio = self.yours / self.reference if self.reference else 1.0
        if not self.higher_is_better:
            ratio = 1 / ratio if ratio else 2.0
        if ratio >= 0.9:
            return "ok"
        if ratio >= 0.5:
            return "behind"
        return "far behind"


@dataclass
class BenchmarkReport:
    source: str                       # "trending · Science & Technology (IN)"
    sample_size: int
    practices: list[Practice] = field(default_factory=list)
    examples: list[dict[str, Any]] = field(default_factory=list)
    topics: list[tuple[str, int]] = field(default_factory=list)
    your_channel: str | None = None
    shorts_excluded: int = 0
    caveat: str = ""

    @property
    def behind(self) -> list[Practice]:
        return [p for p in self.practices if p.verdict in {"behind", "far behind"}]


# ---------------------------------------------------------------------------
# measuring
# ---------------------------------------------------------------------------


# Shorts are a different format with different conventions — no chapters, no
# long descriptions, different pacing. Leaving them in the reference set makes
# every long-form creator look "far behind" on habits that do not apply to the
# videos they make. YouTube's trending chart is heavily Shorts in most regions,
# so this filter is the difference between a useful benchmark and a misleading
# one.
SHORTS_MAX_SECONDS = 90


def split_shorts(videos: list[Video]) -> tuple[list[Video], list[Video]]:
    """(long_form, shorts). Duration is the only signal the API gives us."""
    long_form, shorts = [], []
    for video in videos:
        # Duration 0 means unknown, not short — don't discard on missing data.
        if 0 < video.duration_seconds <= SHORTS_MAX_SECONDS:
            shorts.append(video)
        else:
            long_form.append(video)
    return long_form, shorts


def _pct(videos: list[Video], predicate) -> float:
    if not videos:
        return 0.0
    return 100.0 * sum(1 for v in videos if predicate(v)) / len(videos)


def _median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def measure(videos: list[Video]) -> dict[str, float]:
    """The habits we can observe from public metadata alone."""
    if not videos:
        return {}
    return {
        "chapters_pct": _pct(videos, lambda v: v.has_valid_chapters),
        "description_chars": _median([len(v.description) for v in videos]),
        "title_chars": _median([len(v.title) for v in videos]),
        "tag_count": _median([len(v.tags) for v in videos]),
        "links_per_desc": _median([len(v.urls) for v in videos]),
        "captions_pct": _pct(videos, lambda v: v.caption_available is True),
        "duration_seconds": _median([v.duration_seconds for v in videos
                                     if v.duration_seconds]),
    }


PRACTICE_SPECS: list[tuple[str, str, str, bool]] = [
    ("chapters_pct", "Videos with working chapters", "%", True),
    ("description_chars", "Description length", "chars", True),
    ("tag_count", "Tags per video", "tags", True),
    ("links_per_desc", "Links in description", "links", True),
    ("captions_pct", "Videos with an uploaded caption track", "%", True),
    ("title_chars", "Title length", "chars", True),
    ("duration_seconds", "Video length", "sec", True),
]


def compare(reference: list[Video], yours: list[Video] | None,
            *, source: str, your_channel: str | None = None,
            examples: list[Video] | None = None,
            include_shorts: bool = False) -> BenchmarkReport:
    raw_reference = reference
    shorts_excluded = 0
    caveat = ""

    if not include_shorts:
        long_form, shorts = split_shorts(reference)
        shorts_excluded = len(shorts)
        if long_form:
            reference = long_form
        elif shorts:
            # Everything trending in this niche is a Short. Say so rather than
            # benchmarking long-form habits against a set where none apply.
            caveat = (f"Every one of the {len(shorts)} trending videos here is "
                      f"a Short. Long-form habits like chapters don't apply, "
                      f"so treat these numbers as format context, not targets.")

        # Compare like with like: if the creator makes long-form, judge them
        # against long-form.
        if yours:
            your_long, _ = split_shorts(yours)
            if your_long:
                yours = your_long

    ref_stats = measure(reference)
    your_stats = measure(yours) if yours else {}

    practices = []
    for key, label, unit, higher_better in PRACTICE_SPECS:
        if key not in ref_stats:
            continue
        # Title and duration are stylistic, not better-when-bigger. Report the
        # reference value without scoring the creator against it — telling
        # someone their 6-minute video is "behind" a 22-minute one would be
        # advice, not measurement.
        neutral = key in {"title_chars", "duration_seconds"}
        practices.append(Practice(
            key=key, label=label, unit=unit,
            reference=round(ref_stats[key], 1),
            yours=(round(your_stats[key], 1)
                   if your_stats and key in your_stats and not neutral
                   else None),
            higher_is_better=higher_better,
        ))

    return BenchmarkReport(
        source=source,
        sample_size=len(reference),
        practices=practices,
        examples=[{
            "title": v.title,
            "video_id": v.id,
            "url": f"https://www.youtube.com/watch?v={v.id}",
            "views": v.view_count,
            "has_chapters": v.has_valid_chapters,
            "description_chars": len(v.description),
            "thumbnail_url": v.thumbnail_url,
        } for v in (examples or reference)[:10]],
        # Topics come from the *unfiltered* set — what people are talking about
        # is interesting regardless of format, even when the habits aren't.
        topics=extract_topics(raw_reference),
        your_channel=your_channel,
        shorts_excluded=shorts_excluded,
        caveat=caveat,
    )


# ---------------------------------------------------------------------------
# topics
# ---------------------------------------------------------------------------

_TOPIC_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "is",
    "it", "this", "that", "i", "we", "you", "at", "by", "from", "my", "your",
    "how", "what", "why", "new", "best", "top", "video", "vs", "was", "are",
    "be", "not", "but", "all", "can", "just", "do", "does", "did", "get",
    "got", "has", "have", "he", "she", "they", "his", "her", "them", "who",
    "will", "one", "two", "out", "up", "so", "if", "no", "yes", "now", "then",
    "made", "make", "makes", "into", "about", "after", "before", "more",
    "most", "than", "when", "which", "there", "their", "our", "us", "am", "pm",
}


def extract_topics(videos: list[Video], limit: int = 15) -> list[tuple[str, int]]:
    """What these videos are actually about, by title-word frequency.

    Deliberately not an LLM call. The value here is "these exact words appear
    in N trending titles right now" — a verifiable count. A model's summary of
    the same thing would be prettier and unfalsifiable.
    """
    import re
    from collections import Counter

    counter: Counter[str] = Counter()
    for video in videos:
        words = re.sub(r"[^a-z0-9\s]", " ", video.title.lower()).split()
        counter.update({w for w in words
                        if w not in _TOPIC_STOPWORDS and len(w) > 2})
    return [(word, count) for word, count in counter.most_common(limit)
            if count > 1]


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------


def fetch_trending(client: YouTubeClient, *, region: str = "US",
                   category: str | None = None,
                   limit: int = 50) -> tuple[list[Video], str]:
    """Real trending videos. One quota unit for up to 50.

    `chart="mostPopular"` is the cheapest useful endpoint on the API and it
    returns full snippets, so every check can run against the result without a
    second call.
    """
    category_id = resolve_category(category)
    videos = client.most_popular(region=region, category_id=category_id,
                                 limit=limit)
    label = CATEGORIES.get(category_id or "", "All categories")
    return videos, f"trending · {label} · {region.upper()}"


def fetch_channels(client: YouTubeClient, refs: list[str],
                   *, per_channel: int = 30) -> tuple[list[Video], list[Channel]]:
    """Recent videos from specific channels, for competitor benchmarking."""
    from .catalog import fetch_catalog

    videos: list[Video] = []
    channels: list[Channel] = []
    for ref in refs:
        catalog = fetch_catalog(client, ref, limit=per_channel)
        videos.extend(catalog.videos)
        channels.append(catalog.channel)
    return videos, channels


def gap_topics(reference: list[Video],
               yours: Catalog | None) -> list[tuple[str, int]]:
    """Trending topics the creator has never covered.

    This is the honest version of "give me video ideas": every suggestion is a
    word that is demonstrably trending right now and demonstrably absent from
    the creator's catalog. No invention — it is a set difference over real
    data, and the creator can verify both halves.
    """
    trending = dict(extract_topics(reference, limit=40))
    if not yours or not yours.videos:
        return sorted(trending.items(), key=lambda kv: -kv[1])[:12]

    import re

    covered: set[str] = set()
    for video in yours.videos:
        text = f"{video.title} {video.description[:400]}".lower()
        covered.update(re.sub(r"[^a-z0-9\s]", " ", text).split())

    gaps = [(word, count) for word, count in trending.items()
            if word not in covered]
    return sorted(gaps, key=lambda kv: -kv[1])[:12]
