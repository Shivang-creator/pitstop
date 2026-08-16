"""Check plugin contract + registry.

A Check is a small, independent, pure-ish unit:

    Catalog  ->  Iterable[Finding]

Rules of the contract, enforced by convention and by tests:

  1. A check NEVER writes. It has no access to the write API at all.
  2. A check declares what it needs (`requires_owner`, `requires_network`,
     `requires_llm`). The runner skips it with a reason rather than letting it
     fail — a public scan of someone else's channel should degrade to fewer
     checks, never to a crash or a wrong answer.
  3. A check that can propose a machine-applicable repair attaches a `Fix`.
     Everything else is advisory and shows up in the report as "manual".

Adding a check is one file plus one `@register` decorator. That is the whole
extension story, and it is why the check list can keep growing without any
other layer changing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

from ..models import Catalog, Finding


class Check(Protocol):
    id: str
    name: str
    description: str
    requires_owner: bool
    requires_network: bool
    requires_llm: bool

    def run(self, catalog: Catalog, ctx: "CheckContext") -> Iterable[Finding]:
        ...


@dataclass
class CheckContext:
    """Everything a check may need that isn't the catalog itself."""

    has_network: bool = True
    has_llm: bool = False
    # User-supplied rules from pitstop.yaml — see rules.py
    rules: dict = None  # type: ignore[assignment]
    # Populated by the runner so checks can share expensive work (e.g. the
    # link checker resolves each unique URL once for the whole catalog).
    cache: dict = None  # type: ignore[assignment]

    # --- optional budget for network-bound checks ---------------------------
    # Both default to None, meaning "no ceiling" — that is what the CLI uses,
    # and it is the behaviour every existing test asserts against.
    #
    # The public web scan sets them, because a shared HTTP request cannot spend
    # unbounded time resolving a 900-link catalog. When either bites, the check
    # records what it could not reach in `cache["link_budget"]` so the caller
    # can say so out loud. Unreached links are reported as UNVERIFIED, never as
    # dead — a budget must cost us findings, never invent them.
    link_max_urls: int | None = None
    link_time_budget: float | None = None
    # How many link probes run at once. None keeps links.CONCURRENCY, which is
    # tuned to be a polite neighbour from one creator's laptop. A single
    # serverless scan is a different situation — it is one request, once, and
    # the visitor is watching — so the public scanner raises it.
    link_concurrency: int | None = None

    def __post_init__(self) -> None:
        if self.rules is None:
            self.rules = {}
        if self.cache is None:
            self.cache = {}


@dataclass
class SkippedCheck:
    check_id: str
    name: str
    reason: str


REGISTRY: dict[str, Check] = {}


def register(cls):
    """Class decorator that adds a check to the registry."""
    instance = cls()
    if instance.id in REGISTRY:
        raise ValueError(f"Duplicate check id: {instance.id}")
    REGISTRY[instance.id] = instance
    return cls


class BaseCheck:
    """Convenience base so individual checks stay short."""

    id: str = ""
    name: str = ""
    description: str = ""
    requires_owner: bool = False
    requires_network: bool = False
    requires_llm: bool = False

    def run(self, catalog: Catalog, ctx: CheckContext) -> Iterable[Finding]:
        raise NotImplementedError

    def unavailable_reason(self, catalog: Catalog,
                           ctx: CheckContext) -> str | None:
        if self.requires_owner and not catalog.is_owner:
            return "needs channel ownership (run `pitstop auth`)"
        if self.requires_network and not ctx.has_network:
            return "needs network access"
        if self.requires_llm and not ctx.has_llm:
            return "needs an LLM provider (set FEATHERLESS_API_KEY)"
        return None


def all_checks() -> list[Check]:
    return sorted(REGISTRY.values(), key=lambda c: c.id)


def run_all(
    catalog: Catalog,
    ctx: CheckContext,
    *,
    only: list[str] | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> tuple[list[Finding], list[SkippedCheck]]:
    """Run every registered check, collecting findings and skip reasons."""
    checks = [c for c in all_checks() if not only or c.id in only]
    findings: list[Finding] = []
    skipped: list[SkippedCheck] = []

    for i, check in enumerate(checks):
        if progress:
            progress(check.name, i, len(checks))
        reason = check.unavailable_reason(catalog, ctx)  # type: ignore[attr-defined]
        if reason:
            skipped.append(SkippedCheck(check.id, check.name, reason))
            continue
        try:
            findings.extend(check.run(catalog, ctx))
        except Exception as exc:  # a broken check must not kill the scan
            skipped.append(SkippedCheck(check.id, check.name,
                                        f"errored: {exc}"))

    if progress:
        progress("Done", len(checks), len(checks))
    return findings, skipped
