"""Configuration, loaded once from the environment.

Every knob has a working default so `pitstop scan --fixture` runs on a clean
checkout with no credentials at all. That matters for two reasons: contributors
can work offline, and the demo has a path that cannot fail live on stage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent

# Scopes: force-ssl is the only one that permits videos.update / playlistItems
# writes. readonly is not enough. yt-analytics gives averageViewPercentage,
# which we use to rank findings by impact.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _path(env_key: str, default: str) -> Path:
    raw = os.getenv(env_key, default)
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


@dataclass(frozen=True)
class Config:
    api_key: str | None
    client_secret_file: Path
    token_file: Path
    db_path: Path
    quota_budget: int
    require_confirm: bool

    featherless_api_key: str | None
    featherless_base_url: str
    featherless_model: str
    gemini_router_base_url: str
    gemini_router_model: str

    @classmethod
    def load(cls) -> "Config":
        return cls(
            api_key=os.getenv("YOUTUBE_API_KEY") or None,
            client_secret_file=_path("PITSTOP_CLIENT_SECRET_FILE",
                                     "client_secret.json"),
            token_file=_path("PITSTOP_TOKEN_FILE", ".pitstop/token.json"),
            db_path=_path("PITSTOP_DB", ".pitstop/pitstop.db"),
            quota_budget=int(os.getenv("PITSTOP_QUOTA_BUDGET", "9000")),
            require_confirm=os.getenv("PITSTOP_REQUIRE_CONFIRM", "true").lower()
            not in {"false", "0", "no"},
            featherless_api_key=os.getenv("FEATHERLESS_API_KEY") or None,
            featherless_base_url=os.getenv(
                "FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1"),
            featherless_model=os.getenv(
                "FEATHERLESS_MODEL", "deepseek-ai/DeepSeek-V3-0324"),
            gemini_router_base_url=os.getenv(
                "GEMINI_ROUTER_BASE_URL", "http://127.0.0.1:8787/v1"),
            gemini_router_model=os.getenv(
                "GEMINI_ROUTER_MODEL", "gemini-2.5-flash"),
        )

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def has_oauth(self) -> bool:
        return self.client_secret_file.exists()

    @property
    def has_llm(self) -> bool:
        return bool(self.featherless_api_key)

    def ensure_dirs(self) -> None:
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


CONFIG = Config.load()
