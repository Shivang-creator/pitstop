"""Tags, category, captions, title hygiene, and stale high-traffic videos.

The last check in this file — StaleWinnerCheck — is the one that makes the
whole report feel like it understands the channel rather than just linting it.
It finds videos that are *still pulling real traffic* years after publication
and haven't been touched since. Those are the highest-return repairs on the
entire channel: the audience is already arriving, and every other finding on
that video is compounding daily. It exists to drive ranking, so the report
leads with "fix these four videos" instead of "here are 130 problems".
"""

from __future__ import annotations

import re
from typing import Iterable

from ..models import Catalog, Finding, Fix, Severity
from .base import BaseCheck, CheckContext, register

MIN_TAGS = 5
MAX_TITLE_CHARS = 70          # beyond this, YouTube truncates in most surfaces
DEFAULT_CATEGORY = "22"       # "People & Blogs" — the upload-form default

# Titles that betray an unedited export or a placeholder.
_LAZY_TITLE_PATTERNS = [
    (re.compile(r"^(VID|IMG|MOV|DSC|GX|GOPR)[-_ ]?\d+", re.I), "camera filename"),
    (re.compile(r"^(untitled|new video|final|final ?2|test|draft)\b", re.I),
     "placeholder"),
    (re.compile(r"\.(mp4|mov|avi|mkv)$", re.I), "file extension left in"),
    (re.compile(r"^\s*$"), "empty"),
]


@register
class ThinTagsCheck(BaseCheck):
    id = "metadata.tags"
    name = "Too few tags"
    description = ("Tags help YouTube place a video against related content, "
                   "especially for terms your title doesn't spell out.")
    requires_owner = True   # tags are only returned to the video owner

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        for video in catalog.videos:
            if len(video.tags) >= MIN_TAGS:
                continue
            yield Finding(
                check_id=self.id,
                severity=Severity.NOTICE if video.tags else Severity.WARNING,
                title="No tags" if not video.tags else "Too few tags",
                detail=f"{len(video.tags)} tag(s), target {MIN_TAGS}+",
                video_id=video.id,
                impact_views=int(video.views_per_day * 30),
                evidence={"tags": video.tags},
            )


@register
class DefaultCategoryCheck(BaseCheck):
    id = "metadata.category"
    name = "Category left at the default"
    description = ("Every video sitting in 'People & Blogs' is a video YouTube "
                   "has less signal about. Set it deliberately per series.")

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        target = (ctx.rules or {}).get("default_category_id")
        if not catalog.videos:
            return
        on_default = [v for v in catalog.videos
                      if v.category_id == DEFAULT_CATEGORY]
        # Only worth reporting if it looks unintentional — i.e. the channel is
        # not actually a People & Blogs channel.
        if len(on_default) == len(catalog.videos) and not target:
            return

        for video in on_default:
            yield Finding(
                check_id=self.id,
                severity=Severity.NOTICE,
                title="Category left at upload default",
                detail="category is 'People & Blogs' (22)",
                video_id=video.id,
                impact_views=int(video.views_per_day * 30),
                evidence={"category_id": video.category_id},
                fix=(Fix(field="categoryId", current=video.category_id,
                         proposed=str(target),
                         note=f"set category to {target}")
                     if target else None),
            )


@register
class LazyTitleCheck(BaseCheck):
    id = "metadata.lazy_title"
    name = "Placeholder or filename title"
    description = ("Titles that are still a camera filename, an editor "
                   "placeholder, or carry a file extension.")

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        for video in catalog.videos:
            for pattern, label in _LAZY_TITLE_PATTERNS:
                if not pattern.search(video.title):
                    continue
                yield Finding(
                    check_id=self.id,
                    severity=Severity.CRITICAL,
                    title="Placeholder title",
                    detail=f'"{video.title}" looks like a {label}',
                    video_id=video.id,
                    impact_views=int(video.views_per_day * 30),
                    evidence={"pattern": label},
                )
                break


@register
class LongTitleCheck(BaseCheck):
    id = "metadata.long_title"
    name = "Title truncated in search"
    description = (f"Titles beyond ~{MAX_TITLE_CHARS} characters get cut off "
                   "in search, suggestions and mobile, where most views come "
                   "from.")

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        for video in catalog.videos:
            if len(video.title) <= MAX_TITLE_CHARS:
                continue
            yield Finding(
                check_id=self.id,
                severity=Severity.NOTICE,
                title="Title will be truncated",
                detail=(f"{len(video.title)} chars — cut at ~{MAX_TITLE_CHARS} "
                        f'("…{video.title[MAX_TITLE_CHARS - 12:MAX_TITLE_CHARS]}|")'),
                video_id=video.id,
                impact_views=int(video.views_per_day * 30),
                evidence={"length": len(video.title)},
            )


@register
class MissingCaptionsCheck(BaseCheck):
    id = "metadata.captions"
    name = "No captions"
    description = ("Captions are indexed by YouTube search and are the entire "
                   "experience for deaf viewers and the ~80% who watch muted.")

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        for video in catalog.videos:
            if video.caption_available is None:
                continue  # unknown — public scan can't tell, so don't guess
            if video.caption_available:
                continue
            yield Finding(
                check_id=self.id,
                severity=Severity.WARNING,
                title="No captions",
                detail="no caption track available on this video",
                video_id=video.id,
                impact_views=int(video.views_per_day * 30),
                evidence={},
            )


@register
class StaleWinnerCheck(BaseCheck):
    id = "metadata.stale_winner"
    name = "High-traffic video gone stale"
    description = ("Videos still pulling meaningful traffic that haven't been "
                   "touched in over a year. Every other problem on these is "
                   "compounding daily — fix these first.")

    MIN_AGE_DAYS = 365
    # A video counts as "still pulling" if its lifetime views/day is above the
    # channel's median views/day. Relative, so it works on a 200-sub channel
    # and a 2M-sub channel alike.
    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        candidates = [v for v in catalog.videos
                      if v.age_days >= self.MIN_AGE_DAYS
                      and v.privacy_status == "public"]
        if not candidates:
            return

        rates = sorted(v.views_per_day for v in catalog.videos)
        if not rates:
            return
        median_rate = rates[len(rates) // 2]
        if median_rate <= 0:
            return

        for video in candidates:
            if video.views_per_day < median_rate:
                continue
            years = video.age_days / 365
            yield Finding(
                check_id=self.id,
                severity=Severity.WARNING,
                title="High-traffic video gone stale",
                detail=(f"{years:.1f} years old, still ~"
                        f"{video.views_per_day:.0f} views/day"),
                video_id=video.id,
                impact_views=int(video.views_per_day * 30),
                evidence={"age_days": video.age_days,
                          "views_per_day": round(video.views_per_day, 1),
                          "median_views_per_day": round(median_rate, 1)},
            )
