#!/usr/bin/env python3
"""Generate fixtures/demo.json — a synthetic channel with planted problems.

Why a synthetic fixture exists at all:

  1. Tests need deterministic input.
  2. Contributors can run the whole pipeline with no Google credentials.
  3. A live demo needs a path that cannot fail on stage. `--fixture demo`
     never touches the network for the catalog fetch.

The planted problems are the ones a real four-year-old channel accumulates,
in roughly the proportions you actually see: lots of orphaned videos, a
handful of dead links concentrated on old high-traffic videos, chapters that
*almost* work, and one video that was never renamed after export.

Response shapes match the real YouTube Data API exactly — same key names, same
string-typed statistics, same ISO-8601 durations — so nothing in the parsing
layer is exercised differently by a fixture than by the live API.

    python scripts/make_fixture.py
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "fixtures" / "demo.json"

random.seed(20260806)   # deterministic — the fixture must not drift

CHANNEL_ID = "UCdemoPITSTOPfixture000"
UPLOADS_PLAYLIST = "UUdemoPITSTOPfixture000"

# A dead domain, a guaranteed 404, a live link, and a shortener. These resolve
# for real when the network is up, so the link checker is genuinely exercised.
DEAD_DOMAIN = "https://pitstop-demo-dead-domain-9f3a2b.com/gear"
DEAD_404 = "https://httpbin.org/status/404"
LIVE = "https://github.com/features/actions"
SHORTENER = "https://bit.ly/3xPitstopDemo"

# The footer links must be LIVE. A dead link in a footer appears on every
# video, and the resulting 40-identical-findings wall buries the handful of
# genuinely interesting dead links. That is realistic behaviour, but it makes
# for a fixture that demonstrates one bug forty times instead of ten bugs once.
FOOTER = (
    "── ── ──\n"
    "Subscribe: https://www.youtube.com/@YouTube?sub_confirmation=1\n"
    "Everything I use: " + LIVE + "\n"
)

TOPICS = [
    ("Blender", "Blender Basics", "modelling a {n} in Blender"),
    ("Blender", "Blender Basics", "sculpting a {n} from scratch"),
    ("Python", "Python for Beginners", "building a {n} in Python"),
    ("Python", "Python for Beginners", "automating {n} with Python"),
    ("Docker", None, "deploying a {n} with Docker"),
    ("Rust", None, "writing a {n} in Rust"),
]
NOUNS = ["CLI tool", "web scraper", "REST API", "game loop", "chat bot",
         "dashboard", "parser", "renderer", "task queue", "log viewer",
         "portfolio site", "URL shortener", "note app", "file watcher"]


def chapters(count: int, *, start_at_zero: bool = True) -> str:
    lines = []
    t = 0 if start_at_zero else 42
    labels = ["Intro", "Setup", "The build", "Debugging", "Result",
              "What I'd change", "Outro"]
    for i in range(count):
        m, s = divmod(t, 60)
        lines.append(f"{m:02d}:{s:02d} {labels[i % len(labels)]}")
        t += random.choice([95, 130, 180, 240])
    return "\n".join(lines)


def build_videos() -> list[dict]:
    videos: list[dict] = []
    now = datetime.now(timezone.utc)

    for i in range(42):
        series, playlist, template = TOPICS[i % len(TOPICS)]
        noun = NOUNS[i % len(NOUNS)]
        title = f"{series}: {template.format(n=noun)}"

        age_days = 30 + i * 34          # spread across ~4 years
        published = now - timedelta(days=age_days)
        duration = random.choice([320, 480, 645, 720, 910, 1240])

        # Older videos accumulated more lifetime views — and the oldest few are
        # the channel's evergreen earners, which is what makes their dead links
        # expensive.
        base = random.randint(400, 2500)
        views = int(base * (1 + age_days / 90))
        if i in (2, 5, 9):
            views *= 14                 # the evergreen winners

        body = (f"In this one I walk through {template.format(n=noun)} "
                f"end to end, including the parts that went wrong.\n\n")

        desc_parts = [body]

        # -- planted problems ------------------------------------------------
        if i % 7 == 0:                                  # dead affiliate link
            desc_parts.append(f"Gear I used: {DEAD_DOMAIN}\n")
        if i % 11 == 0:
            desc_parts.append(f"Course link: {DEAD_404}\n")
        if i % 5 == 0:
            desc_parts.append(f"Resources: {SHORTENER}\n")
        if i % 3 == 0:                                  # valid chapters
            desc_parts.append("\n" + chapters(random.randint(4, 6)) + "\n")
        elif i % 3 == 1 and i % 6 != 1:                 # broken chapters
            desc_parts.append("\n" + chapters(4, start_at_zero=False) + "\n")
        if i % 4 != 0:                                  # footer usually present
            desc_parts.append("\n" + FOOTER)

        description = "".join(desc_parts)

        # A couple of videos never got a real description at all.
        if i in (17, 31):
            description = ""

        tags = []
        if i % 6 != 0:
            tags = [series.lower(), noun.split()[0].lower(), "tutorial",
                    "programming", "walkthrough"][:random.randint(1, 5)]

        # One video was never renamed after export, one is far too long.
        if i == 23:
            title = "VID_20240417_final2.mp4"
        if i == 8:
            title = (f"{series}: {template.format(n=noun)} — the complete "
                     f"start-to-finish walkthrough for absolute beginners in 2026")

        # A sponsored video with no disclosure anywhere.
        if i == 14:
            description = (f"Huge thanks to Acme Cloud for sponsoring this "
                           f"video. Use code BUILD20 for 20% off.\n\n"
                           + body + "\n" + FOOTER)

        videos.append({
            "id": f"vidPITSTOP{i:03d}",
            "snippet": {
                "publishedAt": published.isoformat().replace("+00:00", "Z"),
                "channelId": CHANNEL_ID,
                "title": title,
                "description": description,
                "thumbnails": {"high": {
                    "url": f"https://i.ytimg.com/vi/vidPITSTOP{i:03d}/hqdefault.jpg"}},
                "channelTitle": "Demo Channel",
                "tags": tags,
                "categoryId": "22" if i % 3 else "28",
            },
            "contentDetails": {
                "duration": f"PT{duration // 60}M{duration % 60}S",
                "caption": "true" if i % 4 == 0 else "false",
            },
            "statistics": {
                "viewCount": str(views),
                "likeCount": str(int(views * 0.04)),
                "commentCount": str(int(views * 0.006)),
            },
            "status": {"privacyStatus":
                       "private" if i == 37 else "public"},
        })

    return videos


def build_playlists(videos: list[dict]) -> list[dict]:
    """Only two playlists exist, covering a minority of the catalog.

    This is the realistic shape: creators make a couple of playlists early,
    then stop maintaining them. It's why `playlist.orphan` is the most common
    finding on almost every real channel.
    """
    blender = [v["id"] for v in videos
               if v["snippet"]["title"].startswith("Blender")][:6]
    python = [v["id"] for v in videos
              if v["snippet"]["title"].startswith("Python")][:5]

    return [
        {"id": "PLdemoBlender01", "title": "Blender Basics",
         "item_video_ids": blender,
         # This video went private but is still sitting in the playlist —
         # the silent breakage nobody notices.
         "broken_video_ids": [videos[37]["id"]] if videos[37]["id"] in blender
                             else []},
        {"id": "PLdemoPython01", "title": "Python for Beginners",
         "item_video_ids": python + [videos[37]["id"]],
         "broken_video_ids": [videos[37]["id"]]},
    ]


def main() -> None:
    videos = build_videos()
    playlists = build_playlists(videos)
    total_views = sum(int(v["statistics"]["viewCount"]) for v in videos)

    fixture = {
        "_comment": ("Synthetic channel for offline development, tests and a "
                     "demo path that cannot fail live. Response shapes match "
                     "the real YouTube Data API. Regenerate with "
                     "scripts/make_fixture.py"),
        "channel": {
            "id": CHANNEL_ID,
            "snippet": {
                "title": "Demo Channel",
                "customUrl": "@demochannel",
                "thumbnails": {"high": {
                    "url": "https://i.ytimg.com/demo/channel.jpg"}},
            },
            "statistics": {
                "subscriberCount": "48200",
                "videoCount": str(len(videos)),
                "viewCount": str(total_views),
            },
            "contentDetails": {
                "relatedPlaylists": {"uploads": UPLOADS_PLAYLIST},
            },
        },
        "videos": videos,
        "playlists": playlists,
        "captions": {v["id"]: v["contentDetails"]["caption"] == "true"
                     for v in videos},
        "retention": {v["id"]: round(random.uniform(28, 62), 1)
                      for v in videos},
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixture, indent=2), encoding="utf-8")

    orphans = len([v for v in videos
                   if v["id"] not in {i for p in playlists
                                      for i in p["item_video_ids"]}])
    print(f"Wrote {OUT.relative_to(ROOT)}")
    print(f"  {len(videos)} videos, {len(playlists)} playlists, "
          f"{total_views:,} lifetime views")
    print(f"  planted: {orphans} orphaned videos, dead links, broken chapters,")
    print(f"           1 placeholder title, 1 undisclosed sponsorship, "
          f"2 empty descriptions")


if __name__ == "__main__":
    main()
