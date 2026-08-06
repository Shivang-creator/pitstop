"""Benchmark measurement, Shorts handling, and LLM output sanitising."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pitstop import benchmark as bench
from pitstop.enrich import LLMUnavailable, _extract_json_array, _fmt, _to_seconds
from pitstop.models import Video

NOW = datetime.now(timezone.utc)


def v(vid="v1", *, title="A video", description="", duration=600, tags=None,
      views=1000, caption=None) -> Video:
    return Video(
        id=vid, title=title, description=description,
        published_at=NOW - timedelta(days=30),
        tags=tags or [], duration_seconds=duration, view_count=views,
        caption_available=caption,
    )


# --- categories -------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("tech", "28"), ("technology", "28"), ("coding", "28"),
    ("gaming", "20"), ("education", "27"), ("howto", "26"),
    ("28", "28"), ("Science & Technology", "28"), ("science", "28"),
    ("nonsense-category", None), ("", None), (None, None),
])
def test_category_resolution(value, expected):
    assert bench.resolve_category(value) == expected


# --- Shorts -----------------------------------------------------------------


def test_shorts_are_split_by_duration():
    videos = [v("a", duration=30), v("b", duration=600), v("c", duration=89)]
    long_form, shorts = bench.split_shorts(videos)

    assert [x.id for x in long_form] == ["b"]
    assert {x.id for x in shorts} == {"a", "c"}


def test_unknown_duration_is_not_assumed_to_be_a_short():
    """Duration 0 means the API didn't say, not that it's 0 seconds long.
    Discarding those would silently shrink the reference set."""
    long_form, shorts = bench.split_shorts([v("a", duration=0)])
    assert [x.id for x in long_form] == ["a"]
    assert not shorts


def test_shorts_are_excluded_from_the_reference_by_default():
    """Regression: YouTube's trending chart is mostly Shorts in many regions.
    Leaving them in dragged 'median video length' to 151s and made every
    long-form creator look 'far behind' on chapters, which Shorts never use."""
    reference = [v(f"s{i}", duration=30) for i in range(30)]
    reference += [v(f"l{i}", duration=900,
                    description="00:00 Intro\n02:00 Body\n05:00 End")
                  for i in range(6)]

    report = bench.compare(reference, None, source="test")

    assert report.sample_size == 6
    assert report.shorts_excluded == 30
    chapters = next(p for p in report.practices if p.key == "chapters_pct")
    assert chapters.reference == 100.0


def test_all_shorts_reference_produces_a_caveat_not_bad_advice():
    reference = [v(f"s{i}", duration=30) for i in range(10)]
    report = bench.compare(reference, None, source="test")

    assert report.caveat
    assert "Short" in report.caveat


def test_include_shorts_flag_keeps_them():
    reference = [v(f"s{i}", duration=30) for i in range(10)]
    report = bench.compare(reference, None, source="test",
                           include_shorts=True)

    assert report.sample_size == 10
    assert report.shorts_excluded == 0


def test_your_shorts_are_also_excluded_so_the_comparison_is_like_for_like():
    reference = [v(f"l{i}", duration=900, tags=["a"] * 10) for i in range(5)]
    yours = [v("mine-short", duration=20, tags=[]),
             v("mine-long", duration=800, tags=["a"] * 10)]

    report = bench.compare(reference, yours, source="test")
    tags = next(p for p in report.practices if p.key == "tag_count")

    assert tags.yours == 10  # the Short's 0 tags must not drag this down


# --- measurement ------------------------------------------------------------


def test_measure_on_empty_input_is_empty_not_a_crash():
    assert bench.measure([]) == {}


def test_chapters_percentage_counts_only_valid_chapter_lists():
    videos = [
        v("a", description="00:00 Intro\n01:00 Mid\n02:00 End"),   # valid
        v("b", description="01:00 Late\n02:00 Mid\n03:00 End"),    # no 00:00
        v("c", description="no chapters here"),
    ]
    assert bench.measure(videos)["chapters_pct"] == pytest.approx(100 / 3)


def test_stylistic_practices_are_reported_without_judging_you():
    """Telling someone their 6-minute video is "behind" a 22-minute one is
    advice, not measurement."""
    reference = [v(f"r{i}", duration=1800, title="x" * 60) for i in range(5)]
    yours = [v("m", duration=300, title="short title")]

    report = bench.compare(reference, yours, source="test")

    for key in ("title_chars", "duration_seconds"):
        practice = next(p for p in report.practices if p.key == key)
        assert practice.yours is None, f"{key} must not be scored"


def test_verdicts_reflect_the_gap():
    reference = [v(f"r{i}", tags=["a"] * 10, duration=600) for i in range(5)]

    on_par = bench.compare(reference, [v("m", tags=["a"] * 10, duration=600)],
                           source="t")
    behind = bench.compare(reference, [v("m", tags=["a"] * 6, duration=600)],
                           source="t")
    far = bench.compare(reference, [v("m", tags=[], duration=600)], source="t")

    verdict = lambda r: next(p for p in r.practices if p.key == "tag_count").verdict  # noqa: E731
    assert verdict(on_par) == "ok"
    assert verdict(behind) == "behind"
    assert verdict(far) == "far behind"


# --- topics -----------------------------------------------------------------


def test_topics_ignore_stopwords_and_singletons():
    videos = [v("a", title="The best way to build a Rust parser"),
              v("b", title="Building a Rust parser from scratch"),
              v("c", title="Why I moved to Rust")]

    topics = dict(bench.extract_topics(videos))

    assert "rust" in topics and topics["rust"] == 3
    assert "parser" in topics
    for stopword in ("the", "to", "a", "why", "best"):
        assert stopword not in topics


def test_gap_topics_are_a_set_difference_over_real_data():
    from pitstop.models import Catalog, Channel

    reference = [v(f"r{i}", title="Kubernetes operators explained")
                 for i in range(4)]
    reference += [v(f"s{i}", title="Rust parser deep dive") for i in range(3)]

    yours = Catalog(channel=Channel(id="UC0", title="Mine"),
                    videos=[v("m", title="Rust parser basics")])

    gaps = dict(bench.gap_topics(reference, yours))

    assert "kubernetes" in gaps
    assert "rust" not in gaps, "already covered — must not be suggested"


def test_gap_topics_with_no_channel_returns_trending_topics():
    reference = [v(f"r{i}", title="Kubernetes operators explained")
                 for i in range(4)]
    assert dict(bench.gap_topics(reference, None))


# --- enrich sanitising ------------------------------------------------------


def test_json_array_is_found_inside_prose_and_fences():
    assert _extract_json_array('```json\n["a", "b"]\n```') == ["a", "b"]
    assert _extract_json_array('Sure! Here you go: ["a"] Hope that helps.') == ["a"]
    assert _extract_json_array('[{"t": "00:00"}]') == [{"t": "00:00"}]


def test_malformed_llm_output_raises_rather_than_returning_junk():
    for bad in ("no array at all", "[unclosed", '{"not": "an array"}'):
        with pytest.raises(LLMUnavailable):
            _extract_json_array(bad)


@pytest.mark.parametrize("stamp,expected", [
    ("00:00", 0), ("01:30", 90), ("1:02:03", 3723), ("10:00", 600),
    ("not a time", None), ("", None), ("::", None), ("1:2:3:4", None),
])
def test_timestamp_parsing(stamp, expected):
    assert _to_seconds(stamp) == expected


@pytest.mark.parametrize("seconds,expected", [
    (0, "00:00"), (90, "01:30"), (3723, "1:02:03"), (-5, "00:00"),
])
def test_timestamp_formatting(seconds, expected):
    assert _fmt(seconds) == expected


def test_suggested_tags_are_sanitised_and_bounded(monkeypatch):
    """The model returns whatever it likes; the caller must never see junk."""
    from pitstop import enrich

    monkeypatch.setattr(enrich, "_chat", lambda *a, **k: (
        '["Good Tag", "#hashtag", "  spaced  out  ", "dup", "dup", '
        '"' + "x" * 60 + '", "", 42, "final"]'))

    tags = enrich.suggest_tags("Title", "Description", existing=["kept"])

    assert tags[0] == "kept", "existing tags must be preserved, first"
    assert "good tag" in tags
    assert "hashtag" in tags, "punctuation stripped, word kept"
    assert "spaced out" in tags
    assert tags.count("dup") == 1
    assert all(len(t) <= enrich.MAX_TAG_CHARS for t in tags)
    assert all(isinstance(t, str) and t for t in tags)


def test_tag_list_respects_youtubes_total_length_cap(monkeypatch):
    """YouTube rejects the whole videos.update if tags exceed ~500 chars, which
    surfaces as an error that looks unrelated to tags."""
    from pitstop import enrich

    monkeypatch.setattr(enrich, "_chat", lambda *a, **k: (
        "[" + ", ".join(f'"{"t" * 28}{i:02d}"' for i in range(20)) + "]"))

    tags = enrich.suggest_tags("T", "D")

    assert sum(len(t) + 1 for t in tags) <= enrich.MAX_TOTAL_TAG_CHARS


def test_no_usable_tags_raises_rather_than_returning_empty(monkeypatch):
    from pitstop import enrich

    monkeypatch.setattr(enrich, "_chat", lambda *a, **k: '["", "  ", 5]')

    with pytest.raises(LLMUnavailable):
        enrich.suggest_tags("T", "D")


def test_chapters_require_a_transcript():
    from pitstop.enrich import suggest_chapters

    with pytest.raises(LLMUnavailable, match="no transcript"):
        suggest_chapters("Title", "   ", 600)


def test_chapters_are_forced_to_youtubes_rules(monkeypatch):
    """A list violating YouTube's rules renders as *no* chapters, which would
    look like the fix silently did nothing."""
    from pitstop import enrich

    monkeypatch.setattr(enrich, "_chat", lambda *a, **k: (
        '[{"t": "00:30", "label": "Late start"},'
        ' {"t": "00:35", "label": "Too close"},'
        ' {"t": "02:00", "label": "Middle"},'
        ' {"t": "05:00", "label": "End"},'
        ' {"t": "99:00", "label": "Past the end"}]'))

    chapters = enrich.suggest_chapters("T", "transcript text", 600)

    assert chapters[0][0] == "00:00", "first chapter must be 00:00"
    assert len(chapters) >= 3
    seconds = [_to_seconds(s) for s, _ in chapters]
    assert seconds == sorted(seconds)
    assert all(b - a >= 10 for a, b in zip(seconds, seconds[1:]))
    assert all(s < 600 for s in seconds), "nothing past the video's length"


def test_too_few_valid_chapters_raises(monkeypatch):
    from pitstop import enrich

    monkeypatch.setattr(enrich, "_chat", lambda *a, **k:
                        '[{"t": "00:00", "label": "Only one"}]')

    with pytest.raises(LLMUnavailable, match="valid chapters"):
        enrich.suggest_chapters("T", "transcript", 600)


def test_rendered_chapters_parse_back_as_valid():
    from pitstop.enrich import render_chapters
    from pitstop.models import Video

    rendered = render_chapters([("00:00", "Intro"), ("02:00", "Body"),
                                ("05:00", "End")])
    video = v(description=rendered)
    assert video.has_valid_chapters
