"""The public web scan: its ceilings, and its honesty about them.

The public scanner is the only part of Pitstop that runs against a stranger's
channel, inside one HTTP request, for someone who will never read the source.
That combination makes two failure modes much more expensive than they are
anywhere else in the codebase:

  * **Silently reporting a partial scan as a complete one.** Every ceiling here
    is allowed to cost findings. None of them is allowed to go unmentioned.
  * **Inventing a finding because we ran out of time.** A link we never
    resolved must never be reported as dead. The budget can only ever subtract.

Both are asserted directly below, because both are the kind of bug that looks
like a working product right up until someone checks the number.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pitstop import youtube as yt
from pitstop.catalog import fetch_catalog
from pitstop.checks import CheckContext, run_all
from pitstop.checks.links import DeadLinkCheck, LinkStatus
from pitstop.models import Catalog, Channel, Playlist, Video
from pitstop.public_scan import (
    PLAYLIST_DEPENDENT_CHECKS,
    PublicScanError,
    _is_quota_error,
    _limits,
    result_json,
    PublicScanResult,
)
from pitstop import score as scoring

NOW = datetime.now(timezone.utc)


def v(vid="v1", **kw) -> Video:
    base = dict(id=vid, title=f"Title {vid}", description="",
                published_at=NOW - timedelta(days=30), tags=[],
                category_id="22", duration_seconds=600,
                privacy_status="public", view_count=1000)
    base.update(kw)
    return Video(**base)


def cat(videos, playlists=None, **kw) -> Catalog:
    return Catalog(channel=Channel(id="UC0", title="T", video_count=len(videos)),
                   videos=videos, playlists=playlists or [], is_owner=False,
                   **kw)


# --- the link budget subtracts, never adds ----------------------------------


def test_links_past_the_cap_are_unreached_not_dead():
    """The cap must cost us findings, never manufacture one.

    An unresolved link reported as dead would send a creator to edit a
    description that was fine — the single most damaging false positive this
    tool can produce, on the check people judge the whole thing by.
    """
    videos = [v(f"v{i}", description=f"see https://example.com/{i}")
              for i in range(10)]
    ctx = CheckContext(has_network=True, rules={}, link_max_urls=3)
    # Pre-seed so no real network call happens: the three we "reach" are alive.
    ctx.cache["link_status"] = {}

    findings = list(DeadLinkCheck().run(cat(videos), ctx))

    budget = ctx.cache["link_budget"]
    assert budget["unique_urls"] == 10
    assert budget["over_cap"] == 7
    # Nothing beyond the cap was probed, so nothing beyond it can be dead.
    unreached = [u for u, s in ctx.cache["link_status"].items()
                 if s[0] == LinkStatus.UNREACHED]
    assert len(unreached) == 7
    assert all(f.evidence.get("url") not in unreached for f in findings)


def test_unreached_links_produce_no_findings_at_all():
    videos = [v("a", description="https://gone.invalid/x")]
    ctx = CheckContext(has_network=True, rules={}, link_max_urls=0)
    ctx.cache["link_status"] = {}

    assert list(DeadLinkCheck().run(cat(videos), ctx)) == []
    assert ctx.cache["link_budget"]["resolved"] == 0


def test_highest_traffic_links_are_resolved_first():
    """When the budget bites, it has to bite the videos that matter least."""
    videos = [
        v("quiet", description="https://example.com/quiet", view_count=1),
        v("loud", description="https://example.com/loud", view_count=1_000_000),
    ]
    ctx = CheckContext(has_network=True, rules={}, link_max_urls=1)
    ctx.cache["link_status"] = {}

    DeadLinkCheck().run(cat(videos), ctx)
    list(DeadLinkCheck().run(cat(videos), ctx))

    status = ctx.cache["link_status"]
    assert status["https://example.com/quiet"][0] == LinkStatus.UNREACHED
    assert status["https://example.com/loud"][0] != LinkStatus.UNREACHED


def test_no_budget_means_no_ceiling_reported():
    """The CLI path sets neither ceiling and must behave exactly as before."""
    videos = [v("a", description="https://example.com/1")]
    ctx = CheckContext(has_network=True, rules={})
    ctx.cache["link_status"] = {"https://example.com/1":
                                (LinkStatus.ALIVE, 200, "https://example.com/1")}

    list(DeadLinkCheck().run(cat(videos), ctx))
    assert ctx.cache["link_budget"]["unreached"] == 0


# --- truncation is recorded, and it disables what it would corrupt ----------


def _fixture_client(videos: int, playlists: int):
    """A FIXTURE-mode client with a given shape, written to a temp dir."""
    payload = {
        "channel": {"id": "UC0", "snippet": {"title": "Big"},
                    "statistics": {"videoCount": str(videos)},
                    "contentDetails": {"relatedPlaylists": {"uploads": "UU0"}}},
        "videos": [{
            "id": f"vid{i:04d}",
            "snippet": {"publishedAt": "2024-01-01T00:00:00Z", "title": f"V{i}",
                        "description": "x", "thumbnails": {}, "tags": [],
                        "categoryId": "22"},
            "contentDetails": {"duration": "PT10M", "caption": "false"},
            "statistics": {"viewCount": "10"},
            "status": {"privacyStatus": "public"},
        } for i in range(videos)],
        "playlists": [{"id": f"PL{i}", "title": f"List {i}",
                       "item_video_ids": [f"vid{i:04d}"]}
                      for i in range(playlists)],
        "captions": {}, "retention": {},
    }
    tmp = tempfile.mkdtemp()
    (Path(tmp) / "shape.json").write_text(json.dumps(payload))
    return tmp


def test_video_cap_is_recorded_on_the_catalog():
    tmp = _fixture_client(videos=120, playlists=2)
    original = yt.FIXTURE_DIR
    yt.FIXTURE_DIR = Path(tmp)
    try:
        catalog = fetch_catalog(yt.YouTubeClient(fixture="shape"), "shape",
                                limit=40)
    finally:
        yt.FIXTURE_DIR = original

    assert len(catalog.videos) == 40
    assert catalog.videos_truncated is True


def test_a_complete_fetch_is_not_marked_truncated():
    tmp = _fixture_client(videos=12, playlists=2)
    original = yt.FIXTURE_DIR
    yt.FIXTURE_DIR = Path(tmp)
    try:
        catalog = fetch_catalog(yt.YouTubeClient(fixture="shape"), "shape",
                                limit=150, playlist_limit=100)
    finally:
        yt.FIXTURE_DIR = original

    assert catalog.videos_truncated is False
    assert catalog.playlists_truncated is False
    assert _limits(catalog, CheckContext(rules={}), 150, 100) == []


def test_playlist_cap_is_recorded():
    tmp = _fixture_client(videos=5, playlists=30)
    original = yt.FIXTURE_DIR
    yt.FIXTURE_DIR = Path(tmp)
    try:
        catalog = fetch_catalog(yt.YouTubeClient(fixture="shape"), "shape",
                                playlist_limit=10)
    finally:
        yt.FIXTURE_DIR = original

    assert len(catalog.playlists) == 10
    assert catalog.playlists_truncated is True


def test_truncated_playlists_disable_the_orphan_checks():
    """A video whose only playlist we never fetched looks orphaned.

    Reporting that would be a false positive caused purely by our own ceiling,
    so the checks are switched off and the reason is stated instead.
    """
    videos = [v("a"), v("b")]
    catalog = cat(videos, playlists=[Playlist(id="PL1", title="L",
                                              item_video_ids=["a"])],
                  playlists_truncated=True)

    runnable = [c for c in
                (check.id for check in __import__(
                    "pitstop.checks", fromlist=["all_checks"]).all_checks())
                if c not in PLAYLIST_DEPENDENT_CHECKS]
    findings, _ = run_all(catalog, CheckContext(has_network=False, rules={}),
                          only=runnable)

    assert not any(f.check_id in PLAYLIST_DEPENDENT_CHECKS for f in findings)


def test_truncation_always_surfaces_as_a_limit():
    catalog = cat([v("a")], playlists_truncated=True)
    catalog.videos_truncated = True
    catalog.channel.video_count = 900

    limits = _limits(catalog, CheckContext(rules={}), 150, 100)
    keys = {limit.key for limit in limits}

    assert "videos" in keys and "playlists" in keys
    # Every limit has to be sayable out loud, not just flagged internally.
    assert all(limit.label and limit.detail for limit in limits)


# --- quota is told apart from every other 403 -------------------------------


def test_quota_errors_are_distinguished_from_ordinary_403s():
    """403 quotaExceeded and 403 forbidden mean opposite things to a visitor:
    one is "come back tomorrow", the other is "that channel is private"."""
    quota = Exception('<HttpError 403 ... "reason": "quotaExceeded">')
    daily = Exception('<HttpError 403 ... "reason": "dailyLimitExceeded">')
    forbidden = Exception('<HttpError 403 ... "reason": "forbidden">')
    missing = Exception("<HttpError 404 not found>")

    assert _is_quota_error(quota) and _is_quota_error(daily)
    assert not _is_quota_error(forbidden)
    assert not _is_quota_error(missing)


def test_api_key_never_survives_into_an_error_message():
    """Regression: a malformed key produced a 400 whose text was the full
    request URL — including `key=<the API key>` — and that string was being
    handed straight back to whoever typed the channel name. On a public
    endpoint that is credential disclosure to anonymous users.
    """
    from pitstop.public_scan import redact

    leaked = (
        'HttpError: <HttpError 400 when requesting '
        'https://youtube.googleapis.com/youtube/v3/channels'
        '?part=snippet&forHandle=mkbhd&key=AIzaSyREAL_SECRET_VALUE_1234&alt=json'
        ' returned "API key not valid.">'
    )
    cleaned = redact(leaked)

    assert "AIzaSyREAL_SECRET_VALUE_1234" not in cleaned
    assert "key=[redacted]" in cleaned
    # Still diagnosable — the useful half of the message survives.
    assert "API key not valid" in cleaned
    assert "400" in cleaned


def test_redact_handles_empty_and_keyless_text():
    from pitstop.public_scan import redact

    assert redact("") == ""
    assert redact("plain message") == "plain message"


def test_blank_channel_is_a_clean_error_not_a_crash():
    from pitstop.public_scan import public_scan

    try:
        public_scan("   ")
    except PublicScanError as exc:
        assert exc.kind == "not_found"
        assert exc.message
    else:
        raise AssertionError("blank input should raise PublicScanError")


# --- the wire format matches the engine's verdict ---------------------------


def test_result_json_preserves_the_engine_ranking_and_score():
    videos = [v(f"v{i}", description="", view_count=1000 * i)
              for i in range(1, 6)]
    catalog = cat(videos)
    findings, skipped = run_all(catalog, CheckContext(has_network=False,
                                                      rules={}))
    report = scoring.compute(catalog, findings)
    payload = result_json(PublicScanResult(
        catalog=catalog, findings=findings, report=report, skipped=skipped))

    # The page reports exactly what score.py computed — no second opinion.
    assert payload["score"]["score"] == report.score
    assert payload["score"]["grade"] == report.grade
    assert payload["score"]["headline"] == report.headline
    assert payload["score"]["total_findings"] == report.total_findings
    assert len(payload["score"]["categories"]) == len(report.categories)

    # Groups arrive in the engine's ranked order, worst first.
    ranked = scoring.rank(findings, catalog)
    if payload["groups"]:
        first = payload["groups"][0]
        assert first["check_id"] == ranked[0].check_id

    assert payload["videos_scanned"] == len(videos)
    assert "quota_spent" in payload


def test_web_and_cli_report_the_same_verdict():
    """The page must not be able to quote a different number from the CLI.

    Verified against live channels too, but the network makes that test lie: a
    host that 404s during one run and answers during the next changes the
    dead-link count and therefore the score, which looks exactly like a code
    divergence and isn't. So the link results are resolved once and replayed
    into both paths, leaving the engine as the only variable.
    """
    from pitstop.cli import _report_payload
    from pitstop.quota import QuotaLedger

    videos = [
        v("a", description="watch https://example.com/dead", view_count=90_000),
        v("b", description="", view_count=40_000),
        v("c", description="x" * 400, duration_seconds=1800, view_count=5_000),
    ]
    catalog = cat(videos, playlists=[Playlist(id="PL1", title="L",
                                              item_video_ids=["a"])])
    link_cache = {"https://example.com/dead": (LinkStatus.DEAD, 404,
                                               "https://example.com/dead")}

    def run():
        ctx = CheckContext(has_network=True, has_llm=False, rules={})
        ctx.cache["link_status"] = dict(link_cache)
        findings, skipped = run_all(catalog, ctx)
        return findings, skipped, scoring.compute(catalog, findings)

    f_cli, _, r_cli = run()
    f_web, s_web, r_web = run()

    class _Ledger:
        ledger = QuotaLedger(budget=9000)

    cli = _report_payload(catalog, f_cli, r_cli, _Ledger())["score"]
    web = result_json(PublicScanResult(catalog=catalog, findings=f_web,
                                       report=r_web, skipped=s_web))["score"]

    for field_name in ("score", "grade", "total_findings", "critical",
                       "warning", "notice", "auto_fixable", "affected_videos"):
        assert cli[field_name] == web[field_name], field_name

    assert ({c["label"]: c["score"] for c in cli["categories"]}
            == {c["label"]: c["score"] for c in web["categories"]})


def test_result_json_is_serialisable():
    """It goes down an SSE wire as JSON — a dataclass leaking through would
    fail in production and nowhere else."""
    catalog = cat([v("a")])
    findings, skipped = run_all(catalog, CheckContext(has_network=False,
                                                      rules={}))
    payload = result_json(PublicScanResult(
        catalog=catalog, findings=findings,
        report=scoring.compute(catalog, findings), skipped=skipped))

    assert json.loads(json.dumps(payload, default=str))


# --- the deployed app cannot write ------------------------------------------


def test_deployed_app_never_imports_the_writer():
    """Read-only is meant to be structural, not a promise in a docstring.

    Asserted against the real import graph in a clean interpreter, because
    that is the thing that would actually be deployed. If someone later wires
    the applier into the public app, this fails — which is the entire point.
    """
    import subprocess
    import sys

    probe = (
        "import sys; import pitstop.web_app; "
        "leaked = [m for m in ('pitstop.applier', 'pitstop.planner', "
        "'pitstop.setup_oauth', 'google_auth_oauthlib') if m in sys.modules]; "
        "print(','.join(leaked))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, cwd=Path(__file__).resolve().parent.parent)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"public app pulled in {out.stdout.strip()}"


def test_public_app_exposes_no_mutating_route():
    import pitstop.web_app as web_app

    routes = {getattr(r, "path", "") for r in web_app.app.routes}
    assert not any("apply" in p or "plan" in p for p in routes)
    assert "/api/scan" in routes


def test_a_public_client_refuses_every_write():
    """The deployed client is built with owner=False. Prove that shape cannot
    write, rather than trusting that no endpoint calls it."""
    from pitstop.youtube import AuthRequired, YouTubeClient

    client = object.__new__(YouTubeClient)
    client.mode = "PUBLIC"
    client.is_owner = False

    for call in (lambda: client.update_video("v", {"title": "x"}),
                 lambda: client.add_to_playlist("PL", "v"),
                 lambda: client.create_playlist("new"),
                 lambda: client.caption_availability(["v"]),
                 lambda: client.retention_metrics(["v"])):
        try:
            call()
        except AuthRequired:
            continue
        raise AssertionError("a public client performed a privileged call")
