"""Planner tests — the fix-composition logic is the riskiest code in the repo.

When several checks independently propose a new description for the same
video, each computed its proposal from the *original* text. Applying them
naively is last-writer-wins, and two of three reviewed repairs vanish
silently. These tests pin the composition behaviour down.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pitstop.models import (Catalog, Channel, Finding, Fix, Playlist,
                            Severity, Video)
from pitstop.planner import build_plan, split_by_budget


def make_video(video_id="v1", description="line one\nline two\nline three",
               **kwargs) -> Video:
    defaults = dict(
        id=video_id, title="A video", description=description,
        published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        view_count=1000,
    )
    defaults.update(kwargs)
    return Video(**defaults)


def make_catalog(videos, playlists=None) -> Catalog:
    return Catalog(
        channel=Channel(id="UC0", title="Test"),
        videos=videos, playlists=playlists or [], is_owner=True,
    )


def finding(video, current, proposed, check_id="test.check",
            field="description", note="") -> Finding:
    return Finding(
        check_id=check_id, severity=Severity.WARNING, title="t", detail="d",
        video_id=video.id,
        fix=Fix(field=field, current=current, proposed=proposed, note=note),
    )


# --- composition ------------------------------------------------------------


def test_two_appends_both_survive():
    video = make_video(description="body")
    catalog = make_catalog([video])
    findings = [
        finding(video, "body", "body\nFOOTER", "a", note="footer"),
        finding(video, "body", "body\nLINKS", "b", note="links"),
    ]

    plan, conflicts = build_plan(catalog, findings)

    assert not conflicts
    assert len(plan.changes) == 1
    proposed = plan.changes[0].proposed
    assert "FOOTER" in proposed and "LINKS" in proposed
    assert proposed.startswith("body")


def test_append_and_prepend_compose():
    video = make_video(description="middle")
    catalog = make_catalog([video])
    findings = [
        finding(video, "middle", "00:00 Intro\nmiddle", "chapters"),
        finding(video, "middle", "middle\nFOOTER", "footer"),
    ]

    plan, conflicts = build_plan(catalog, findings)

    assert not conflicts
    proposed = plan.changes[0].proposed
    assert proposed == "00:00 Intro\nmiddle\nFOOTER"


def test_substring_replacement_replays_after_an_append():
    """The dead-link fix must still land even though the footer fix ran first
    and changed the string it was computed against."""
    original = "see https://dead.example/x for gear"
    video = make_video(description=original)
    catalog = make_catalog([video])
    findings = [
        finding(video, original, original + "\nFOOTER", "footer"),
        finding(video, original,
                original.replace("https://dead.example/x", "[REMOVED]"),
                "links"),
    ]

    plan, conflicts = build_plan(catalog, findings)

    assert not conflicts
    proposed = plan.changes[0].proposed
    assert "[REMOVED]" in proposed
    assert "https://dead.example/x" not in proposed
    assert "FOOTER" in proposed


def test_unmergeable_fix_is_dropped_not_guessed():
    """If an earlier fix removed the text a later fix targets, the later fix is
    reported as a conflict rather than applied somewhere approximate."""
    video = make_video(description="alpha beta")
    catalog = make_catalog([video])
    findings = [
        finding(video, "alpha beta", "totally different", "first"),
        finding(video, "alpha beta", "alpha GAMMA", "second"),
    ]

    plan, conflicts = build_plan(catalog, findings)

    assert len(conflicts) == 1
    assert conflicts[0].dropped == "second"
    assert plan.changes[0].proposed == "totally different"


def test_composition_is_deterministic():
    """Two runs over the same input must produce byte-identical plans, or the
    diff is not reviewable and CI cannot diff it."""
    video = make_video(description="body")
    catalog = make_catalog([video])
    findings = [
        finding(video, "body", "body\nZ", "z.check"),
        finding(video, "body", "body\nA", "a.check"),
    ]

    first, _ = build_plan(catalog, findings)
    second, _ = build_plan(catalog, list(reversed(findings)))

    assert first.changes[0].proposed == second.changes[0].proposed


def test_tags_are_unioned_not_overwritten():
    video = make_video(tags=["one"])
    catalog = make_catalog([video])
    findings = [
        finding(video, ["one"], ["one", "two"], "a", field="tags"),
        finding(video, ["one"], ["one", "three"], "b", field="tags"),
    ]

    plan, _ = build_plan(catalog, findings)

    assert plan.changes[0].proposed == ["one", "two", "three"]


def test_noop_fix_produces_no_change():
    video = make_video(description="same")
    catalog = make_catalog([video])
    findings = [finding(video, "same", "same", "a")]

    plan, _ = build_plan(catalog, findings)

    assert plan.changes == []


# --- quota ------------------------------------------------------------------


def test_multiple_fields_on_one_video_cost_one_update():
    """videos.update is billed per call, not per field. Three field edits on
    one video must price at 50 units, not 150."""
    video = make_video(description="body", tags=["a"])
    catalog = make_catalog([video])
    findings = [
        finding(video, "body", "body\nX", "a"),
        finding(video, ["a"], ["a", "b"], "b", field="tags"),
        finding(video, "Old title", "New title", "c", field="title"),
    ]

    plan, _ = build_plan(catalog, findings)

    assert plan.quota_cost == 50
    assert plan.affected_videos == 1


def test_playlist_ops_are_billed_separately():
    video = make_video()
    catalog = make_catalog([video])
    findings = [
        finding(video, "body", "body\nX", "a"),
        finding(video, None, "PL123", "b", field="playlist_add"),
    ]

    plan, _ = build_plan(catalog, findings)

    assert plan.quota_cost == 100  # 50 update + 50 playlistItems.insert


def test_split_by_budget_defers_the_overflow():
    videos = [make_video(f"v{i}") for i in range(10)]
    catalog = make_catalog(videos)
    findings = [finding(v, v.description, v.description + "\nX", "a")
                for v in videos]

    plan, _ = build_plan(catalog, findings)
    assert plan.quota_cost == 500

    today, later = split_by_budget(plan, budget=150)

    assert len(today.changes) == 3      # 3 × 50 = 150
    assert len(later.changes) == 7
    assert today.quota_cost <= 150


def test_split_by_budget_does_not_double_charge_one_video():
    """Two field edits on one video are one charge, so a 50-unit budget must
    fit both — not just the first."""
    video = make_video(tags=["a"])
    catalog = make_catalog([video])
    findings = [
        finding(video, video.description, video.description + "\nX", "a"),
        finding(video, ["a"], ["a", "b"], "b", field="tags"),
    ]

    plan, _ = build_plan(catalog, findings)
    today, later = split_by_budget(plan, budget=50)

    assert len(today.changes) == 2
    assert len(later.changes) == 0


# --- filtering --------------------------------------------------------------


def test_non_fixable_findings_land_in_skipped():
    video = make_video()
    catalog = make_catalog([video])
    advisory = Finding(check_id="x", severity=Severity.CRITICAL, title="t",
                       detail="d", video_id=video.id, fix=None)

    plan, _ = build_plan(catalog, [advisory])

    assert plan.changes == []
    assert advisory in plan.skipped


def test_only_checks_filter():
    video = make_video()
    catalog = make_catalog([video])
    findings = [
        finding(video, "body", "body\nA", "keep.me"),
        finding(video, "body", "body\nB", "drop.me"),
    ]

    plan, _ = build_plan(catalog, findings, only_checks=["keep.me"])

    assert len(plan.changes) == 1
    assert "A" in plan.changes[0].proposed
    assert "B" not in plan.changes[0].proposed


@pytest.mark.parametrize("field", ["title", "description", "categoryId"])
def test_snippet_fields_group_into_one_change_set(field):
    video = make_video()
    catalog = make_catalog([video])
    findings = [finding(video, "old", "new", "a", field=field)]

    plan, _ = build_plan(catalog, findings)

    assert plan.quota_cost == 50
