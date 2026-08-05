"""Thin, quota-accounted wrapper over the YouTube Data + Analytics APIs.

Three modes, chosen by what credentials exist:

  PUBLIC   API key only. Read-only, any channel. No login. This is the mode
           that makes "paste a URL, get a score" work with zero friction.
  OWNER    OAuth. Adds tags, caption state, analytics, and write access.
  FIXTURE  No network at all; replays recorded API responses from fixtures/.
           Used for tests, offline development, and as a demo fallback so a
           live presentation never depends on OAuth succeeding on stage.

Every call goes through `_charge`, so the ledger is always truthful — including
in fixture mode, which means quota estimates can be verified without spending
a single real unit.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import CONFIG, SCOPES, ROOT
from .models import Channel, Playlist, Video
from .quota import QuotaLedger

FIXTURE_DIR = ROOT / "fixtures"


class YouTubeError(RuntimeError):
    pass


class AuthRequired(YouTubeError):
    """Raised when an operation needs OAuth but only an API key is present."""


# ---------------------------------------------------------------------------
# Handle / URL / ID parsing
# ---------------------------------------------------------------------------

_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_URL_PATTERNS = [
    re.compile(r"youtube\.com/channel/(?P<id>UC[A-Za-z0-9_-]{22})"),
    re.compile(r"youtube\.com/@(?P<handle>[A-Za-z0-9._-]+)"),
    re.compile(r"youtube\.com/c/(?P<legacy>[A-Za-z0-9._-]+)"),
    re.compile(r"youtube\.com/user/(?P<legacy>[A-Za-z0-9._-]+)"),
]


def parse_channel_ref(ref: str) -> tuple[str, str]:
    """Turn anything a user might paste into ('id'|'handle', value).

    Accepts a bare channel id, a bare @handle, or any of the four channel URL
    shapes YouTube still serves.
    """
    ref = ref.strip()
    if _CHANNEL_ID_RE.match(ref):
        return "id", ref
    if ref.startswith("@"):
        return "handle", ref[1:]
    for pat in _URL_PATTERNS:
        m = pat.search(ref)
        if not m:
            continue
        gd = m.groupdict()
        if gd.get("id"):
            return "id", gd["id"]
        if gd.get("handle"):
            return "handle", gd["handle"]
        if gd.get("legacy"):
            return "handle", gd["legacy"]
    # Bare word — assume it's a handle without the @
    return "handle", ref.lstrip("@")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class YouTubeClient:
    def __init__(
        self,
        *,
        ledger: QuotaLedger | None = None,
        fixture: str | None = None,
        owner: bool = False,
    ) -> None:
        self.ledger = ledger or QuotaLedger(budget=CONFIG.quota_budget)
        self.fixture = fixture
        self.is_owner = owner
        self._data = None
        self._analytics = None

        if fixture:
            self.mode = "FIXTURE"
            self._fixture_path = FIXTURE_DIR / f"{fixture}.json"
            if not self._fixture_path.exists():
                raise YouTubeError(
                    f"No fixture named {fixture!r} in {FIXTURE_DIR}")
            self._fixture_data = json.loads(
                self._fixture_path.read_text(encoding="utf-8"))
        elif owner:
            self.mode = "OWNER"
        elif CONFIG.has_api_key:
            self.mode = "PUBLIC"
        else:
            raise YouTubeError(
                "No credentials. Set YOUTUBE_API_KEY in .env for a public scan, "
                "run `pitstop auth` for owner access, or pass --fixture demo "
                "to work offline."
            )

    # -- lazily built service handles ---------------------------------------

    @property
    def data(self):
        if self._data is None:
            from googleapiclient.discovery import build

            if self.mode == "OWNER":
                self._data = build("youtube", "v3",
                                   credentials=self._credentials(),
                                   cache_discovery=False)
            else:
                self._data = build("youtube", "v3",
                                   developerKey=CONFIG.api_key,
                                   cache_discovery=False)
        return self._data

    @property
    def analytics(self):
        if self._analytics is None:
            from googleapiclient.discovery import build

            self._analytics = build("youtubeAnalytics", "v2",
                                    credentials=self._credentials(),
                                    cache_discovery=False)
        return self._analytics

    def _credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        CONFIG.ensure_dirs()
        creds = None
        if CONFIG.token_file.exists():
            creds = Credentials.from_authorized_user_file(
                str(CONFIG.token_file), SCOPES)
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CONFIG.has_oauth:
                raise AuthRequired(
                    f"OAuth client secret not found at "
                    f"{CONFIG.client_secret_file}. See .env.example step 2."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CONFIG.client_secret_file), SCOPES)
            creds = flow.run_local_server(port=0, prompt="consent")
        CONFIG.token_file.write_text(creds.to_json(), encoding="utf-8")
        return creds

    # -- request plumbing ----------------------------------------------------

    def _charge(self, method: str, times: int = 1) -> None:
        self.ledger.charge(method, times)

    def _fixture_get(self, key: str) -> Any:
        return self._fixture_data.get(key)

    def _paged(self, resource_name: str, method: str,
               **kwargs) -> Iterator[dict]:
        """Page through a list endpoint, charging 1 call per page."""
        if self.mode == "FIXTURE":
            for item in self._fixture_get(method) or []:
                yield item
            self._charge(method)
            return

        resource = getattr(self.data, resource_name)()
        request = resource.list(**kwargs)
        while request is not None:
            self._charge(method)
            response = request.execute()
            yield from response.get("items", [])
            request = resource.list_next(request, response)

    # -- reads ---------------------------------------------------------------

    def resolve_channel(self, ref: str) -> tuple[Channel, str]:
        """Resolve any channel reference to (Channel, uploads_playlist_id).

        Note we never touch search.list here. It costs 100 units *and* is capped
        at 100 calls/day regardless of the unit pool — one careless search-based
        resolver would burn a whole day's budget on ~100 lookups. channels.list
        by handle costs 1.
        """
        if self.mode == "FIXTURE":
            raw = self._fixture_get("channel")
            self._charge("channels.list")
            return self._parse_channel(raw), raw["contentDetails"][
                "relatedPlaylists"]["uploads"]

        kind, value = parse_channel_ref(ref)
        params: dict[str, Any] = {
            "part": "snippet,statistics,contentDetails",
        }
        if kind == "id":
            params["id"] = value
        else:
            params["forHandle"] = value

        self._charge("channels.list")
        resp = self.data.channels().list(**params).execute()
        items = resp.get("items", [])
        if not items and kind == "handle":
            # Legacy /c/ and /user/ vanity names aren't handles. forUsername is
            # the documented fallback and still costs 1 unit.
            self._charge("channels.list")
            resp = self.data.channels().list(
                part="snippet,statistics,contentDetails",
                forUsername=value).execute()
            items = resp.get("items", [])
        if not items:
            raise YouTubeError(f"No channel found for {ref!r}")

        raw = items[0]
        uploads = raw["contentDetails"]["relatedPlaylists"]["uploads"]
        return self._parse_channel(raw), uploads

    def list_uploads(self, uploads_playlist_id: str,
                     limit: int | None = None) -> list[str]:
        """Video ids from the channel's uploads playlist. 1 unit per 50."""
        if self.mode == "FIXTURE":
            ids = [v["id"] for v in self._fixture_get("videos")]
            self._charge("playlistItems.list", max(1, -(-len(ids) // 50)))
            return ids[:limit] if limit else ids

        ids: list[str] = []
        for item in self._paged("playlistItems", "playlistItems.list",
                                part="contentDetails",
                                playlistId=uploads_playlist_id,
                                maxResults=50):
            ids.append(item["contentDetails"]["videoId"])
            if limit and len(ids) >= limit:
                break
        return ids

    def hydrate_videos(self, video_ids: list[str]) -> list[Video]:
        """Full video records. videos.list takes 50 ids per call for 1 unit."""
        if self.mode == "FIXTURE":
            wanted = set(video_ids)
            raws = [v for v in self._fixture_get("videos") if v["id"] in wanted]
            self._charge("videos.list", max(1, -(-len(raws) // 50)))
            return [self._parse_video(v) for v in raws]

        out: list[Video] = []
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            self._charge("videos.list")
            resp = self.data.videos().list(
                part="snippet,statistics,contentDetails,status",
                id=",".join(batch), maxResults=50).execute()
            out.extend(self._parse_video(v) for v in resp.get("items", []))
        return out

    def list_playlists(self, channel_id: str) -> list[Playlist]:
        if self.mode == "FIXTURE":
            self._charge("playlists.list")
            out = []
            for raw in self._fixture_get("playlists") or []:
                self._charge("playlistItems.list")
                out.append(Playlist(
                    id=raw["id"], title=raw["title"],
                    item_video_ids=list(raw.get("item_video_ids", [])),
                    broken_video_ids=list(raw.get("broken_video_ids", [])),
                ))
            return out

        playlists: list[Playlist] = []
        for raw in self._paged("playlists", "playlists.list",
                               part="snippet,contentDetails",
                               channelId=channel_id, maxResults=50):
            playlists.append(Playlist(id=raw["id"],
                                      title=raw["snippet"]["title"]))

        for pl in playlists:
            for item in self._paged("playlistItems", "playlistItems.list",
                                    part="contentDetails,status",
                                    playlistId=pl.id, maxResults=50):
                vid = item["contentDetails"]["videoId"]
                pl.item_video_ids.append(vid)
                # privacyStatus on the *item* reveals videos that went
                # private/deleted while still sitting in the playlist. This is
                # the silent breakage nobody notices.
                status = (item.get("status") or {}).get("privacyStatus")
                if status in {"private", "privacyStatusUnspecified"}:
                    pl.broken_video_ids.append(vid)
        return playlists

    def caption_availability(self, video_ids: list[str]) -> dict[str, bool]:
        """Owner-only. captions.list costs 50 units per video — expensive, so
        the caller decides whether it is worth it."""
        if not self.is_owner and self.mode != "FIXTURE":
            raise AuthRequired("Caption state requires OAuth.")
        if self.mode == "FIXTURE":
            self._charge("captions.list")
            return self._fixture_get("captions") or {}
        out = {}
        for vid in video_ids:
            self._charge("captions.list")
            resp = self.data.captions().list(part="snippet",
                                             videoId=vid).execute()
            out[vid] = bool(resp.get("items"))
        return out

    def retention_metrics(self, video_ids: list[str]) -> dict[str, float]:
        """averageViewPercentage per video, from the Analytics API.

        NOTE: impressionClickThroughRate is deliberately absent — it is not
        exposed by the public Analytics API at all, so Pitstop cannot and does
        not claim anything about thumbnail CTR. See ARCHITECTURE.md.

        The Analytics API is not billed against the Data API unit pool.
        """
        if self.mode == "FIXTURE":
            return self._fixture_get("retention") or {}
        if not self.is_owner:
            raise AuthRequired("Analytics requires OAuth as the channel owner.")
        resp = self.analytics.reports().query(
            ids="channel==MINE",
            startDate="2005-02-14",
            endDate=datetime.now(timezone.utc).date().isoformat(),
            metrics="averageViewPercentage",
            dimensions="video",
            filters="video==" + ",".join(video_ids[:200]),
            maxResults=200,
        ).execute()
        return {row[0]: float(row[1]) for row in resp.get("rows", [])}

    # -- writes --------------------------------------------------------------

    def update_video(self, video_id: str, snippet_updates: dict[str, Any],
                     status_updates: dict[str, Any] | None = None) -> None:
        """One videos.update call — 50 units regardless of how many fields.

        The API replaces the whole `snippet` part, so we must read-modify-write:
        omitting categoryId or title on an update silently wipes them. This is
        the single most common way to destroy a channel with this endpoint.
        """
        if self.mode == "FIXTURE":
            self._charge("videos.update")
            return
        if not self.is_owner:
            raise AuthRequired("Writing requires OAuth as the channel owner.")

        self._charge("videos.list")
        current = self.data.videos().list(
            part="snippet,status", id=video_id).execute()
        items = current.get("items", [])
        if not items:
            raise YouTubeError(f"Video {video_id} not found or not yours.")

        snippet = items[0]["snippet"]
        snippet.update(snippet_updates)
        body: dict[str, Any] = {"id": video_id, "snippet": snippet}
        parts = ["snippet"]
        if status_updates:
            status = items[0]["status"]
            status.update(status_updates)
            body["status"] = status
            parts.append("status")

        self._charge("videos.update")
        self.data.videos().update(part=",".join(parts), body=body).execute()

    def add_to_playlist(self, playlist_id: str, video_id: str) -> None:
        if self.mode == "FIXTURE":
            self._charge("playlistItems.insert")
            return
        if not self.is_owner:
            raise AuthRequired("Writing requires OAuth as the channel owner.")
        self._charge("playlistItems.insert")
        self.data.playlistItems().insert(
            part="snippet",
            body={"snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }},
        ).execute()

    def create_playlist(self, title: str, description: str = "") -> str:
        if self.mode == "FIXTURE":
            self._charge("playlists.insert")
            return "PL_FIXTURE"
        if not self.is_owner:
            raise AuthRequired("Writing requires OAuth as the channel owner.")
        self._charge("playlists.insert")
        resp = self.data.playlists().insert(
            part="snippet,status",
            body={"snippet": {"title": title, "description": description},
                  "status": {"privacyStatus": "public"}},
        ).execute()
        return resp["id"]

    # -- parsing -------------------------------------------------------------

    @staticmethod
    def _parse_channel(raw: dict) -> Channel:
        sn = raw.get("snippet", {})
        st = raw.get("statistics", {})
        return Channel(
            id=raw["id"],
            title=sn.get("title", ""),
            handle=sn.get("customUrl", "").lstrip("@"),
            subscriber_count=int(st.get("subscriberCount", 0) or 0),
            video_count=int(st.get("videoCount", 0) or 0),
            view_count=int(st.get("viewCount", 0) or 0),
            thumbnail_url=sn.get("thumbnails", {}).get(
                "high", {}).get("url", ""),
        )

    @staticmethod
    def _parse_video(raw: dict) -> Video:
        sn = raw.get("snippet", {})
        st = raw.get("statistics", {})
        cd = raw.get("contentDetails", {})
        status = raw.get("status", {})
        return Video(
            id=raw["id"],
            title=sn.get("title", ""),
            description=sn.get("description", ""),
            published_at=_parse_dt(sn.get("publishedAt")),
            tags=sn.get("tags", []) or [],
            category_id=sn.get("categoryId", ""),
            duration_seconds=_parse_duration(cd.get("duration", "PT0S")),
            privacy_status=status.get("privacyStatus", "public"),
            view_count=int(st.get("viewCount", 0) or 0),
            like_count=int(st.get("likeCount", 0) or 0),
            comment_count=int(st.get("commentCount", 0) or 0),
            thumbnail_url=sn.get("thumbnails", {}).get(
                "high", {}).get("url", ""),
            caption_available=(cd.get("caption") == "true"
                               if "caption" in cd else None),
        )


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


_DUR_RE = re.compile(
    r"P(?:(?P<d>\d+)D)?T?(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?")


def _parse_duration(iso: str) -> int:
    m = _DUR_RE.match(iso or "")
    if not m:
        return 0
    g = {k: int(v) if v else 0 for k, v in m.groupdict().items()}
    return g["d"] * 86400 + g["h"] * 3600 + g["m"] * 60 + g["s"]
