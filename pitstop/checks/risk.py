"""Ad-suitability risk in title and description.

READ THIS BEFORE CHANGING ANYTHING HERE.

This check does **not** predict what YouTube's classifier will do. It cannot.
Nobody outside YouTube can, the classifier is not public, and a tool that
claimed otherwise would be lying to creators about their income.

What it does instead: it is a **guideline-grounded audit**. Every flag names the
specific published advertiser-friendly guideline category it maps to, quotes
the text that triggered it, and says plainly that this is a *conflict worth
reviewing*, not a verdict. The creator makes the call.

The distinction matters practically, not just ethically. A creator who trusts a
false "you're safe" signal and uploads is worse off than one who was never told
anything. So the output is deliberately shaped as "here is text that conflicts
with clause X, decide for yourself" and the summary never renders a green
all-clear — only "N items to review" or "nothing matched".

Scope is honest too: this reads title and description only. It does not watch
the video. Spoken-word and visual analysis would need transcription plus a
vision pass over keyframes; that is scoped in ARCHITECTURE.md as future work
and is NOT claimed anywhere in the UI.

Categories below follow YouTube's published advertiser-friendly content
guidelines. The patterns are intentionally narrow — high precision, low recall.
A noisy version of this check would get ignored, which is the worst outcome.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..models import Catalog, Finding, Severity
from .base import BaseCheck, CheckContext, register


@dataclass(frozen=True)
class Policy:
    key: str
    label: str          # the published guideline category name
    note: str           # what the guideline actually says, in short
    patterns: tuple[re.Pattern, ...]
    severity: Severity


def _p(*words: str) -> tuple[re.Pattern, ...]:
    r"""Compile word-boundary patterns.

    The boundary is only added where it can actually match. `\b` asserts a
    transition between a word and a non-word character, so `\b#ad\b` never
    matches "#ad" at the start of a string — both `^` and `#` are non-word,
    so there is no transition. Blanket-wrapping every pattern in `\b` silently
    broke the single most important disclosure marker there is.
    """
    out = []
    for word in words:
        prefix = r"\b" if word[0].isalnum() or word[0] == "_" else ""
        suffix = r"\b" if word[-1].isalnum() or word[-1] == "_" else ""
        out.append(re.compile(rf"{prefix}{word}{suffix}", re.I))
    return tuple(out)


POLICIES: tuple[Policy, ...] = (
    Policy(
        key="profanity",
        label="Inappropriate language",
        note=("Frequent strong profanity in the title or the first lines of a "
              "description can limit ads, and profanity in the first ~30 "
              "seconds is weighted more heavily."),
        patterns=_p(r"f+u+c+k+\w*", r"sh[i1]t+\w*", r"b[i1]tch\w*",
                    r"c+u+n+t+", r"a+s+s+h+o+l+e+", r"d[i1]ck head",
                    r"mother\s?f\w+"),
        severity=Severity.WARNING,
    ),
    Policy(
        key="violence",
        label="Violence and graphic content",
        note=("Descriptions of graphic injury, death or real-world violence "
              "may restrict advertising even when the framing is educational."),
        patterns=_p("gore", "graphic footage", "brutal(?:ly)? killed",
                    "beheading", "execution video", "dead body", "corpse",
                    "massacre", "shooting spree"),
        severity=Severity.WARNING,
    ),
    Policy(
        key="controversial",
        label="Controversial issues and sensitive events",
        note=("Terrorism, war, abuse, and major tragedies are treated as "
              "sensitive regardless of the creator's stance or intent."),
        patterns=_p("terrorist attack", "school shooting", "genocide",
                    "war crime", "child abuse", "sexual assault", "suicide",
                    "self.?harm"),
        severity=Severity.CRITICAL,
    ),
    Policy(
        key="adult",
        label="Adult content",
        note=("Sexually gratifying framing, nudity references and explicit "
              "sexual language restrict advertising."),
        patterns=_p("nsfw", "nude", "naked", "porn\\w*", "onlyfans",
                    "sex tape", "strip(?:ping|per)"),
        severity=Severity.CRITICAL,
    ),
    Policy(
        key="drugs",
        label="Recreational drugs and drug-related content",
        note=("Promotion or depiction of recreational drug and regulated "
              "substance use restricts advertising."),
        patterns=_p("cocaine", "heroin", "meth(?:amphetamine)?", "how to roll",
                    "getting high", "weed haul", "vape review", "bong"),
        severity=Severity.WARNING,
    ),
    Policy(
        key="firearms",
        label="Firearms-related content",
        note=("Sale, assembly or modification of firearms and accessories is "
              "restricted."),
        patterns=_p("ghost gun", "3d printed gun", "full auto conversion",
                    "silencer build", "how to build a (?:gun|rifle)",
                    "ammo (?:deal|discount)"),
        severity=Severity.CRITICAL,
    ),
    Policy(
        key="shocking",
        label="Shocking content",
        note="Content intended to shock or disgust restricts advertising.",
        patterns=_p("disgusting", "you won.?t believe what happened",
                    "gross out", "vomit", "worst injury"),
        severity=Severity.NOTICE,
    ),
    Policy(
        key="misinfo",
        label="Demonstrably false claims",
        note=("Claims contradicting well-established consensus on health, "
              "elections or historical events restrict advertising."),
        patterns=_p("miracle cure", "doctors hate", "cure for cancer",
                    "vaccine causes", "flat earth", "rigged election"),
        severity=Severity.WARNING,
    ),
)

# Paid-promotion disclosure. Not an ad-suitability rule — a legal one (FTC in
# the US, ASCI in India) and a YouTube policy requirement. Cheap to check,
# expensive to get wrong.
_SPONSOR_HINTS = _p("sponsor(?:ed|ship)?", "paid partnership", "brand deal",
                    "thanks to .{0,30} for sponsoring", "use code",
                    "discount code", "affiliate link")
_DISCLOSURE_HINTS = _p("#ad", "#sponsored", "paid promotion",
                       "includes paid promotion", "advertisement",
                       "commissions? (?:from|on)", "affiliate")


@register
class AdSuitabilityCheck(BaseCheck):
    id = "risk.ad_suitability"
    name = "Ad-suitability review items"
    description = ("Text in the title or description that conflicts with a "
                   "published advertiser-friendly guideline. Every flag cites "
                   "the guideline. This is a review prompt, not a prediction "
                   "of YouTube's decision.")

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        for video in catalog.videos:
            haystack = f"{video.title}\n{video.description}"
            for policy in POLICIES:
                hits = _matches(policy, haystack)
                if not hits:
                    continue
                in_title = any(p.search(video.title) for p in policy.patterns)
                severity = (Severity.CRITICAL
                            if in_title and policy.severity != Severity.NOTICE
                            else policy.severity)
                yield Finding(
                    check_id=self.id,
                    severity=severity,
                    title=f"Review: {policy.label}",
                    detail=(f"matched {_quote(hits)}"
                            f"{' in the title' if in_title else ''} — "
                            f"{policy.note}"),
                    video_id=video.id,
                    impact_views=int(video.views_per_day * 30),
                    evidence={"policy": policy.key,
                              "guideline": policy.label,
                              "matches": hits,
                              "in_title": in_title,
                              "is_prediction": False},
                )


@register
class MissingDisclosureCheck(BaseCheck):
    id = "risk.disclosure"
    name = "Sponsorship without disclosure"
    description = ("A description that reads as sponsored or affiliate but "
                   "carries no disclosure. This is a legal requirement in most "
                   "jurisdictions, not a style preference.")

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        for video in catalog.videos:
            text = f"{video.title}\n{video.description}"
            sponsor_hits = [m.group(0) for p in _SPONSOR_HINTS
                            if (m := p.search(text))]
            if not sponsor_hits:
                continue
            if any(p.search(text) for p in _DISCLOSURE_HINTS):
                continue
            yield Finding(
                check_id=self.id,
                severity=Severity.CRITICAL,
                title="Sponsorship with no disclosure",
                detail=(f"description mentions {_quote(sponsor_hits)} but "
                        f"contains no #ad / paid-promotion disclosure"),
                video_id=video.id,
                impact_views=int(video.views_per_day * 30),
                evidence={"matches": sponsor_hits},
                # No auto-fix. Adding a legal disclosure on a creator's behalf,
                # to a video whose sponsorship terms we cannot see, is not a
                # decision a tool gets to make.
                fix=None,
            )


def _matches(policy: Policy, text: str) -> list[str]:
    found: list[str] = []
    for pattern in policy.patterns:
        m = pattern.search(text)
        if m:
            found.append(m.group(0))
    return found


def _quote(items: list[str]) -> str:
    shown = ", ".join(f'"{i}"' for i in items[:3])
    extra = f" (+{len(items) - 3} more)" if len(items) > 3 else ""
    return shown + extra
