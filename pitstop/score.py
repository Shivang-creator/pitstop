"""Health score and impact ranking.

A score is a lie if you can't explain it, so this one is deliberately simple
enough to reconstruct by hand:

    penalty(finding) = severity.weight × traffic_multiplier(finding)

    traffic_multiplier scales 1.0 → 3.0 with the share of channel traffic the
    affected video represents. A dead link on a video nobody watches is a
    hygiene issue. The same dead link on the video carrying 40% of the
    channel's traffic is an emergency. Same finding, different cost.

    score = 100 × (1 − min(1, penalty_per_video / ZERO_POINT))

Dividing by video count is what makes the number comparable between a 4-video
channel and a 900-video one. Without it a big channel would always look worse
purely for being big, which would make the score useless for the back-catalog
work this tool exists to do.

ZERO_POINT is the one tuned constant in the product, so it is stated in the
open rather than buried: **45 penalty points per video scores 0**. Since a
CRITICAL finding at median traffic is worth ~13 points, that means a channel
whose average video carries three to four critical problems scores zero. A
channel with a couple of NOTICE-level issues per video lands in the low 90s.

Those two anchor points were chosen to make the middle of the range useful —
an earlier version normalised against "every video fails every check", which
is so pessimistic a denominator that genuinely broken channels scored in the
high 80s and the number carried no information.

Every score the UI shows expands into the findings that produced it. There is
no hidden term.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Catalog, Finding, Severity

# Which checks roll up into which headline category, for the UI breakdown.
CATEGORIES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "money": ("Money leaks", "Revenue and links walking out the door",
              ("links.dead", "links.self_rot", "links.shortener")),
    "discovery": ("Discovery", "How findable the catalog is",
                  ("playlist.orphan", "playlist.missing_series",
                   "description.no_chapters", "description.broken_chapters",
                   "description.thin", "metadata.tags", "metadata.captions",
                   "metadata.long_title", "metadata.category")),
    "hygiene": ("Hygiene", "Consistency and housekeeping",
                ("description.footer", "metadata.lazy_title",
                 "playlist.broken_items", "metadata.stale_winner")),
    "risk": ("Risk", "Ad-suitability and disclosure items to review",
             ("risk.ad_suitability", "risk.disclosure")),
}

TRAFFIC_MULTIPLIER_MAX = 3.0

# Penalty-per-video at which a channel scores 0. See the module docstring —
# this is the single tuned constant and it is deliberately visible.
ZERO_POINT = 45.0


@dataclass
class CategoryScore:
    key: str
    label: str
    description: str
    score: int
    findings: int
    critical: int
    penalty: float
    max_penalty: float


@dataclass
class ScoreReport:
    score: int
    grade: str
    total_findings: int
    critical: int
    warning: int
    notice: int
    auto_fixable: int
    affected_videos: int
    categories: list[CategoryScore] = field(default_factory=list)
    # Videos ranked by total penalty — "fix these first".
    priority_videos: list[dict] = field(default_factory=list)

    @property
    def headline(self) -> str:
        if self.score >= 90:
            return "This channel is in good shape."
        if self.score >= 70:
            return "A few real problems, mostly hygiene."
        if self.score >= 50:
            return "Meaningful traffic and revenue is leaking."
        return "The back catalog needs serious work."


def grade_for(score: int) -> str:
    for threshold, letter in ((90, "A"), (80, "B"), (70, "C"),
                              (60, "D"), (0, "F")):
        if score >= threshold:
            return letter
    return "F"


def _traffic_multiplier(finding: Finding, median_monthly_views: float) -> float:
    """Scale a finding's cost by how much traffic its video actually gets.

    Measured against the channel's **median** video, not against total channel
    traffic. That distinction matters: share-of-total makes every video on a
    10-video channel look like 10% of the channel and every video on a
    200-video channel look like 0.5%, so identical problems scored differently
    purely because of catalog size. Median-relative is a within-channel
    measure, which is what was actually intended.

        below median   0.5 → 1.0   (a video nobody watches is hygiene)
        at median      1.0
        10× median     3.0         (the channel's main earner)

    Logarithmic above the median, so a single viral video cannot drown out 200
    genuinely broken ones — which is exactly the back-catalog work this tool
    exists to support.
    """
    if median_monthly_views <= 0:
        return 1.0
    ratio = finding.impact_views / median_monthly_views
    if ratio <= 0:
        return 0.5
    if ratio < 1.0:
        return 0.5 + 0.5 * ratio
    import math

    return 1.0 + (TRAFFIC_MULTIPLIER_MAX - 1.0) * min(1.0, math.log10(ratio))


def _median_monthly_views(catalog: Catalog) -> float:
    monthly = sorted(int(v.views_per_day * 30) for v in catalog.videos)
    if not monthly:
        return 0.0
    mid = len(monthly) // 2
    return float(monthly[mid] if len(monthly) % 2
                 else (monthly[mid - 1] + monthly[mid]) / 2)


def _hydrate_impact(catalog: Catalog, findings: list[Finding]) -> None:
    """Fill in impact_views from the catalog.

    Both compute() and rank() need this, and rank() used to rely on compute()
    having been called first — so ranking silently degraded to arbitrary order
    for any caller that ranked without scoring. Idempotent, so calling it twice
    is free.
    """
    for finding in findings:
        if finding.impact_views or not finding.video_id:
            continue
        video = catalog.video(finding.video_id)
        if video:
            finding.impact_views = int(video.views_per_day * 30)


def compute(catalog: Catalog, findings: list[Finding]) -> ScoreReport:
    video_count = max(1, len(catalog.videos))
    _hydrate_impact(catalog, findings)
    median_monthly = _median_monthly_views(catalog)

    # --- per-category penalties --------------------------------------------
    check_to_category = {
        check_id: key
        for key, (_, _, ids) in CATEGORIES.items()
        for check_id in ids
    }

    penalties: dict[str, float] = {k: 0.0 for k in CATEGORIES}
    counts: dict[str, int] = {k: 0 for k in CATEGORIES}
    criticals: dict[str, int] = {k: 0 for k in CATEGORIES}

    for finding in findings:
        category = check_to_category.get(finding.check_id, "hygiene")
        penalty = finding.severity.weight * _traffic_multiplier(
            finding, median_monthly)
        penalties[category] += penalty
        counts[category] += 1
        if finding.severity is Severity.CRITICAL:
            criticals[category] += 1

    categories: list[CategoryScore] = []
    total_penalty = 0.0

    # Each category is scored against its own share of the zero point, weighted
    # by how many checks feed it. A category with nine checks can accumulate
    # more penalty than one with two, so it gets proportionally more headroom.
    total_checks = sum(len(ids) for _, _, ids in CATEGORIES.values())

    for key, (label, description, check_ids) in CATEGORIES.items():
        share = len(check_ids) / total_checks
        zero_at = ZERO_POINT * share * video_count
        penalty = penalties[key]
        ratio = min(1.0, penalty / zero_at) if zero_at else 0.0
        categories.append(CategoryScore(
            key=key, label=label, description=description,
            score=int(round(100 * (1 - ratio))),
            findings=counts[key], critical=criticals[key],
            penalty=round(penalty, 1), max_penalty=round(zero_at, 1),
        ))
        total_penalty += penalty

    penalty_per_video = total_penalty / video_count
    score = int(round(100 * (1 - min(1.0, penalty_per_video / ZERO_POINT))))

    # --- priority videos ----------------------------------------------------
    per_video: dict[str, float] = {}
    per_video_count: dict[str, int] = {}
    for finding in findings:
        if not finding.video_id:
            continue
        p = finding.severity.weight * _traffic_multiplier(finding, median_monthly)
        per_video[finding.video_id] = per_video.get(finding.video_id, 0.0) + p
        per_video_count[finding.video_id] = per_video_count.get(
            finding.video_id, 0) + 1

    priority = []
    for video_id, penalty in sorted(per_video.items(),
                                    key=lambda kv: -kv[1])[:10]:
        video = catalog.video(video_id)
        if not video:
            continue
        priority.append({
            "video_id": video_id,
            "title": video.title,
            "issues": per_video_count[video_id],
            "penalty": round(penalty, 1),
            "views_per_month": int(video.views_per_day * 30),
            "thumbnail_url": video.thumbnail_url,
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })

    return ScoreReport(
        score=score,
        grade=grade_for(score),
        total_findings=len(findings),
        critical=sum(1 for f in findings if f.severity is Severity.CRITICAL),
        warning=sum(1 for f in findings if f.severity is Severity.WARNING),
        notice=sum(1 for f in findings if f.severity is Severity.NOTICE),
        auto_fixable=sum(1 for f in findings if f.auto_fixable),
        affected_videos=len({f.video_id for f in findings if f.video_id}),
        categories=categories,
        priority_videos=priority,
    )


def rank(findings: list[Finding], catalog: Catalog) -> list[Finding]:
    """Most-worth-fixing first: severity, then traffic, then fixability."""
    _hydrate_impact(catalog, findings)
    median_monthly = _median_monthly_views(catalog)
    return sorted(
        findings,
        key=lambda f: (
            -f.severity.weight * _traffic_multiplier(f, median_monthly),
            -f.impact_views,
            not f.auto_fixable,
            f.check_id,
        ),
    )
