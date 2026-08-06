"""Pitstop command line.

    pitstop scan   <channel>   read-only audit of any channel. No login.
    pitstop plan   <channel>   what would change, as a reviewable diff.
    pitstop apply  <channel>   actually change it. Requires ownership.
    pitstop auth               one-time OAuth for your own channel.
    pitstop checks             list every check and what it needs.
    pitstop init               write a starter pitstop.yaml.

The output is deliberately dense and colour-coded — this is the surface a
creator actually looks at, and a wall of undifferentiated text would bury the
four findings that matter under the ninety that don't.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import (BarColumn, Progress, SpinnerColumn, TextColumn,
                           TimeElapsedColumn)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from . import score as scoring
from .catalog import fetch_catalog
from .checks import CheckContext, all_checks, run_all
from .config import CONFIG, ROOT as ROOT_DIR
from .models import Catalog, Finding, Severity
from .planner import build_plan, split_by_budget
from .quota import QuotaLedger, estimate_fetch_cost
from .rules import EXAMPLE, load_rules
from .youtube import AuthRequired, YouTubeClient, YouTubeError

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Audit, score and repair a YouTube channel.")
console = Console()

SEVERITY_STYLE = {
    Severity.CRITICAL: ("bold red", "⛔"),
    Severity.WARNING: ("yellow", "⚠️ "),
    Severity.NOTICE: ("dim cyan", "· "),
}


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _build_client(fixture: Optional[str], owner: bool,
                  budget: int) -> YouTubeClient:
    ledger = QuotaLedger(budget=budget)
    try:
        return YouTubeClient(ledger=ledger, fixture=fixture, owner=owner)
    except YouTubeError as exc:
        console.print(f"[bold red]✗[/] {exc}")
        raise typer.Exit(1)


def _scan(channel: str, *, fixture: Optional[str], owner: bool,
          limit: Optional[int], budget: int, rules_path: Optional[Path],
          quiet: bool = False) -> tuple[Catalog, list[Finding], list, YouTubeClient]:
    client = _build_client(fixture, owner, budget)
    rules = load_rules(rules_path)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  BarColumn(bar_width=28), TimeElapsedColumn(),
                  console=console, transient=True, disable=quiet) as progress:
        task = progress.add_task("Starting", total=None)

        def on_fetch(stage: str, done: int, total: int | None) -> None:
            progress.update(task, description=stage,
                            total=total, completed=done)

        try:
            catalog = fetch_catalog(client, channel, limit=limit,
                                    with_captions=owner and limit is not None,
                                    with_analytics=owner, progress=on_fetch)
        except YouTubeError as exc:
            progress.stop()
            console.print(f"[bold red]✗[/] {exc}")
            raise typer.Exit(1)

        ctx = CheckContext(has_network=True, has_llm=CONFIG.has_llm, rules=rules)
        progress.update(task, description="Running checks",
                        total=len(all_checks()), completed=0)

        def on_check(name: str, done: int, total: int) -> None:
            progress.update(task, description=f"Checking · {name}",
                            total=total, completed=done)

        findings, skipped = run_all(catalog, ctx, progress=on_check)

    return catalog, findings, skipped, client


def _header(catalog: Catalog, client: YouTubeClient) -> None:
    ch = catalog.channel
    mode = {"PUBLIC": "public scan · read-only",
            "OWNER": "authenticated as owner",
            "FIXTURE": "fixture replay · offline"}[client.mode]
    subtitle = (f"{ch.video_count:,} videos · {ch.subscriber_count:,} subscribers"
                f" · {ch.view_count:,} views")
    console.print()
    console.print(Panel(
        Group(Text(ch.title, style="bold white"),
              Text(subtitle, style="dim"),
              Text(mode, style="dim italic")),
        title="[bold]PITSTOP[/]", border_style="bright_black",
        padding=(0, 2)))


def _score_block(report: scoring.ScoreReport) -> None:
    colour = ("green" if report.score >= 80
              else "yellow" if report.score >= 60 else "red")
    filled = round(report.score / 5)
    bar = "█" * filled + "░" * (20 - filled)

    console.print()
    console.print(Text.assemble(
        ("  Channel health  ", "dim"),
        (f"{report.score}", f"bold {colour}"),
        ("/100  ", "dim"),
        (f"[{report.grade}]  ", f"bold {colour}"),
        (bar, colour),
    ))
    console.print(f"  [dim]{report.headline}[/]")
    console.print()

    table = Table(box=None, padding=(0, 2), show_header=True,
                  header_style="dim")
    table.add_column("Category")
    table.add_column("Score", justify="right")
    table.add_column("Issues", justify="right")
    table.add_column("", style="dim")
    for cat in report.categories:
        cat_colour = ("green" if cat.score >= 80
                      else "yellow" if cat.score >= 60 else "red")
        table.add_row(cat.label,
                      f"[{cat_colour}]{cat.score}[/]",
                      str(cat.findings) if cat.findings else "[dim]—[/]",
                      cat.description)
    console.print(table)


def _findings_block(findings: list[Finding], catalog: Catalog,
                    limit: int) -> None:
    ranked = scoring.rank(findings, catalog)
    grouped: dict[str, list[Finding]] = {}
    for finding in ranked:
        grouped.setdefault(finding.title, []).append(finding)

    console.print()
    console.print(Rule("[dim]Findings[/]", style="bright_black"))
    console.print()

    for title, group in list(grouped.items())[:limit]:
        style, icon = SEVERITY_STYLE[group[0].severity]
        views = sum(f.impact_views for f in group)
        fixable = sum(1 for f in group if f.auto_fixable)

        head = Text.assemble(
            (f"{icon} ", style),
            (title, f"{style}"),
            (f"  ×{len(group)}", "dim"),
        )
        if views:
            head.append(f"   ~{views:,} views/mo affected", style="dim")
        if fixable:
            head.append(f"   {fixable} auto-fixable", style="dim green")
        console.print(head)

        for finding in group[:3]:
            video = catalog.video(finding.video_id) if finding.video_id else None
            label = f"{video.title[:52]}…" if video and len(video.title) > 52 else (
                video.title if video else "channel-wide")
            console.print(f"    [dim]{label}[/]")
            console.print(f"      [dim]↳ {finding.detail}[/]")
        if len(group) > 3:
            console.print(f"    [dim]… and {len(group) - 3} more[/]")
        console.print()


def _priority_block(report: scoring.ScoreReport) -> None:
    if not report.priority_videos:
        return
    console.print(Rule("[dim]Fix these first[/]", style="bright_black"))
    console.print()
    table = Table(box=None, padding=(0, 2), show_header=True,
                  header_style="dim")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Video")
    table.add_column("Issues", justify="right")
    table.add_column("Views/mo", justify="right")
    for i, video in enumerate(report.priority_videos[:5], 1):
        title = video["title"]
        table.add_row(str(i),
                      title[:56] + "…" if len(title) > 56 else title,
                      str(video["issues"]),
                      f"{video['views_per_month']:,}")
    console.print(table)
    console.print()


def _quota_line(client: YouTubeClient, label: str = "Quota") -> None:
    ledger = client.ledger
    console.print(
        f"[dim]{label}: {ledger.spent:,} / {ledger.budget:,} units used "
        f"({ledger.pct_used:.1f}%) · {ledger.remaining:,} remaining today[/]")


def _skipped_block(skipped: list) -> None:
    if not skipped:
        return
    console.print(f"[dim]{len(skipped)} check(s) skipped:[/]")
    for item in skipped:
        console.print(f"  [dim]· {item.name} — {item.reason}[/]")
    console.print()


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


@app.command()
def scan(
    channel: str = typer.Argument(..., help="Channel URL, @handle, or UC… id"),
    limit: Optional[int] = typer.Option(None, "--limit", "-n",
                                        help="Only scan the N most recent videos"),
    fixture: Optional[str] = typer.Option(None, "--fixture",
                                          help="Replay a recorded fixture, offline"),
    owner: bool = typer.Option(False, "--owner",
                               help="Authenticate to unlock tags/analytics"),
    top: int = typer.Option(8, "--top", help="How many finding groups to show"),
    rules_file: Optional[Path] = typer.Option(None, "--rules",
                                              help="Path to pitstop.yaml"),
    json_out: Optional[Path] = typer.Option(None, "--json",
                                            help="Also write the full report as JSON"),
):
    """Audit a channel. Read-only — this never changes anything."""
    catalog, findings, skipped, client = _scan(
        channel, fixture=fixture, owner=owner, limit=limit,
        budget=CONFIG.quota_budget, rules_path=rules_file)

    _header(catalog, client)
    report = scoring.compute(catalog, findings)
    _score_block(report)

    if findings:
        _findings_block(findings, catalog, top)
        _priority_block(report)
    else:
        console.print("\n  [green]No findings. This catalog is clean.[/]\n")

    _skipped_block(skipped)

    summary = Text.assemble(
        (f"{report.total_findings} findings", "bold"),
        ("  ·  ", "dim"),
        (f"{report.critical} critical", "red" if report.critical else "dim"),
        ("  ·  ", "dim"),
        (f"{report.auto_fixable} auto-fixable", "green"),
        ("  ·  ", "dim"),
        (f"{report.affected_videos} videos affected", "dim"),
    )
    console.print(summary)
    _quota_line(client)

    if report.auto_fixable:
        console.print(f"\n  [bold]Next:[/] [cyan]pitstop plan {channel}[/] "
                      f"[dim]— see exactly what would change[/]\n")

    if json_out:
        _write_json(json_out, catalog, findings, report, client)
        console.print(f"[dim]Report written to {json_out}[/]")


@app.command()
def plan(
    channel: str = typer.Argument(..., help="Channel URL, @handle, or UC… id"),
    limit: Optional[int] = typer.Option(None, "--limit", "-n"),
    fixture: Optional[str] = typer.Option(None, "--fixture"),
    owner: bool = typer.Option(True, "--owner/--no-owner"),
    only: Optional[str] = typer.Option(None, "--only",
                                       help="Comma-separated check ids"),
    rules_file: Optional[Path] = typer.Option(None, "--rules"),
    full: bool = typer.Option(False, "--full", help="Show complete new text"),
):
    """Show exactly what `apply` would change. Changes nothing."""
    catalog, findings, _skipped, client = _scan(
        channel, fixture=fixture, owner=owner, limit=limit,
        budget=CONFIG.quota_budget, rules_path=rules_file)

    _header(catalog, client)
    only_checks = [c.strip() for c in only.split(",")] if only else None
    the_plan, conflicts = build_plan(catalog, findings,
                                     only_checks=only_checks)
    today, deferred = split_by_budget(the_plan, CONFIG.quota_budget)

    if not the_plan.changes:
        console.print("\n  [green]Nothing to change.[/] "
                      "[dim]Remaining findings need a human decision.[/]\n")
        raise typer.Exit(0)

    console.print()
    console.print(Rule("[dim]Plan[/]", style="bright_black"))

    current_video = None
    for change in today.changes:
        if change.video_id != current_video:
            current_video = change.video_id
            console.print()
            console.print(Text.assemble(
                ("~ ", "yellow"),
                (change.video_title[:64], "bold white"),
                (f"  {change.video_id}", "dim"),
            ))
        _render_change(change, full=full)

    console.print()
    console.print(Rule(style="bright_black"))
    console.print(Text.assemble(
        ("Plan: ", "bold"),
        (f"{len(today.changes)} changes", "bold cyan"),
        (" across ", "dim"),
        (f"{today.affected_videos} videos", "bold cyan"),
        ("  ·  quota ", "dim"),
        (f"{today.quota_cost:,}/{CONFIG.quota_budget:,} units", "dim"),
    ))

    if deferred.changes:
        console.print(
            f"[yellow]⚠[/]  {len(deferred.changes)} further changes exceed "
            f"today's quota budget and are deferred to the next run.\n"
            f"   [dim]This is expected on large catalogs — videos.update costs "
            f"50 units and the daily pool is 10,000.[/]")

    if conflicts:
        console.print(f"\n[yellow]⚠[/]  {len(conflicts)} fix(es) dropped as "
                      f"unmergeable:")
        for conflict in conflicts[:5]:
            console.print(f"   [dim]{conflict.video_id} {conflict.field}: "
                          f"{conflict.dropped} — {conflict.reason}[/]")

    console.print(f"\n  [dim]Nothing has changed yet.[/] "
                  f"Run [cyan]pitstop apply {channel}[/] to execute.\n")


@app.command()
def apply(
    channel: str = typer.Argument(...),
    limit: Optional[int] = typer.Option(None, "--limit", "-n"),
    fixture: Optional[str] = typer.Option(None, "--fixture"),
    only: Optional[str] = typer.Option(None, "--only"),
    rules_file: Optional[Path] = typer.Option(None, "--rules"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Walk the whole path, write nothing"),
):
    """Apply the plan. This changes your channel."""
    catalog, findings, _skipped, client = _scan(
        channel, fixture=fixture, owner=True, limit=limit,
        budget=CONFIG.quota_budget, rules_path=rules_file)

    _header(catalog, client)
    only_checks = [c.strip() for c in only.split(",")] if only else None
    the_plan, _conflicts = build_plan(catalog, findings,
                                      only_checks=only_checks)
    today, deferred = split_by_budget(the_plan, client.ledger.remaining)

    if not today.changes:
        console.print("\n  [green]Nothing to apply.[/]\n")
        raise typer.Exit(0)

    console.print(f"\n  About to change [bold]{today.affected_videos} videos[/] "
                  f"({len(today.changes)} edits, ~{today.quota_cost:,} units).")

    if CONFIG.require_confirm and not yes and not dry_run:
        if not typer.confirm("  Proceed?", default=False):
            console.print("  [dim]Aborted. Nothing changed.[/]\n")
            raise typer.Exit(0)

    with Progress(SpinnerColumn(),
                  TextColumn("[progress.description]{task.description}"),
                  BarColumn(bar_width=30), TimeElapsedColumn(),
                  console=console) as progress:
        task = progress.add_task("Applying", total=len(today.changes))

        def on_progress(stage: str, done: int, total: int) -> None:
            progress.update(task, description=stage, completed=done,
                            total=total)

        from .applier import apply_plan
        try:
            result = apply_plan(client, today, dry_run=dry_run,
                                progress=on_progress)
        except AuthRequired as exc:
            progress.stop()
            console.print(f"\n[bold red]✗[/] {exc}\n"
                          f"  Run [cyan]pitstop auth[/] first.\n")
            raise typer.Exit(1)

    console.print()
    verb = "would change" if dry_run else "changed"
    console.print(f"  [bold green]✓[/] {verb} "
                  f"[bold]{len(result.applied)}[/] fields across "
                  f"[bold]{len({c.video_id for c in result.applied})}[/] videos.")

    if result.failed:
        console.print(f"  [red]✗[/] {len(result.failed)} failed:")
        for change, error in result.failed[:5]:
            console.print(f"     [dim]{change.video_id} {change.field}: "
                          f"{error[:80]}[/]")

    if result.stopped_early:
        console.print(f"  [yellow]⚠[/] {result.stopped_early}")
    if deferred.changes:
        console.print(f"  [dim]{len(deferred.changes)} change(s) deferred — "
                      f"re-run tomorrow, the plan re-diffs automatically.[/]")

    _quota_line(client, "Quota spent")
    if not dry_run:
        console.print(f"\n  [dim]Verify: [/][cyan]https://www.youtube.com/"
                      f"{'@' + catalog.channel.handle if catalog.channel.handle else 'channel/' + catalog.channel.id}/videos[/]\n")


@app.command()
def auth():
    """One-time OAuth so Pitstop can read analytics and write to your channel."""
    if not CONFIG.has_oauth:
        console.print(
            f"\n[bold red]✗[/] No OAuth client at "
            f"[cyan]{CONFIG.client_secret_file}[/].\n\n"
            f"  1. console.cloud.google.com → your project\n"
            f"  2. Enable [bold]YouTube Data API v3[/] and "
            f"[bold]YouTube Analytics API[/]\n"
            f"  3. OAuth consent screen → External → stay in [bold]Testing[/] "
            f"→ add yourself as a test user\n"
            f"  4. Credentials → Create OAuth client ID → [bold]Desktop app[/] "
            f"→ download JSON\n"
            f"  5. Save it as [cyan]{CONFIG.client_secret_file.name}[/] in the "
            f"project root\n")
        raise typer.Exit(1)

    console.print("\n  Opening your browser for Google sign-in…")
    client = YouTubeClient(owner=True)
    try:
        channel, _ = client.resolve_channel("MINE")
    except Exception:
        # resolve_channel needs a ref; for the owner we use mine=True upstream.
        # Falling back to a direct call keeps `auth` a pure credential check.
        creds_ok = CONFIG.token_file.exists()
        if creds_ok:
            console.print(f"  [bold green]✓[/] Authenticated. Token cached at "
                          f"[dim]{CONFIG.token_file}[/]\n")
            raise typer.Exit(0)
        raise
    console.print(f"  [bold green]✓[/] Authenticated as [bold]{channel.title}[/]\n")


@app.command()
def doctor():
    """Check your setup and say exactly what's missing and how to fix it."""
    console.print()
    console.print(Panel(Text("Pitstop setup check", style="bold white"),
                        border_style="bright_black", padding=(0, 2)))
    console.print()

    env_file = ROOT_DIR / ".env"
    rows: list[tuple[str, bool, str, str]] = []

    # 1. .env exists
    rows.append((
        ".env file",
        env_file.exists(),
        str(env_file),
        f"Create it: cp .env.example .env",
    ))

    # 2. API key
    key = CONFIG.api_key
    key_ok = bool(key) and len(key) > 20
    if key and not key_ok:
        hint = f"Key looks too short ({len(key)} chars) — expected ~39."
    else:
        hint = "Add YOUTUBE_API_KEY=... to .env  (see SETUP.md step 1)"
    rows.append((
        "YouTube API key",
        key_ok,
        f"{key[:8]}…{key[-4:]} ({len(key)} chars)" if key_ok else "not set",
        hint,
    ))

    # 3. OAuth client secret
    secret = CONFIG.client_secret_file
    secret_ok = secret.exists()
    detail = str(secret)
    hint = ("Download the OAuth client JSON and save it as "
            f"{secret.name} in {ROOT_DIR}  (see SETUP.md step 2)")
    if secret_ok:
        try:
            data = json.loads(secret.read_text(encoding="utf-8"))
            kind = "installed" if "installed" in data else (
                "web" if "web" in data else "unknown")
            if kind == "installed":
                detail = f"{secret.name} · Desktop app ✓"
            elif kind == "web":
                secret_ok = False
                detail = f"{secret.name} · type is 'Web application'"
                hint = ("Wrong client type. Create a new OAuth client and pick "
                        "'Desktop app' — a Web client cannot do the local "
                        "loopback login Pitstop uses.")
            else:
                secret_ok = False
                detail = f"{secret.name} · unrecognised format"
                hint = "This doesn't look like a Google OAuth client file."
        except (json.JSONDecodeError, OSError) as exc:
            secret_ok = False
            detail = f"{secret.name} · unreadable"
            hint = f"Could not parse it: {exc}"
    else:
        detail = "not found"
    rows.append(("OAuth client (Desktop app)", secret_ok, detail, hint))

    # 4. Token
    token_ok = CONFIG.token_file.exists()
    rows.append((
        "Signed in (pitstop auth)",
        token_ok,
        str(CONFIG.token_file) if token_ok else "not signed in yet",
        "Run: pitstop auth",
    ))

    # 5. Fixture
    fixtures = sorted(p.stem for p in (ROOT_DIR / "fixtures").glob("*.json"))
    rows.append((
        "Offline demo fixture",
        bool(fixtures),
        ", ".join(fixtures) if fixtures else "none",
        "Run: python scripts/make_fixture.py",
    ))

    table = Table(box=None, padding=(0, 2), show_header=True,
                  header_style="dim")
    table.add_column("", width=2)
    table.add_column("What")
    table.add_column("Status", style="dim")

    for label, ok, detail, _ in rows:
        table.add_row("[green]✓[/]" if ok else "[red]✗[/]", label, detail)
    console.print(table)

    # --- what you can do right now -----------------------------------------
    console.print()
    console.print(Rule("[dim]What you can do right now[/]",
                       style="bright_black"))
    console.print()

    can_fixture = bool(fixtures)
    can_public = key_ok
    can_apply = secret_ok and token_ok

    def line(ok: bool, text: str, cmd: str) -> None:
        mark = "[green]✓[/]" if ok else "[dim]·[/]"
        body = text if ok else f"[dim]{text}[/]"
        console.print(f"  {mark} {body}")
        console.print(f"      [{'cyan' if ok else 'dim'}]{cmd}[/]")

    line(can_fixture, "Run the whole pipeline offline, no credentials",
         "pitstop scan demo --fixture demo")
    line(can_public, "Scan any real public channel",
         "pitstop scan @mkbhd --limit 100")
    line(can_apply, "Repair your own channel",
         "pitstop plan @shivangshirodkar4518")

    # --- next action --------------------------------------------------------
    blocked = [(label, hint) for label, ok, _, hint in rows if not ok]
    console.print()
    if not blocked:
        console.print("  [bold green]Everything is set up.[/]\n")
        return

    console.print(Rule("[dim]Next step[/]", style="bright_black"))
    console.print()
    label, hint = blocked[0]
    console.print(f"  [bold]{label}[/]")
    console.print(f"  [dim]{hint}[/]")
    if len(blocked) > 1:
        console.print(f"\n  [dim]Then {len(blocked) - 1} more — re-run "
                      f"[cyan]pitstop doctor[/][dim] after each.[/]")
    console.print(f"\n  [dim]Full walkthrough: {ROOT_DIR / 'SETUP.md'}[/]\n")


@app.command()
def checks():
    """List every check, what it looks for, and what it needs to run."""
    table = Table(box=None, padding=(0, 2), header_style="dim")
    table.add_column("id", style="cyan")
    table.add_column("what it finds")
    table.add_column("needs", style="dim")

    for check in all_checks():
        needs = []
        if check.requires_owner:
            needs.append("ownership")
        if check.requires_network:
            needs.append("network")
        if check.requires_llm:
            needs.append("llm")
        table.add_row(check.id, check.name, ", ".join(needs) or "—")

    console.print()
    console.print(table)
    console.print(f"\n  [dim]{len(all_checks())} checks registered.[/]\n")


@app.command()
def init(
    path: Path = typer.Option(Path("pitstop.yaml"), "--path"),
    force: bool = typer.Option(False, "--force"),
):
    """Write a starter pitstop.yaml with commented examples."""
    if path.exists() and not force:
        console.print(f"[yellow]![/] {path} already exists. Use --force.")
        raise typer.Exit(1)
    path.write_text(EXAMPLE, encoding="utf-8")
    console.print(f"[green]✓[/] Wrote {path}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
):
    """Run the web UI + API."""
    import uvicorn

    console.print(f"\n  Pitstop UI → [cyan]http://{host}:{port}[/]\n")
    uvicorn.run("pitstop.server:app", host=host, port=port, reload=False)


# ---------------------------------------------------------------------------


def _render_change(change, *, full: bool) -> None:
    if change.field == "playlist_add":
        console.print(f"    [green]+[/] playlist    [dim]{change.note}[/]")
        return
    if change.field == "tags":
        added = [t for t in change.proposed if t not in (change.current or [])]
        console.print(f"    [yellow]~[/] tags        "
                      f"[green]+{', '.join(added)}[/]  [dim]{change.note}[/]")
        return

    console.print(f"    [yellow]~[/] {change.field:<11} [dim]{change.note}[/]")
    if change.field == "description":
        for line in _diff_lines(str(change.current), str(change.proposed),
                                full=full):
            console.print(f"      {line}")


def _diff_lines(before: str, after: str, *, full: bool) -> list[str]:
    import difflib

    diff = list(difflib.unified_diff(
        before.splitlines(), after.splitlines(), lineterm="", n=0))
    out: list[str] = []
    for line in diff[2:]:
        if line.startswith("+"):
            out.append(f"[green]{line[:110]}[/]")
        elif line.startswith("-"):
            out.append(f"[red]{line[:110]}[/]")
        elif line.startswith("@"):
            out.append(f"[dim]{line}[/]")
        if not full and len(out) >= 6:
            out.append("[dim]… (--full for the rest)[/]")
            break
    return out


def _write_json(path: Path, catalog: Catalog, findings: list[Finding],
                report: scoring.ScoreReport, client: YouTubeClient) -> None:
    import json
    from dataclasses import asdict

    payload = {
        "channel": asdict(catalog.channel),
        "score": asdict(report),
        "quota": {"spent": client.ledger.spent,
                  "budget": client.ledger.budget},
        "findings": [
            {"check_id": f.check_id, "severity": f.severity.value,
             "title": f.title, "detail": f.detail, "video_id": f.video_id,
             "impact_views": f.impact_views, "auto_fixable": f.auto_fixable,
             "evidence": f.evidence}
            for f in scoring.rank(findings, catalog)
        ],
    }
    path.write_text(json.dumps(payload, indent=2, default=str),
                    encoding="utf-8")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/]")
        sys.exit(130)


if __name__ == "__main__":
    main()
