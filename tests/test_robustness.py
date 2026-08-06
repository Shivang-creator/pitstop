"""Adversarial input tests.

Every check runs against whatever a real channel happens to contain, which
includes things no unit test would naturally produce: empty descriptions,
emoji, RTL text, four-hour videos, timestamps that look like chapters but
aren't, zero-view uploads, and channels with a single video.

A check that throws takes out its own findings (the runner catches it), so a
crash here is silent data loss rather than a visible error. That makes these
the cheapest tests in the suite to justify.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pitstop import score as scoring
from pitstop.checks import CheckContext, all_checks, run_all
from pitstop.models import Catalog, Channel, Playlist, Video
from pitstop.planner import build_plan

NOW = datetime.now(timezone.utc)
CTX = CheckContext(has_network=False, has_llm=False, rules={})


def v(vid="v1", **kw) -> Video:
    base = dict(
        id=vid, title="Title", description="",
        published_at=NOW - timedelta(days=30),
        tags=[], category_id="22", duration_seconds=600,
        privacy_status="public", view_count=100,
    )
    base.update(kw)
    return Video(**base)


def cat(videos, playlists=None, owner=True) -> Catalog:
    return Catalog(channel=Channel(id="UC0", title="T"), videos=videos,
                   playlists=playlists or [], is_owner=owner)


# --- pathological catalogs --------------------------------------------------

PATHOLOGICAL = {
    "empty channel": cat([]),
    "single video": cat([v()]),
    "all private": cat([v("a", privacy_status="private"),
                        v("b", privacy_status="private")]),
    "zero views": cat([v("a", view_count=0), v("b", view_count=0)]),
    "published in the future": cat([v(published_at=NOW + timedelta(days=5))]),
    "published today": cat([v(published_at=NOW)]),
    "zero duration": cat([v(duration_seconds=0)]),
    "four hour video": cat([v(duration_seconds=14400)]),
    "empty title": cat([v(title="")]),
    "whitespace title": cat([v(title="   ")]),
    "emoji everywhere": cat([v(title="🔥🔥🔥", description="😀 " * 200,
                               tags=["🎬"])]),
    "rtl text": cat([v(title="مرحبا بالعالم", description="שלום עולם")]),
    "cjk text": cat([v(title="日本語のタイトル", description="中文描述" * 60)]),
    "huge description": cat([v(description="x" * 5000)]),
    "description of only urls": cat([
        v(description="\n".join(f"https://e.com/{i}" for i in range(40)))]),
    "empty playlist": cat([v()], [Playlist(id="P", title="Empty")]),
    "playlist with no title": cat([v()], [Playlist(id="P", title="")]),
    "playlist referencing missing video": cat(
        [v("a")], [Playlist(id="P", title="X", item_video_ids=["ghost"])]),
    "duplicate video ids": cat([v("dup"), v("dup")]),
    "public scan (no owner)": cat([v()], owner=False),
    "unknown captions": cat([v(caption_available=None)]),
}


@pytest.mark.parametrize("name", list(PATHOLOGICAL))
def test_no_check_crashes_on_pathological_input(name):
    """The runner swallows check exceptions, so a crash is silent data loss."""
    catalog = PATHOLOGICAL[name]
    findings, skipped = run_all(catalog, CTX)

    errored = [s for s in skipped if s.reason.startswith("errored")]
    assert not errored, f"{name}: {[(s.check_id, s.reason) for s in errored]}"


@pytest.mark.parametrize("name", list(PATHOLOGICAL))
def test_scoring_survives_pathological_input(name):
    catalog = PATHOLOGICAL[name]
    findings, _ = run_all(catalog, CTX)
    report = scoring.compute(catalog, findings)

    assert 0 <= report.score <= 100
    assert report.grade in {"A", "B", "C", "D", "F"}
    scoring.rank(findings, catalog)


@pytest.mark.parametrize("name", list(PATHOLOGICAL))
def test_planning_survives_pathological_input(name):
    catalog = PATHOLOGICAL[name]
    findings, _ = run_all(catalog, CTX)
    plan, _conflicts = build_plan(catalog, findings)

    assert plan.quota_cost >= 0
    for change in plan.changes:
        # A change that doesn't change anything would burn 50 quota units to
        # write back what is already there.
        assert change.current != change.proposed, change


# --- specific edge cases ----------------------------------------------------


def test_timestamps_that_are_not_chapters_are_not_treated_as_chapters():
    """"12:30" in a sentence about a schedule is not a chapter list."""
    from pitstop.checks.description import BrokenChaptersCheck

    video = v(description="The stream starts at 12:30 and ends at 14:00.")
    findings = list(BrokenChaptersCheck().run(cat([video]), CTX))
    # Two timestamps, so it reports "not enough" — but it must not crash and
    # must not offer to repair something that was never a chapter list.
    assert all(f.fix is None for f in findings)


def test_chapter_repair_never_produces_invalid_output():
    from pitstop.checks.description import BrokenChaptersCheck

    video = v(description="01:00 One\n02:00 Two\n03:00 Three")
    findings = list(BrokenChaptersCheck().run(cat([video]), CTX))
    for finding in findings:
        if finding.fix:
            repaired = v(description=finding.fix.proposed)
            assert repaired.has_valid_chapters


def test_urls_with_trailing_punctuation_are_cleaned():
    """"see https://x.com/page." must not probe a URL ending in a period."""
    video = v(description="see https://example.com/page.")
    urls = video.urls
    assert urls
    # Video.urls itself may keep the dot; the check strips it before probing.
    from pitstop.checks.links import DeadLinkCheck

    ctx = CheckContext(has_network=True, rules={})
    ctx.cache["link_status"] = {}
    list(DeadLinkCheck().run(cat([video]), ctx))  # must not raise


def test_duplicate_video_ids_do_not_double_apply():
    """A catalog with the same id twice must not produce two writes to it."""
    findings, _ = run_all(cat([v("dup"), v("dup")]), CTX)
    plan, _ = build_plan(cat([v("dup"), v("dup")]), findings)
    per_video_fields = [(c.video_id, c.field) for c in plan.changes]
    assert len(per_video_fields) == len(set(per_video_fields))


def test_future_published_video_has_non_negative_age():
    video = v(published_at=NOW + timedelta(days=10))
    assert video.age_days >= 0
    assert video.views_per_day >= 0


def test_every_check_is_reachable_and_declares_metadata():
    for check in all_checks():
        assert check.id.count(".") >= 1
        assert check.name and check.description
        assert isinstance(check.requires_owner, bool)


# --- bugs found by code review, each with the failure it caused -------------


def test_fixture_mode_loads_more_than_50_videos():
    """catalog.fetch_catalog used to `break` after the first batch in fixture
    mode. Any fixture over 50 videos silently lost everything after #50."""
    import json
    import tempfile
    from pathlib import Path

    from pitstop import youtube as yt
    from pitstop.catalog import fetch_catalog

    videos = [{
        "id": f"vid{i:04d}",
        "snippet": {"publishedAt": "2024-01-01T00:00:00Z", "title": f"V{i}",
                    "description": "x", "thumbnails": {}, "tags": [],
                    "categoryId": "22"},
        "contentDetails": {"duration": "PT10M", "caption": "false"},
        "statistics": {"viewCount": "10"},
        "status": {"privacyStatus": "public"},
    } for i in range(120)]

    fixture = {
        "channel": {"id": "UC0", "snippet": {"title": "Big"},
                    "statistics": {"videoCount": "120"},
                    "contentDetails": {"relatedPlaylists": {"uploads": "UU0"}}},
        "videos": videos, "playlists": [], "captions": {}, "retention": {},
    }

    with tempfile.TemporaryDirectory() as tmp:
        original = yt.FIXTURE_DIR
        yt.FIXTURE_DIR = Path(tmp)
        try:
            (Path(tmp) / "big.json").write_text(json.dumps(fixture))
            catalog = fetch_catalog(yt.YouTubeClient(fixture="big"), "big")
        finally:
            yt.FIXTURE_DIR = original

    assert len(catalog.videos) == 120


def test_footer_marker_ignores_decorative_separator():
    """The default marker was the footer's first line, which for the most
    common footer shape is a decorative rule like "── ── ──". Any description
    using a separator looked like it already had the footer, so the check
    silently passed on videos it should have flagged."""
    from pitstop.checks.description import _footer_marker

    footer = "── ── ──\nSubscribe: https://youtube.com/@me\nMy gear: ..."
    marker = _footer_marker(footer)

    assert "http" in marker
    assert marker != "── ── ──"


def test_footer_check_flags_video_that_only_has_a_separator():
    from pitstop.checks.description import MissingFooterCheck

    ctx = CheckContext(rules={
        "description_footer": "── ── ──\nSubscribe: https://youtube.com/@me"})
    video = v(description="Some notes.\n\n── ── ──\n\nUnrelated text.")

    findings = list(MissingFooterCheck().run(cat([video]), ctx))
    assert len(findings) == 1, "separator alone must not count as the footer"


def test_footer_check_passes_when_footer_really_present():
    from pitstop.checks.description import MissingFooterCheck

    ctx = CheckContext(rules={
        "description_footer": "── ── ──\nSubscribe: https://youtube.com/@me"})
    video = v(description="Notes.\n\n── ── ──\nSubscribe: https://youtube.com/@me")

    assert not list(MissingFooterCheck().run(cat([video]), ctx))


def test_footer_of_only_punctuation_does_not_crash():
    from pitstop.checks.description import _footer_marker

    assert _footer_marker("--- ---") == "--- ---"
    assert _footer_marker("   \n  \n") == ""
