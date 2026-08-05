"""Load and validate pitstop.yaml.

Validation is strict and the errors point at the offending rule by id. A
config file that silently does nothing is worse than one that refuses to load,
because the user believes their rules are being enforced when they aren't.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .checks.custom import PREDICATES, RuleError

DEFAULT_FILENAMES = ("pitstop.yaml", "pitstop.yml", ".pitstop.yaml")
VALID_SEVERITIES = {"critical", "warning", "notice"}
VALID_FIXES = {"add_tag", "append_description", "set_category"}


def find_rules_file(start: Path | None = None) -> Path | None:
    directory = (start or Path.cwd()).resolve()
    for name in DEFAULT_FILENAMES:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def load_rules(path: Path | None = None) -> dict[str, Any]:
    path = path or find_rules_file()
    if not path or not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise RuleError(f"{path.name}: top level must be a mapping")
    validate(raw, source=path.name)
    return raw


def validate(config: dict[str, Any], *, source: str = "pitstop.yaml") -> None:
    rules = config.get("rules") or []
    if not isinstance(rules, list):
        raise RuleError(f"{source}: `rules` must be a list")

    seen: set[str] = set()
    for index, rule in enumerate(rules):
        where = f"{source}: rule #{index + 1}"
        if not isinstance(rule, dict):
            raise RuleError(f"{where} is not a mapping")

        rule_id = rule.get("id")
        if not rule_id:
            raise RuleError(f"{where} is missing `id`")
        where = f"{source}: rule {rule_id!r}"
        if rule_id in seen:
            raise RuleError(f"{where} — duplicate id")
        seen.add(rule_id)

        require = rule.get("require")
        if not require or not isinstance(require, dict):
            raise RuleError(f"{where} — `require` must be a non-empty mapping")

        for clause_name in ("when", "require"):
            clause = rule.get(clause_name) or {}
            if not isinstance(clause, dict):
                raise RuleError(f"{where} — `{clause_name}` must be a mapping")
            for predicate in clause:
                if predicate not in PREDICATES:
                    raise RuleError(
                        f"{where} — unknown predicate {predicate!r} in "
                        f"`{clause_name}`. Known: "
                        f"{', '.join(sorted(PREDICATES))}")

        severity = str(rule.get("severity", "warning")).lower()
        if severity not in VALID_SEVERITIES:
            raise RuleError(
                f"{where} — severity must be one of "
                f"{', '.join(sorted(VALID_SEVERITIES))}, got {severity!r}")

        fix = rule.get("fix")
        if fix is not None and fix not in VALID_FIXES:
            raise RuleError(
                f"{where} — unknown fix {fix!r}. Known: "
                f"{', '.join(sorted(VALID_FIXES))}")
        if fix == "add_tag" and "has_tag" not in require:
            raise RuleError(
                f"{where} — fix `add_tag` needs `require.has_tag` to know "
                f"which tag to add")
        if fix == "set_category" and "category_id" not in require:
            raise RuleError(
                f"{where} — fix `set_category` needs `require.category_id`")


EXAMPLE = """\
# pitstop.yaml — your channel's conventions, enforced across the catalog.

# Appended to any description that doesn't already contain the first line.
description_footer: |
  ── ── ──
  Subscribe: https://youtube.com/@yourhandle?sub_confirmation=1
  Everything I use: https://yoursite.com/gear

# Optional: what `metadata.category` should set videos to.
# 27 = Education, 28 = Science & Technology, 24 = Entertainment
default_category_id: 28

rules:
  - id: tutorials-tagged
    name: Tutorial videos carry the "tutorial" tag
    when:
      title_matches: "(?i)tutorial|how to|guide"
    require:
      has_tag: tutorial
    severity: warning
    fix: add_tag

  - id: long-videos-have-chapters
    name: Videos over 8 minutes have chapters
    when:
      longer_than_seconds: 480
    require:
      has_chapters: true
    severity: warning

  - id: everything-in-a-playlist
    name: Every video belongs to at least one playlist
    require:
      in_any_playlist: true
    severity: warning
"""
