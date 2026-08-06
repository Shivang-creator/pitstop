"""Guided OAuth setup.

Google's console needs roughly eleven clicks across four pages that live under
two different menu names (it is mid-rename from "APIs & Services > OAuth
consent screen" to "Google Auth Platform"). Written as prose in a README that
is genuinely hard to follow.

So this walks it: one page at a time, opened in the browser for you, with the
exact thing to click on *that* page and nothing else. At the end it watches
your Downloads folder and files the credential itself.

Nothing here talks to Google. It opens URLs and moves a file — all the real
work happens in your browser, which is the only place it can happen.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.text import Text

from .config import CONFIG, ROOT

console = Console()

# Stable console URLs. The /apis/ paths redirect to the newer Google Auth
# Platform UI when an account has been migrated, so one link works for both.
URLS = {
    "library_analytics": (
        "https://console.cloud.google.com/apis/library/"
        "youtubeanalytics.googleapis.com"),
    "consent": "https://console.cloud.google.com/apis/credentials/consent",
    "credentials": "https://console.cloud.google.com/apis/credentials",
}

DOWNLOADS = Path.home() / "Downloads"


def _open(url: str) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", url], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", url], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            import webbrowser

            webbrowser.open(url)
    except Exception:
        pass


def _step(number: int, total: int, title: str, url: str | None,
          instructions: list[str], note: str | None = None) -> None:
    console.print()
    console.rule(f"[bold cyan]Step {number} of {total}[/]  {title}",
                 style="bright_black", align="left")
    console.print()

    if url:
        _open(url)
        console.print("  [dim]Opened in your browser:[/]")
        console.print(f"  [blue underline]{url}[/]")
        console.print()

    for i, line in enumerate(instructions, 1):
        console.print(f"  [bold cyan]{i}.[/] {line}")

    if note:
        console.print()
        console.print(f"  [yellow]![/] [dim]{note}[/]")

    console.print()
    Prompt.ask("  [dim]Press Enter when that's done[/]", default="",
               show_default=False)


def _find_downloaded_client(since: float) -> Path | None:
    """Newest client_secret*.json in Downloads, modified after `since`."""
    candidates = [
        p for p in DOWNLOADS.glob("client_secret*.json")
        if p.stat().st_mtime >= since - 5
    ]
    if not candidates:
        # Some browsers rename on collision; fall back to any recent json that
        # looks like an OAuth client.
        candidates = [
            p for p in DOWNLOADS.glob("*.json")
            if p.stat().st_mtime >= since
            and "installed" in p.read_text(errors="ignore")[:400]
        ]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def run() -> int:
    console.print()
    console.print(Panel(
        Text.assemble(
            ("Connect your YouTube channel\n", "bold white"),
            ("Four pages, one thing to click on each. "
             "I'll open them for you.", "dim"),
        ),
        border_style="cyan", padding=(1, 3)))

    if CONFIG.client_secret_file.exists():
        console.print()
        console.print(f"  [yellow]![/] {CONFIG.client_secret_file.name} already "
                      f"exists.")
        if not Confirm.ask("  Start over and replace it?", default=False):
            console.print("\n  [dim]Keeping it. Run "
                          "[cyan]pitstop auth[/][dim] to sign in.[/]\n")
            return 0

    # --- which account owns the channel? -----------------------------------
    console.print()
    console.print("  [bold]First, one question.[/]")
    console.print("  [dim]Which Google account owns the YouTube channel you "
                  "want to repair?[/]")
    console.print("  [dim]This is the account you log into YouTube Studio "
                  "with — not necessarily[/]")
    console.print("  [dim]the one paying for anything else.[/]")
    console.print()
    email = Prompt.ask("  [cyan]Channel owner's Google account[/]").strip()

    if not email or "@" not in email:
        console.print("\n  [red]✗[/] That doesn't look like an email address.\n")
        return 1

    console.print()
    console.print(f"  [green]✓[/] Using [bold]{email}[/] throughout.")
    console.print(f"  [dim]Sign in as this account on every page below. If your "
                  f"browser is[/]")
    console.print(f"  [dim]logged into a different Google account, switch it "
                  f"now or use a private window.[/]")

    total = 4

    # --- 1. Analytics API ---------------------------------------------------
    _step(1, total, "Turn on the Analytics API",
          URLS["library_analytics"],
          ["Check the project selector at the top says [bold]pitstop[/] "
           "(the project your API key is in).",
           "Click the blue [bold]ENABLE[/] button.",
           "If it already says [bold]MANAGE[/], it's on — nothing to do."],
          note="This is the only API you still need. Data API v3 is already on.")

    # --- 2. Consent screen --------------------------------------------------
    _step(2, total, "Say who the app is",
          URLS["consent"],
          ["If asked for a user type, choose [bold]External[/], then "
           "[bold]CREATE[/].",
           f"App name: [bold]Pitstop[/]  ·  User support email: "
           f"[bold]{email}[/]",
           f"Scroll to the bottom, Developer contact: [bold]{email}[/]",
           "Click [bold]SAVE[/] (or SAVE AND CONTINUE through any extra "
           "pages — you can skip Scopes entirely)."],
          note="Leave it in Testing mode. Do NOT click 'Publish app' — that "
               "starts a review that takes weeks, and you don't need it.")

    # --- 3. Test user -------------------------------------------------------
    _step(3, total, "Allow yourself in",
          URLS["consent"],
          [f"Find [bold]Test users[/] — it's either a section on this page or "
           f"a tab called [bold]Audience[/] in the left menu.",
           "Click [bold]+ ADD USERS[/].",
           f"Type [bold]{email}[/] and click [bold]SAVE[/]."],
          note="Skipping this is what causes 'access_denied' later. Without "
               "it Google refuses your own login.")

    # --- 4. Create the client ----------------------------------------------
    started = time.time()
    _step(4, total, "Create and download the credential",
          URLS["credentials"],
          ["Click [bold]+ CREATE CREDENTIALS[/] at the top → "
           "[bold]OAuth client ID[/].",
           "Application type → [bold]Desktop app[/]  "
           "[red](not 'Web application')[/]",
           "Name it [bold]Pitstop CLI[/] → [bold]CREATE[/].",
           "In the popup, click [bold]DOWNLOAD JSON[/]."],
          note="Desktop app matters — a Web client cannot do the local login "
               "this tool uses.")

    # --- file it ------------------------------------------------------------
    console.print("  [dim]Looking for the downloaded file…[/]")
    found = None
    for _ in range(3):
        found = _find_downloaded_client(started)
        if found:
            break
        time.sleep(1.5)

    if found:
        console.print(f"  [green]✓[/] Found [bold]{found.name}[/]")
        shutil.move(str(found), str(CONFIG.client_secret_file))
        console.print(f"  [green]✓[/] Filed as "
                      f"[cyan]{CONFIG.client_secret_file.name}[/] in {ROOT}")
    else:
        console.print()
        console.print("  [yellow]![/] Couldn't find it in your Downloads "
                      "folder automatically.")
        console.print("  [dim]Move it yourself with this — the wildcard "
                      "handles the long filename:[/]")
        console.print()
        console.print(f"  [cyan]mv ~/Downloads/client_secret_*.json "
                      f"{CONFIG.client_secret_file}[/]")
        console.print()
        return 1

    # --- sign in ------------------------------------------------------------
    console.print()
    console.rule("[bold cyan]Last thing[/]  Sign in", style="bright_black",
                 align="left")
    console.print()
    console.print(f"  A browser window will open. Sign in as [bold]{email}[/].")
    console.print()
    console.print("  [yellow]You will see \"Google hasn't verified this app\".[/]")
    console.print("  [dim]That's expected — it's your own app, in Testing "
                  "mode. Click[/]")
    console.print("  [dim]Advanced → \"Go to Pitstop (unsafe)\" → Allow.[/]")
    console.print()

    if not Confirm.ask("  Open the sign-in window now?", default=True):
        console.print("\n  [dim]Run [cyan]pitstop auth[/][dim] when you're "
                      "ready.[/]\n")
        return 0

    from .youtube import YouTubeClient

    try:
        client = YouTubeClient(owner=True)
        client._credentials()  # triggers the browser flow, caches the token
    except Exception as exc:
        console.print(f"\n  [red]✗[/] Sign-in failed: {exc}")
        console.print("  [dim]Run [cyan]pitstop doctor[/][dim] to see where "
                      "things stand.[/]\n")
        return 1

    console.print()
    console.print("  [bold green]✓ Done.[/] Pitstop can now repair your "
                  "channel.")
    console.print()
    console.print("  [dim]Try it:[/]")
    console.print("  [cyan]pitstop plan <your-channel>[/]  "
                  "[dim]— see what would change, changes nothing[/]")
    console.print()
    return 0
