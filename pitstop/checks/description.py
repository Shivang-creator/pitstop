"""Description quality: chapters, length, and the standard footer.

Chapters are the highest-value item here. They are pure text in the
description, they cost one videos.update to add, and YouTube uses them to
surface a video for specific searches and to render the segmented progress bar.
The rules YouTube actually enforces:

  * at least three timestamps
  * the first must be 00:00
  * each chapter at least 10 seconds long
  * listed in ascending order

Pitstop enforces all four, because a description with two timestamps or one
starting at 01:20 renders no chapters at all and the creator usually has no
idea. That is a silent failure worth catching on its own — several videos in a
typical catalog have *almost* valid chapters.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..models import Catalog, Finding, Fix, Severity, _to_seconds
from .base import BaseCheck, CheckContext, register

_TIMESTAMP_LINE = re.compile(
    r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s+(\S.*)$", re.M)

MIN_DESCRIPTION_CHARS = 250
MIN_CHAPTERS = 3
MIN_CHAPTER_GAP_SECONDS = 10


@register
class MissingChaptersCheck(BaseCheck):
    id = "description.no_chapters"
    name = "No chapters in description"
    description = ("Chapters let YouTube surface a video for specific searches "
                   "and render the segmented progress bar. They are plain text "
                   "in the description and cost one edit to add.")

    # Long videos benefit most; a 90-second Short does not need chapters.
    MIN_DURATION = 120

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        for video in catalog.videos:
            if video.duration_seconds and video.duration_seconds < self.MIN_DURATION:
                continue
            if video.has_valid_chapters:
                continue

            stamps = video.chapter_timestamps
            if stamps:
                continue  # handled by BrokenChaptersCheck — don't double-report

            yield Finding(
                check_id=self.id,
                severity=Severity.WARNING,
                title="No chapters",
                detail=(f"{_fmt_duration(video.duration_seconds)} video with no "
                        f"timestamp list"),
                video_id=video.id,
                impact_views=int(video.views_per_day * 30),
                evidence={"duration_seconds": video.duration_seconds},
                # Generating good chapters needs the transcript, which needs an
                # LLM pass — that happens in enrich.py during `plan`, not here.
                # A check stays cheap and offline; enrichment is opt-in.
                fix=None,
            )


@register
class BrokenChaptersCheck(BaseCheck):
    id = "description.broken_chapters"
    name = "Chapters present but not rendering"
    description = ("Timestamps that YouTube silently rejects: fewer than three, "
                   "not starting at 00:00, out of order, or less than 10s "
                   "apart. The creator thinks they have chapters; viewers see "
                   "none.")

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        for video in catalog.videos:
            matches = _TIMESTAMP_LINE.findall(video.description)
            if not matches:
                continue
            if video.has_valid_chapters and _gaps_ok(matches):
                continue

            reason = _why_invalid(matches)
            if not reason:
                continue

            fixed = _repair_chapters(video.description, matches)
            yield Finding(
                check_id=self.id,
                severity=Severity.WARNING,
                title="Chapters present but not rendering",
                detail=reason,
                video_id=video.id,
                impact_views=int(video.views_per_day * 30),
                evidence={"timestamps": [m[0] for m in matches],
                          "reason": reason},
                fix=(Fix(field="description", current=video.description,
                         proposed=fixed,
                         note="prepend 00:00 chapter so the list renders")
                     if fixed and fixed != video.description else None),
            )


@register
class ThinDescriptionCheck(BaseCheck):
    id = "description.thin"
    name = "Thin or empty description"
    description = ("Descriptions are indexed text. An empty one gives YouTube "
                   "nothing to match a search against.")

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        for video in catalog.videos:
            length = len(video.description.strip())
            if length >= MIN_DESCRIPTION_CHARS:
                continue
            severity = Severity.CRITICAL if length == 0 else Severity.WARNING
            yield Finding(
                check_id=self.id,
                severity=severity,
                title="Empty description" if length == 0 else "Thin description",
                detail=f"{length} characters (target: {MIN_DESCRIPTION_CHARS}+)",
                video_id=video.id,
                impact_views=int(video.views_per_day * 30),
                evidence={"length": length},
            )


@register
class MissingFooterCheck(BaseCheck):
    id = "description.footer"
    name = "Missing standard footer"
    description = ("Your subscribe link, socials and channel boilerplate should "
                   "appear on every video. Configure the expected block in "
                   "pitstop.yaml and Pitstop will append it where it's missing.")

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        footer = (ctx.rules or {}).get("description_footer")
        if not footer:
            return  # nothing configured — not a finding, just not applicable

        marker = (ctx.rules or {}).get("footer_marker") or _footer_marker(footer)
        if not marker:
            return

        for video in catalog.videos:
            if marker in video.description:
                continue
            proposed = video.description.rstrip() + "\n\n" + footer.strip() + "\n"
            yield Finding(
                check_id=self.id,
                severity=Severity.NOTICE,
                title="Missing standard footer",
                detail=f'"{marker.strip()[:48]}" not found in description',
                video_id=video.id,
                impact_views=int(video.views_per_day * 30),
                evidence={"marker": marker},
                fix=Fix(field="description", current=video.description,
                        proposed=proposed, note="append standard footer"),
            )


# --- helpers ---------------------------------------------------------------


def _footer_marker(footer: str) -> str:
    """Pick the line that reliably proves the footer is present.

    Naively taking the first line breaks on the most common footer shape there
    is, because that line is usually a decorative rule:

        ── ── ──
        Subscribe: https://youtube.com/@me

    "── ── ──" is not distinctive — any description using a separator would
    look like it already has the footer, and the check would silently pass on
    every video it should have flagged.

    So: prefer the first line containing a URL (a link is as distinctive as
    text gets), then the longest line with real word characters, and only fall
    back to the first line if the footer is nothing but punctuation.
    """
    lines = [line.strip() for line in footer.strip().splitlines() if line.strip()]
    if not lines:
        return ""

    for line in lines:
        if "http://" in line or "https://" in line:
            return line

    wordy = [line for line in lines if re.search(r"\w{3,}", line)]
    if wordy:
        return max(wordy, key=len)

    return lines[0]


def _gaps_ok(matches: list[tuple[str, str]]) -> bool:
    seconds = [_to_seconds(m[0]) for m in matches]
    return all(b - a >= MIN_CHAPTER_GAP_SECONDS
               for a, b in zip(seconds, seconds[1:]))


def _why_invalid(matches: list[tuple[str, str]]) -> str | None:
    seconds = [_to_seconds(m[0]) for m in matches]
    if len(matches) < MIN_CHAPTERS:
        return (f"only {len(matches)} timestamp(s) — YouTube needs "
                f"{MIN_CHAPTERS}+ to render chapters")
    if seconds[0] != 0:
        return f"first timestamp is {matches[0][0]}, must be 00:00"
    if seconds != sorted(seconds):
        return "timestamps are out of order"
    if not _gaps_ok(matches):
        return "some chapters are shorter than the 10s minimum"
    return None


def _repair_chapters(description: str,
                     matches: list[tuple[str, str]]) -> str | None:
    """Only repair the one case we can fix without inventing content.

    A missing 00:00 opener is mechanical: prepend one line. Anything else
    (too few chapters, bad gaps) needs the creator to decide what the sections
    actually are, so we report it and stop. Guessing chapter titles here would
    put words in the creator's mouth.
    """
    seconds = [_to_seconds(m[0]) for m in matches]
    if len(matches) >= MIN_CHAPTERS and seconds[0] != 0 and seconds == sorted(seconds):
        first_line = f"{matches[0][0]} {matches[0][1]}"
        idx = description.find(first_line)
        if idx == -1:
            return None
        return description[:idx] + "00:00 Intro\n" + description[idx:]
    return None


def _fmt_duration(seconds: int) -> str:
    if not seconds:
        return "unknown-length"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
