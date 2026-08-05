"""Check behaviour, scoring, quota and rule validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pitstop import score as scoring
from pitstop.checks import CheckContext, all_checks
from pitstop.checks.custom import CustomRulesCheck, RuleError
from pitstop.checks.description import (BrokenChaptersCheck,
                                        MissingChaptersCheck,
                                        MissingFooterCheck)
from pitstop.checks.metadata import LazyTitleCheck, StaleWinnerCheck
from pitstop.checks.playlists import OrphanVideoCheck
from pitstop.checks.risk import AdSuitabilityCheck, MissingDisclosureCheck
from pitstop.models import Catalog, Channel, Playlist, Severity, Video
from pitstop.quota import COSTS, QuotaExceeded, QuotaLedger
from pitstop.rules import validate
from pitstop.youtube import parse_channel_ref

NOW = datetime.now(timezone.utc)


def video(vid="v1", *, title="A tutorial", description="", days_old=100,
          views=1000, duration=600, tags=None, privacy="public") -> Video:
    return Video(
        id=vid, title=title, description=description,
        published_at=NOW - timedelta(days=days_old),
        tags=tags or [], duration_seconds=duration,
        view_count=views, privacy_status=privacy,
    )


def catalog(videos, playlists=None, owner=True) -> Catalog:
    return Catalog(channel=Channel(id="UC0", title="T"), videos=videos,
                   playlists=playlists or [], is_owner=owner)


CTX = CheckContext(has_network=False, has_llm=False, rules={})


# --- chapters ---------------------------------------------------------------


def test_valid_chapters_are_not_flagged():
    v = video(description="00:00 Intro\n02:30 Setup\n05:00 Build")
    assert v.has_valid_chapters
    assert not list(MissingChaptersCheck().run(catalog([v]), CTX))


def test_chapters_not_starting_at_zero_are_flagged_and_repaired():
    v = video(description="01:20 Setup\n03:00 Build\n06:00 Done")
    findings = list(BrokenChaptersCheck().run(catalog([v]), CTX))

    assert len(findings) == 1
    assert "must be 00:00" in findings[0].detail
    assert findings[0].fix is not None
    assert findings[0].fix.proposed.startswith("00:00 Intro")


def test_two_timestamps_is_not_enough_and_is_not_auto_repaired():
    """Fewer than three chapters needs the creator to decide what the sections
    are. We report it; we do not invent chapter titles."""
    v = video(description="00:00 Intro\n02:00 End")
    findings = list(BrokenChaptersCheck().run(catalog([v]), CTX))

    assert len(findings) == 1
    assert "only 2 timestamp" in findings[0].detail
    assert findings[0].fix is None


def test_short_videos_are_exempt_from_the_chapter_check():
    v = video(duration=90, description="no chapters here")
    assert not list(MissingChaptersCheck().run(catalog([v]), CTX))


def test_missing_and_broken_chapters_never_double_report():
    v = video(description="01:20 Setup\n03:00 Build\n06:00 Done")
    cat = catalog([v])
    missing = list(MissingChaptersCheck().run(cat, CTX))
    broken = list(BrokenChaptersCheck().run(cat, CTX))
    assert len(missing) + len(broken) == 1


# --- playlists --------------------------------------------------------------


def test_orphan_video_is_flagged():
    v = video()
    findings = list(OrphanVideoCheck().run(catalog([v]), CTX))
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING


def test_video_in_a_playlist_is_not_flagged():
    v = video()
    pl = Playlist(id="PL1", title="Series", item_video_ids=["v1"])
    assert not list(OrphanVideoCheck().run(catalog([v], [pl]), CTX))


def test_playlist_suggestion_requires_a_real_title_match():
    """A weak overlap must not turn one playlist into a dumping ground."""
    v = video(title="Blender: modelling a chair", tags=["blender"])
    good = Playlist(id="PL1", title="Blender Basics")
    cat = catalog([v], [Playlist(id="PL0", title="Unrelated Cooking Show"),
                        good])

    finding = next(iter(OrphanVideoCheck().run(cat, CTX)))

    assert finding.evidence["suggested_playlist_id"] == "PL1"


def test_private_videos_are_not_reported_as_orphans():
    v = video(privacy="private")
    assert not list(OrphanVideoCheck().run(catalog([v]), CTX))


# --- metadata ---------------------------------------------------------------


@pytest.mark.parametrize("title", [
    "VID_20240417.mp4", "IMG_9931", "untitled", "final2", "my video.mov",
])
def test_placeholder_titles_are_caught(title):
    findings = list(LazyTitleCheck().run(catalog([video(title=title)]), CTX))
    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL


def test_real_titles_are_not_flagged():
    v = video(title="How I built a standing desk from scrap plywood")
    assert not list(LazyTitleCheck().run(catalog([v]), CTX))


def test_stale_winner_needs_both_age_and_traffic():
    old_popular = video("v1", days_old=800, views=80_000)
    old_quiet = video("v2", days_old=800, views=10)
    new_popular = video("v3", days_old=10, views=80_000)

    findings = list(StaleWinnerCheck().run(
        catalog([old_popular, old_quiet, new_popular]), CTX))

    assert [f.video_id for f in findings] == ["v1"]


# --- risk -------------------------------------------------------------------


def test_ad_suitability_flag_cites_a_guideline_and_disclaims_prediction():
    v = video(title="I tried cocaine for a week")
    findings = list(AdSuitabilityCheck().run(catalog([v]), CTX))

    assert findings
    finding = findings[0]
    assert finding.evidence["is_prediction"] is False
    assert finding.evidence["guideline"]
    assert finding.title.startswith("Review:")


def test_clean_content_produces_no_risk_findings():
    v = video(title="Building a REST API in Python",
              description="A calm walkthrough of building an API.")
    assert not list(AdSuitabilityCheck().run(catalog([v]), CTX))


def test_sponsorship_without_disclosure_is_critical():
    v = video(description="Thanks to Acme for sponsoring. Use code SAVE20.")
    findings = list(MissingDisclosureCheck().run(catalog([v]), CTX))

    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL
    # Adding a legal disclosure on a creator's behalf is not a tool's call.
    assert findings[0].fix is None


def test_sponsorship_with_disclosure_is_clean():
    v = video(description="#ad Thanks to Acme for sponsoring. Use code SAVE20.")
    assert not list(MissingDisclosureCheck().run(catalog([v]), CTX))


# --- custom rules -----------------------------------------------------------


def test_custom_rule_fires_and_offers_a_fix():
    ctx = CheckContext(rules={"rules": [{
        "id": "tut", "name": "Tutorials tagged",
        "when": {"title_matches": "(?i)tutorial"},
        "require": {"has_tag": "tutorial"},
        "fix": "add_tag",
    }]})
    v = video(title="Python tutorial", tags=["python"])

    findings = list(CustomRulesCheck().run(catalog([v]), ctx))

    assert len(findings) == 1
    assert findings[0].fix.proposed == ["python", "tutorial"]


def test_custom_rule_does_not_fire_outside_its_when_clause():
    ctx = CheckContext(rules={"rules": [{
        "id": "tut", "when": {"title_matches": "(?i)tutorial"},
        "require": {"has_tag": "tutorial"},
    }]})
    v = video(title="A vlog about nothing", tags=[])
    assert not list(CustomRulesCheck().run(catalog([v]), ctx))


def test_unknown_predicate_is_a_loud_error():
    with pytest.raises(RuleError, match="unknown predicate"):
        validate({"rules": [{"id": "x", "require": {"nonsense": 1}}]})


def test_duplicate_rule_ids_are_rejected():
    with pytest.raises(RuleError, match="duplicate id"):
        validate({"rules": [
            {"id": "a", "require": {"min_tags": 1}},
            {"id": "a", "require": {"min_tags": 2}},
        ]})


def test_add_tag_fix_requires_a_target_tag():
    with pytest.raises(RuleError, match="needs `require.has_tag`"):
        validate({"rules": [{"id": "a", "require": {"min_tags": 1},
                             "fix": "add_tag"}]})


# --- scoring ----------------------------------------------------------------


def test_clean_channel_scores_100():
    report = scoring.compute(catalog([video()]), [])
    assert report.score == 100
    assert report.grade == "A"


def test_score_falls_as_findings_accumulate():
    from pitstop.models import Finding

    videos = [video(f"v{i}") for i in range(10)]
    cat = catalog(videos)

    def score_with(n_critical):
        findings = [
            Finding(check_id="links.dead", severity=Severity.CRITICAL,
                    title="t", detail="d", video_id=f"v{i}")
            for i in range(n_critical)
        ]
        return scoring.compute(cat, findings).score

    assert score_with(0) == 100
    assert score_with(3) < score_with(0)
    assert score_with(10) < score_with(3)


def test_score_is_comparable_across_channel_sizes():
    """A 200-video channel and a 10-video channel with the same *rate* of
    problems must score alike — otherwise the number just measures size."""
    from pitstop.models import Finding

    def score_for(n):
        videos = [video(f"v{i}", views=1000) for i in range(n)]
        findings = [
            Finding(check_id="links.dead", severity=Severity.CRITICAL,
                    title="t", detail="d", video_id=f"v{i}")
            for i in range(n // 2)
        ]
        return scoring.compute(catalog(videos), findings).score

    assert abs(score_for(10) - score_for(200)) <= 3


def test_high_traffic_findings_outrank_identical_low_traffic_ones():
    from pitstop.models import Finding

    big = video("big", views=1_000_000, days_old=100)
    small = video("small", views=10, days_old=100)
    cat = catalog([big, small])
    findings = [
        Finding(check_id="links.dead", severity=Severity.CRITICAL, title="t",
                detail="d", video_id="small"),
        Finding(check_id="links.dead", severity=Severity.CRITICAL, title="t",
                detail="d", video_id="big"),
    ]

    ranked = scoring.rank(findings, cat)

    assert ranked[0].video_id == "big"


def test_priority_videos_lead_with_the_worst():
    from pitstop.models import Finding

    videos = [video("a", views=100_000), video("b", views=100)]
    findings = [
        Finding(check_id="links.dead", severity=Severity.CRITICAL, title="t",
                detail="d", video_id="a"),
        Finding(check_id="playlist.orphan", severity=Severity.WARNING,
                title="t", detail="d", video_id="a"),
        Finding(check_id="playlist.orphan", severity=Severity.WARNING,
                title="t", detail="d", video_id="b"),
    ]

    report = scoring.compute(catalog(videos), findings)

    assert report.priority_videos[0]["video_id"] == "a"


# --- quota ------------------------------------------------------------------


def test_ledger_refuses_to_exceed_budget():
    ledger = QuotaLedger(budget=100)
    ledger.charge("videos.update")   # 50
    ledger.charge("videos.update")   # 100
    with pytest.raises(QuotaExceeded):
        ledger.charge("videos.update")
    assert ledger.spent == 100       # the failed charge did not apply


def test_documented_costs_match_googles_published_table():
    assert COSTS["videos.list"] == 1
    assert COSTS["videos.update"] == 50
    assert COSTS["thumbnails.set"] == 50
    assert COSTS["playlistItems.insert"] == 50
    assert COSTS["captions.insert"] == 400
    assert COSTS["search.list"] == 100


# --- channel reference parsing ----------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("UCYAQdWin40MPzE7FreE_xKQ", ("id", "UCYAQdWin40MPzE7FreE_xKQ")),
    ("https://www.youtube.com/channel/UCYAQdWin40MPzE7FreE_xKQ",
     ("id", "UCYAQdWin40MPzE7FreE_xKQ")),
    ("https://www.youtube.com/@mkbhd", ("handle", "mkbhd")),
    ("https://youtube.com/@mkbhd/videos", ("handle", "mkbhd")),
    ("@mkbhd", ("handle", "mkbhd")),
    ("mkbhd", ("handle", "mkbhd")),
    ("https://www.youtube.com/c/LegacyName", ("handle", "LegacyName")),
    ("https://www.youtube.com/user/OldSchool", ("handle", "OldSchool")),
])
def test_channel_reference_parsing(raw, expected):
    assert parse_channel_ref(raw) == expected


# --- registry ---------------------------------------------------------------


def test_every_check_declares_an_id_name_and_description():
    for check in all_checks():
        assert check.id and "." in check.id
        assert check.name
        assert check.description


def test_check_ids_are_unique():
    ids = [c.id for c in all_checks()]
    assert len(ids) == len(set(ids))
