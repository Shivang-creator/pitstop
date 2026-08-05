"""Turn findings into a reviewable, priced Plan.

The hard part here is not producing changes — it's producing *one coherent*
change per field when several checks independently want to edit the same field
on the same video.

Concretely: a single video can trip the dead-link check, the missing-footer
check, and the broken-chapters check. All three propose a new `description`,
and each one computed its proposal from the *original* text. Applying them
naively means last-writer-wins and two of the three repairs silently vanish —
the worst possible failure mode, because `plan` would have shown all three.

So fixes on the same field are *composed*: each one's edit is re-derived as a
transformation and replayed against the running value. The three shapes our
checks actually emit are append, prepend, and substring-replace, and each of
those is unambiguous to re-apply. Anything that doesn't reduce to one of them
is reported as a conflict and dropped from the plan rather than guessed at —
a change the creator didn't review is a change Pitstop doesn't make.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Catalog, Change, Finding, Fix, Plan
from .quota import COSTS

# Fields that ride along on a single videos.update call.
SNIPPET_FIELDS = {"title", "description", "tags", "categoryId"}
STATUS_FIELDS = {"privacyStatus", "publishAt"}


@dataclass
class Conflict:
    video_id: str
    field: str
    kept: str      # check_id whose fix survived
    dropped: str   # check_id whose fix could not be composed
    reason: str


def _compose_text(accumulated: str, fix: Fix) -> tuple[str | None, str]:
    """Replay `fix`'s edit against `accumulated`.

    Returns (new_value, reason_if_failed).
    """
    original, proposed = fix.current, fix.proposed
    if not isinstance(original, str) or not isinstance(proposed, str):
        return None, "non-text fix on a text field"

    if original == accumulated:
        return proposed, ""

    # Pure append: proposal is the original plus a suffix.
    if proposed.startswith(original):
        return accumulated + proposed[len(original):], ""

    # Pure prepend: proposal is a prefix plus the original.
    if proposed.endswith(original):
        return proposed[:len(proposed) - len(original)] + accumulated, ""

    # Substring replacement: find the single changed span and replay it.
    head = _common_prefix_len(original, proposed)
    tail = _common_suffix_len(original[head:], proposed[head:])
    removed = original[head:len(original) - tail]
    inserted = proposed[head:len(proposed) - tail]
    if removed and removed in accumulated:
        return accumulated.replace(removed, inserted, 1), ""

    return None, "edit region no longer present after an earlier fix"


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _common_suffix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[len(a) - 1 - i] == b[len(b) - 1 - i]:
        i += 1
    return i


def build_plan(catalog: Catalog, findings: list[Finding],
               *, only_checks: list[str] | None = None,
               only_videos: list[str] | None = None,
               ) -> tuple[Plan, list[Conflict]]:
    """Collapse findings into the minimum set of write operations."""
    plan = Plan()
    conflicts: list[Conflict] = []

    fixable = [f for f in findings if f.auto_fixable and f.video_id]
    if only_checks:
        fixable = [f for f in fixable if f.check_id in only_checks]
    if only_videos:
        fixable = [f for f in fixable if f.video_id in only_videos]

    plan.skipped = [f for f in findings if not f.auto_fixable]

    # (video_id, field) -> list of findings, in a deterministic order so two
    # runs of `plan` on unchanged input produce byte-identical output. That
    # property is what makes the diff reviewable and the plan diffable in CI.
    grouped: dict[tuple[str, str], list[Finding]] = {}
    for finding in sorted(fixable, key=lambda f: (f.video_id or "",
                                                  f.fix.field,  # type: ignore[union-attr]
                                                  f.check_id)):
        assert finding.fix is not None
        grouped.setdefault((finding.video_id, finding.fix.field), []).append(
            finding)

    for (video_id, field), group in grouped.items():
        video = catalog.video(video_id)
        if not video:
            continue

        if field in {"playlist_add", "playlist_remove"}:
            # Not a merge — each playlist op is its own API call.
            for finding in group:
                assert finding.fix is not None
                plan.changes.append(Change(
                    video_id=video_id,
                    video_title=video.title,
                    field=field,
                    current=finding.fix.current,
                    proposed=finding.fix.proposed,
                    note=finding.fix.note,
                    check_id=finding.check_id,
                    quota_cost=COSTS["playlistItems.insert"],
                ))
            continue

        if field == "tags":
            # Tags are a list; union everything requested, preserving order.
            merged = list(group[0].fix.current or [])  # type: ignore[union-attr]
            notes = []
            for finding in group:
                assert finding.fix is not None
                for tag in finding.fix.proposed:
                    if tag not in merged:
                        merged.append(tag)
                notes.append(finding.fix.note)
            if merged != (group[0].fix.current or []):  # type: ignore[union-attr]
                plan.changes.append(Change(
                    video_id=video_id, video_title=video.title, field=field,
                    current=list(group[0].fix.current or []),  # type: ignore[union-attr]
                    proposed=merged,
                    note="; ".join(n for n in notes if n),
                    check_id=",".join(f.check_id for f in group),
                    quota_cost=COSTS["videos.update"],
                ))
            continue

        # Text fields: compose sequentially.
        accumulated = group[0].fix.current  # type: ignore[union-attr]
        notes: list[str] = []
        applied_checks: list[str] = []
        for finding in group:
            assert finding.fix is not None
            new_value, reason = _compose_text(accumulated, finding.fix)
            if new_value is None:
                conflicts.append(Conflict(
                    video_id=video_id, field=field,
                    kept=applied_checks[-1] if applied_checks else "(none)",
                    dropped=finding.check_id, reason=reason))
                plan.skipped.append(finding)
                continue
            accumulated = new_value
            notes.append(finding.fix.note)
            applied_checks.append(finding.check_id)

        if accumulated != group[0].fix.current:  # type: ignore[union-attr]
            plan.changes.append(Change(
                video_id=video_id, video_title=video.title, field=field,
                current=group[0].fix.current,  # type: ignore[union-attr]
                proposed=accumulated,
                note="; ".join(n for n in notes if n),
                check_id=",".join(applied_checks),
                quota_cost=COSTS["videos.update"],
            ))

    plan.changes.sort(key=lambda c: (c.video_id, c.field))
    return plan, conflicts


def split_by_budget(plan: Plan, budget: int) -> tuple[Plan, Plan]:
    """Split a plan into (fits_today, deferred).

    A 300-video repair genuinely cannot complete in one day at 50 units per
    videos.update. Rather than starting and dying at unit 10,001, Pitstop plans
    two days up front and tells the user so. The deferred half re-diffs on the
    next run, so nothing is double-applied.
    """
    today, later = Plan(skipped=plan.skipped), Plan()
    spent = 0
    charged_videos: set[str] = set()

    for change in plan.changes:
        if change.field in SNIPPET_FIELDS | STATUS_FIELDS:
            cost = 0 if change.video_id in charged_videos else COSTS["videos.update"]
        else:
            cost = change.quota_cost

        if spent + cost > budget:
            later.changes.append(change)
            continue

        spent += cost
        if change.field in SNIPPET_FIELDS | STATUS_FIELDS:
            charged_videos.add(change.video_id)
        today.changes.append(change)

    return today, later
