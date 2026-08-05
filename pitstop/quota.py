"""Quota accounting.

The YouTube Data API bills in abstract "units" against a 10,000/day pool, and
the costs are wildly uneven — reading a video costs 1, writing one costs 50.
That single fact shapes the whole product:

    10,000 units/day  ÷  50 units per videos.update  =  200 metadata edits/day

So a 300-video repair genuinely does not fit in one day. Pitstop refuses to
discover that halfway through a run. `plan` prices the work up front, and the
applier stops cleanly at the budget with a resumable remainder rather than
dying on a 403 with half the channel modified.

Costs verified against developers.google.com/youtube/v3/determine_quota_cost.
`search.list` and `videos.insert` are additionally capped at 100 *calls*/day
each, on top of the shared 10,000-unit pool — which is exactly why the catalog
fetcher walks the uploads playlist instead of paging `search.list`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# method -> units per call
COSTS: dict[str, int] = {
    "channels.list": 1,
    "playlists.list": 1,
    "playlistItems.list": 1,
    "videos.list": 1,
    "captions.list": 50,
    "commentThreads.list": 1,
    "search.list": 100,          # also capped at 100 calls/day — avoid
    "videos.update": 50,
    "videos.insert": 1600,       # also capped at 100 calls/day
    "thumbnails.set": 50,
    "playlistItems.insert": 50,
    "playlistItems.delete": 50,
    "captions.insert": 400,
    "playlists.insert": 50,
}

DAILY_UNITS = 10_000


class QuotaExceeded(RuntimeError):
    def __init__(self, spent: int, budget: int, method: str) -> None:
        super().__init__(
            f"Quota budget exhausted: {spent}/{budget} units spent, "
            f"next call ({method}, {COSTS.get(method, '?')} units) would exceed it."
        )
        self.spent = spent
        self.budget = budget
        self.method = method


@dataclass
class QuotaLedger:
    """Tracks spend and refuses to go over budget.

    Deliberately a hard stop rather than a warning. A partially-applied plan is
    recoverable (re-run `plan`, it re-diffs against live state and only proposes
    what's still outstanding). A 403 mid-write with no accounting is not.
    """

    budget: int = DAILY_UNITS
    spent: int = 0
    by_method: dict[str, int] = field(default_factory=dict)

    def cost(self, method: str) -> int:
        return COSTS.get(method, 1)

    def can_afford(self, method: str, times: int = 1) -> bool:
        return self.spent + self.cost(method) * times <= self.budget

    def charge(self, method: str, times: int = 1) -> int:
        cost = self.cost(method) * times
        if self.spent + cost > self.budget:
            raise QuotaExceeded(self.spent, self.budget, method)
        self.spent += cost
        self.by_method[method] = self.by_method.get(method, 0) + cost
        return cost

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.spent)

    @property
    def pct_used(self) -> float:
        return 100.0 * self.spent / self.budget if self.budget else 0.0


def estimate_fetch_cost(video_count: int, playlist_count: int = 0) -> int:
    """What a full catalog scan will cost, before we run it.

    videos.list and playlistItems.list both page at 50 items for 1 unit each,
    which is why scanning a 500-video channel costs ~25 units and not 500.
    """
    pages = lambda n: max(1, -(-n // 50))  # noqa: E731 — ceil div
    return (
        COSTS["channels.list"]
        + COSTS["playlistItems.list"] * pages(video_count)   # uploads playlist
        + COSTS["videos.list"] * pages(video_count)          # hydrate details
        + COSTS["playlists.list"] * pages(playlist_count)
        + COSTS["playlistItems.list"] * playlist_count
    )
