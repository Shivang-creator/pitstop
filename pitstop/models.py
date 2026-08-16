"""Domain types shared by every layer.

The whole engine is a pipeline over these:

    Catalog  --checks-->  [Finding]  --planner-->  Plan([Change])  --applier-->  Result

A Check never mutates anything and never talks to the write API. It reads a
Catalog and emits Findings. A Finding optionally carries a `fix`, which is the
*proposed* new value for one field of one video. The planner collects those into
a Plan; only the applier is allowed to call a write endpoint. That separation is
what makes `scan` and `plan` safe to run against anyone's channel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal


class Severity(str, Enum):
    """How much a finding is costing the channel.

    Ordered. `Severity.CRITICAL > Severity.WARNING` works via `.weight`.
    """

    CRITICAL = "critical"   # actively losing money or breaking for viewers
    WARNING = "warning"     # measurable discovery/retention cost
    NOTICE = "notice"       # hygiene; worth fixing in bulk, not urgent

    @property
    def weight(self) -> int:
        return {"critical": 10, "warning": 4, "notice": 1}[self.value]


# Which fields Pitstop is allowed to write. Anything not in here cannot be
# auto-fixed — see ARCHITECTURE.md "What the API will not let us touch".
FixField = Literal[
    "title",
    "description",
    "tags",
    "categoryId",
    "privacyStatus",
    "publishAt",
    "thumbnail",
    "playlist_add",
    "playlist_remove",
    "captions",
]


@dataclass(frozen=True)
class Fix:
    """A concrete, machine-applicable change to one field of one video."""

    field: FixField
    current: Any
    proposed: Any
    # Free-text note shown in the diff, e.g. "9 chapters generated from transcript"
    note: str = ""

    @property
    def is_noop(self) -> bool:
        return self.current == self.proposed


@dataclass
class Finding:
    """One problem, on one video (or channel-wide when video_id is None)."""

    check_id: str
    severity: Severity
    title: str                     # short, human: "Dead link in description"
    detail: str                    # the specific instance: which URL, which line
    video_id: str | None = None
    fix: Fix | None = None
    # Populated by score.py — estimated monthly views touching this finding.
    # Used to rank, so the report leads with what actually matters.
    impact_views: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def auto_fixable(self) -> bool:
        return self.fix is not None and not self.fix.is_noop


@dataclass
class Video:
    id: str
    title: str
    description: str
    published_at: datetime
    tags: list[str] = field(default_factory=list)
    category_id: str = ""
    duration_seconds: int = 0
    privacy_status: str = "public"
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    thumbnail_url: str = ""
    has_custom_thumbnail: bool | None = None   # None = unknown (public scan)
    caption_available: bool | None = None
    # Owner-only, from the Analytics API. None when we only did a public scan.
    average_view_percentage: float | None = None
    average_view_duration: float | None = None

    # --- derived helpers used by several checks -----------------------------

    _CHAPTER_RE = re.compile(r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s+\S", re.M)

    @property
    def chapter_timestamps(self) -> list[str]:
        return self._CHAPTER_RE.findall(self.description)

    @property
    def has_valid_chapters(self) -> bool:
        """YouTube requires >=3 chapters, first one at 00:00, each >=10s apart.

        We only enforce the two rules we can check from text alone.
        """
        stamps = self.chapter_timestamps
        if len(stamps) < 3:
            return False
        return _to_seconds(stamps[0]) == 0

    @property
    def urls(self) -> list[str]:
        return _URL_RE.findall(self.description)

    @property
    def age_days(self) -> int:
        now = datetime.now(tz=self.published_at.tzinfo)
        return max(0, (now - self.published_at).days)

    @property
    def views_per_day(self) -> float:
        return self.view_count / max(1, self.age_days)


@dataclass
class Playlist:
    id: str
    title: str
    item_video_ids: list[str] = field(default_factory=list)
    # Video ids present in the playlist that no longer resolve (deleted/private).
    # These break the playlist silently and nobody ever notices.
    broken_video_ids: list[str] = field(default_factory=list)


@dataclass
class Channel:
    id: str
    title: str
    handle: str = ""
    subscriber_count: int = 0
    video_count: int = 0
    view_count: int = 0
    thumbnail_url: str = ""


@dataclass
class Catalog:
    """Everything Pitstop knows about a channel at scan time."""

    channel: Channel
    videos: list[Video] = field(default_factory=list)
    playlists: list[Playlist] = field(default_factory=list)
    # True when fetched via OAuth as the owner — unlocks tags, captions,
    # analytics, and makes `apply` possible.
    is_owner: bool = False
    fetched_at: datetime | None = None
    # True when a caller-supplied cap stopped the fetch short. `videos` being
    # incomplete only means the scan saw less; `playlists` being incomplete
    # actively breaks the orphan checks, since a video whose only playlist was
    # never fetched looks like it belongs to none.
    videos_truncated: bool = False
    playlists_truncated: bool = False

    def video(self, video_id: str) -> Video | None:
        return next((v for v in self.videos if v.id == video_id), None)

    @property
    def videos_in_playlists(self) -> set[str]:
        return {vid for p in self.playlists for vid in p.item_video_ids}

    @property
    def median_views(self) -> float:
        if not self.videos:
            return 0.0
        s = sorted(v.view_count for v in self.videos)
        mid = len(s) // 2
        return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


@dataclass
class Change:
    """One write operation the applier will perform. Grouped per video."""

    video_id: str
    video_title: str
    field: FixField
    current: Any
    proposed: Any
    note: str
    check_id: str
    quota_cost: int


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    skipped: list[Finding] = field(default_factory=list)   # not auto-fixable

    @property
    def quota_cost(self) -> int:
        """Cost of the whole plan.

        videos.update is billed per *call*, not per field, so several field
        changes on one video collapse into one 50-unit charge. Playlist inserts
        are billed separately per item.
        """
        per_video_update = 50
        video_updates = {
            c.video_id for c in self.changes
            if c.field in {"title", "description", "tags", "categoryId",
                           "privacyStatus", "publishAt"}
        }
        cost = len(video_updates) * per_video_update
        cost += sum(c.quota_cost for c in self.changes
                    if c.field in {"thumbnail", "playlist_add",
                                   "playlist_remove", "captions"})
        return cost

    @property
    def affected_videos(self) -> int:
        return len({c.video_id for c in self.changes})


@dataclass
class ApplyResult:
    applied: list[Change] = field(default_factory=list)
    failed: list[tuple[Change, str]] = field(default_factory=list)
    quota_spent: int = 0
    stopped_early: str | None = None   # reason, if we hit the budget mid-run


# --- module-level helpers ---------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+")


def _to_seconds(stamp: str) -> int:
    parts = [int(p) for p in stamp.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]
