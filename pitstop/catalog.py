"""Fetch a channel into a Catalog, and cache it.

The fetch order is deliberate and quota-shaped:

  channels.list          1 unit    -> channel + uploads playlist id
  playlistItems.list     1/50      -> every video id, ever
  videos.list            1/50      -> hydrate titles, descriptions, stats
  playlists.list         1/50      -> playlists
  playlistItems.list     1 each    -> playlist membership + silent breakage

A 500-video channel costs roughly 25 units — a quarter of one percent of the
daily pool. Doing the same thing via search.list would cost 1,000 units and hit
the separate 100-calls/day cap after two channels. That difference is the reason
"scan any channel, no login" is viable as a product at all.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .models import Catalog, Video
from .youtube import YouTubeClient


def fetch_catalog(
    client: YouTubeClient,
    channel_ref: str,
    *,
    limit: int | None = None,
    with_captions: bool = False,
    with_analytics: bool = False,
    progress=None,
) -> Catalog:
    """Pull everything Pitstop needs about a channel.

    `progress` is an optional callable(stage: str, done: int, total: int|None)
    so the CLI and the web UI can both render live progress off the same fetch.
    """

    def tick(stage: str, done: int = 0, total: int | None = None) -> None:
        if progress:
            progress(stage, done, total)

    tick("Resolving channel")
    channel, uploads_playlist = client.resolve_channel(channel_ref)

    tick("Listing uploads", 0, channel.video_count or None)
    video_ids = client.list_uploads(uploads_playlist, limit=limit)

    tick("Fetching video details", 0, len(video_ids))
    videos: list[Video] = []
    for i in range(0, len(video_ids), 50):
        videos.extend(client.hydrate_videos(video_ids[i:i + 50]))
        tick("Fetching video details", min(i + 50, len(video_ids)),
             len(video_ids))
        if client.mode == "FIXTURE":
            break

    tick("Fetching playlists")
    playlists = client.list_playlists(channel.id)

    catalog = Catalog(
        channel=channel,
        videos=videos,
        playlists=playlists,
        is_owner=client.is_owner or client.mode == "FIXTURE",
        fetched_at=datetime.now(timezone.utc),
    )

    # Owner-only enrichment. Both are optional because both cost something:
    # captions.list is 50 units *per video* (so it is opt-in, never automatic
    # on a large channel), and analytics needs OAuth. Checks that depend on
    # these degrade to "skipped", never to a wrong answer.
    if with_captions and catalog.is_owner:
        tick("Checking captions", 0, len(videos))
        try:
            caption_map = client.caption_availability([v.id for v in videos])
            for v in videos:
                if v.id in caption_map:
                    v.caption_available = caption_map[v.id]
        except Exception:
            pass

    if with_analytics and catalog.is_owner:
        tick("Pulling retention analytics")
        try:
            retention = client.retention_metrics([v.id for v in videos])
            for v in videos:
                if v.id in retention:
                    v.average_view_percentage = retention[v.id]
        except Exception:
            # A brand-new channel returns an empty report rather than an error.
            # Checks that need retention simply don't fire.
            pass

    tick("Done", len(videos), len(videos))
    return catalog
