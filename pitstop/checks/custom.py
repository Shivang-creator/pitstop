"""User-defined rules from pitstop.yaml — the linter half of the product.

Built-in checks encode what is true for every channel. Custom rules encode what
is true for *your* channel: your naming convention, your required tags, your
series conventions. Same Finding pipeline, so custom rules get scored, ranked
and auto-fixed exactly like built-ins.

    # pitstop.yaml
    rules:
      - id: tutorials-tagged
        name: Tutorial videos carry the tutorial tag
        when:
          title_matches: "(?i)tutorial|how to"
        require:
          has_tag: tutorial
        severity: warning
        fix: add_tag          # optional; omit for report-only

      - id: series-prefix
        name: Series videos use the "Series | " prefix
        when:
          in_playlist: "Deep Dives"
        require:
          title_matches: "^Deep Dives \\\\| "
        severity: notice

`when` selects the videos a rule applies to; `require` is what must hold. Both
support the same predicates, so a rule is just two predicate sets and a verdict.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from ..models import Catalog, Finding, Fix, Severity, Video
from .base import BaseCheck, CheckContext, register

# predicate name -> (video, catalog, expected) -> bool
PREDICATES: dict[str, Callable[[Video, Catalog, Any], bool]] = {
    "title_matches":
        lambda v, c, x: bool(re.search(str(x), v.title)),
    "description_matches":
        lambda v, c, x: bool(re.search(str(x), v.description)),
    "description_contains":
        lambda v, c, x: str(x) in v.description,
    "has_tag":
        lambda v, c, x: str(x).lower() in {t.lower() for t in v.tags},
    "min_tags":
        lambda v, c, x: len(v.tags) >= int(x),
    "min_description_chars":
        lambda v, c, x: len(v.description.strip()) >= int(x),
    "has_chapters":
        lambda v, c, x: v.has_valid_chapters == bool(x),
    "in_playlist":
        lambda v, c, x: any(v.id in p.item_video_ids
                            for p in c.playlists
                            if p.title.lower() == str(x).lower()),
    "in_any_playlist":
        lambda v, c, x: (v.id in c.videos_in_playlists) == bool(x),
    "category_id":
        lambda v, c, x: v.category_id == str(x),
    "privacy":
        lambda v, c, x: v.privacy_status == str(x),
    "longer_than_seconds":
        lambda v, c, x: v.duration_seconds > int(x),
    "published_after":
        lambda v, c, x: v.published_at.isoformat() > str(x),
}


class RuleError(ValueError):
    pass


def _evaluate(clause: dict[str, Any], video: Video,
              catalog: Catalog) -> bool:
    """All predicates in a clause must hold (AND)."""
    for name, expected in (clause or {}).items():
        fn = PREDICATES.get(name)
        if fn is None:
            raise RuleError(
                f"Unknown predicate {name!r}. Known: "
                f"{', '.join(sorted(PREDICATES))}")
        try:
            if not fn(video, catalog, expected):
                return False
        except re.error as exc:
            raise RuleError(f"Bad regex in {name!r}: {exc}") from exc
    return True


def _build_fix(rule: dict[str, Any], video: Video) -> Fix | None:
    """Only mechanical, reversible repairs are offered.

    `add_tag` and `append_description` are safe: they add, never overwrite.
    Anything that would rewrite a creator's title is report-only by design —
    `plan` review does not scale if the tool is rewriting prose.
    """
    action = rule.get("fix")
    require = rule.get("require", {})
    if not action:
        return None

    if action == "add_tag":
        tag = require.get("has_tag")
        if not tag:
            return None
        return Fix(field="tags", current=list(video.tags),
                   proposed=list(video.tags) + [str(tag)],
                   note=f'add tag "{tag}"')

    if action == "append_description":
        text = rule.get("fix_text") or require.get("description_contains")
        if not text:
            return None
        return Fix(field="description", current=video.description,
                   proposed=video.description.rstrip() + "\n\n" + str(text),
                   note="append required text")

    if action == "set_category":
        target = require.get("category_id")
        if not target:
            return None
        return Fix(field="categoryId", current=video.category_id,
                   proposed=str(target), note=f"set category {target}")

    raise RuleError(
        f"Unknown fix action {action!r}. "
        "Known: add_tag, append_description, set_category")


@register
class CustomRulesCheck(BaseCheck):
    id = "custom.rules"
    name = "Custom rules (pitstop.yaml)"
    description = ("Your own channel conventions, enforced across the whole "
                   "catalog.")

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        rules = (ctx.rules or {}).get("rules") or []
        for rule in rules:
            rule_id = rule.get("id") or "unnamed"
            name = rule.get("name") or rule_id
            severity = Severity(str(rule.get("severity", "warning")).lower())
            when = rule.get("when") or {}
            require = rule.get("require") or {}
            if not require:
                raise RuleError(f"Rule {rule_id!r} has no `require` clause")

            for video in catalog.videos:
                if not _evaluate(when, video, catalog):
                    continue
                if _evaluate(require, video, catalog):
                    continue
                yield Finding(
                    check_id=f"custom.{rule_id}",
                    severity=severity,
                    title=name,
                    detail=_explain(require),
                    video_id=video.id,
                    impact_views=int(video.views_per_day * 30),
                    evidence={"rule_id": rule_id, "require": require},
                    fix=_build_fix(rule, video),
                )


def _explain(require: dict[str, Any]) -> str:
    bits = []
    for name, expected in require.items():
        readable = name.replace("_", " ")
        bits.append(f"{readable}: {expected}")
    return "failed requirement — " + "; ".join(bits)
