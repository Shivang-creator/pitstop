"""Execute a Plan against the real channel.

This is the only module in the codebase allowed to call a write endpoint, and
it is deliberately boring. Every interesting decision was already made and
reviewed during `plan`.

Guarantees:

  * **Grouped writes.** All snippet-field changes for one video collapse into a
    single videos.update. Three field edits on one video cost 50 units, not 150.
  * **Read-modify-write.** videos.update replaces the entire snippet part, so
    omitting a field you didn't mean to touch *erases* it. The client re-reads
    the live snippet immediately before writing. This is the single most
    common way to destroy a channel with this endpoint and it is handled in
    exactly one place.
  * **Hard budget stop.** The ledger refuses the call that would exceed budget.
    We stop cleanly with a resumable remainder rather than discovering the
    limit as a 403 halfway through.
  * **Per-change isolation.** One failing video does not abort the run. Failures
    are collected and reported; the rest of the plan still lands.
  * **Idempotent by construction.** Re-running `plan` after a partial apply
    re-diffs against live state, so an interrupted run resumes rather than
    double-applying.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from .models import ApplyResult, Change, Plan
from .planner import SNIPPET_FIELDS, STATUS_FIELDS
from .quota import QuotaExceeded
from .youtube import YouTubeClient

ProgressFn = Callable[[str, int, int], None]

# Change.field -> the key the API actually wants in the snippet body.
_SNIPPET_KEY = {
    "title": "title",
    "description": "description",
    "tags": "tags",
    "categoryId": "categoryId",
}


def apply_plan(
    client: YouTubeClient,
    plan: Plan,
    *,
    dry_run: bool = False,
    progress: ProgressFn | None = None,
) -> ApplyResult:
    result = ApplyResult()

    # Group by video so each video costs one videos.update regardless of how
    # many fields changed.
    by_video: dict[str, list[Change]] = defaultdict(list)
    playlist_ops: list[Change] = []

    for change in plan.changes:
        if change.field in {"playlist_add", "playlist_remove"}:
            playlist_ops.append(change)
        else:
            by_video[change.video_id].append(change)

    total = len(by_video) + len(playlist_ops)
    done = 0

    # --- metadata updates ---------------------------------------------------
    for video_id, changes in by_video.items():
        title = changes[0].video_title
        if progress:
            progress(f"Updating {title[:48]}", done, total)

        snippet_updates = {
            _SNIPPET_KEY[c.field]: c.proposed
            for c in changes if c.field in SNIPPET_FIELDS
        }
        status_updates = {
            c.field: c.proposed for c in changes if c.field in STATUS_FIELDS
        }

        if dry_run:
            result.applied.extend(changes)
            done += 1
            continue

        try:
            client.update_video(video_id, snippet_updates,
                                status_updates or None)
            result.applied.extend(changes)
        except QuotaExceeded as exc:
            result.stopped_early = str(exc)
            result.quota_spent = client.ledger.spent
            if progress:
                progress("Stopped: quota budget reached", done, total)
            return result
        except Exception as exc:
            for change in changes:
                result.failed.append((change, str(exc)))
        done += 1

    # --- playlist operations ------------------------------------------------
    for change in playlist_ops:
        if progress:
            progress(f"Playlist: {change.video_title[:40]}", done, total)
        if dry_run:
            result.applied.append(change)
            done += 1
            continue
        try:
            if change.field == "playlist_add":
                client.add_to_playlist(str(change.proposed), change.video_id)
            result.applied.append(change)
        except QuotaExceeded as exc:
            result.stopped_early = str(exc)
            result.quota_spent = client.ledger.spent
            return result
        except Exception as exc:
            result.failed.append((change, str(exc)))
        done += 1

    if progress:
        progress("Done", total, total)
    result.quota_spent = client.ledger.spent
    return result
