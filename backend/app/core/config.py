from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Read from the environment (compose passes .env). Names match docs/PLAN.md section 6."""

    DATABASE_URL: str  # postgresql+psycopg://..., set by compose
    DATA_DIR: Path  # set by compose
    # Each user's own key is in users.zernio_key_enc; this one is read once, by `cli bootstrap` (user 1's import)
    ZERNIO_API_KEY: str = ""
    ZERNIO_BASE_URL: str = "https://zernio.com/api/v1"
    # Fernet key(s) sealing users' secrets (app.core.secrets), comma-separated: the first seals, any opens.
    # Only the api, publisher and migrate get it; blank: no key can be read (every user's Zernio key is "missing").
    SECRETS_KEY: str = ""
    # Meta's API Reel limit (15 min). Zernio's docs say 90 s, but a 120 s Reel published live on 2026-09-26.
    ZERNIO_MAX_REEL_SECONDS: int = 900
    ZERNIO_MIN_REEL_SECONDS: int = 3
    # Prefixed to every post's Idempotency-Key. Empty in production (its keys never change); staging sets its own, so a
    # post there never shares a key with one in production: Zernio replays a key per Zernio user, whatever the body.
    IDEMPOTENCY_SALT: str = ""
    APP_BASE_URL: str = "http://localhost:5173"
    STATIC_DIR: Path | None = None  # production: the built frontend, served at / (compose.prod.yml); dev uses Vite
    # The operator's .env bots, read once, by `cli bootstrap` (user 1's import into telegram_bots): the first gets
    # the alerts, _2 and _3 are interactive only. Every user's bots live in telegram_bots.
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_BOT_TOKEN_2: str = ""
    TELEGRAM_CHAT_ID_2: str = ""
    TELEGRAM_BOT_TOKEN_3: str = ""
    TELEGRAM_CHAT_ID_3: str = ""
    CLIPPER_API_URL: str = "http://api:8000"  # the api as the bot container sees it
    PUBLISHING_ENABLED: bool = False
    PUBLISH_DEBUG_PAUSE: Literal["", "after_upload", "before_post", "after_post"] = ""
    FFMPEG_THREADS: int = 2
    YTDLP_COOKIES_FILE: str = ""
    MAX_UPLOAD_BYTES: int = 2 * 1024**3
    # Users (docs: multi-user). Open signup until MAX_USERS non-disabled users exist (the operator counts).
    MAX_USERS: int = 15
    USER_QUOTA_BYTES: int = 5 * 1024**3  # a new user's storage cap (users.quota_bytes)
    MIN_FREE_BYTES: int = 3 * 1024**3  # no upload, import or render when DATA_DIR's disk has less free
    # The bot service's credential: the api honours X-Clipper-User only with this bearer. Blank: bots off.
    BOT_SERVICE_SECRET: str = ""
    APP_DB_PASSWORD: str = "clipper_app"  # the api's role (cli db-grants); postgres is never published off-host
    # bootstrap only (the migrate service): user 1's username, and its password as a bcrypt hash (or base64 of one)
    CLIPPER_USER: str = ""
    CLIPPER_PASSWORD_HASH: str = ""

    @field_validator("ZERNIO_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_BOT_TOKEN_2", "TELEGRAM_CHAT_ID_2",
                     "TELEGRAM_BOT_TOKEN_3", "TELEGRAM_CHAT_ID_3", "YTDLP_COOKIES_FILE", mode="before")  # fmt: skip
    @classmethod
    def blank_if_comment(cls, v):
        # compose's env_file keeps `KEY=   # comment` as the value "# comment"; treat it as unset
        return "" if isinstance(v, str) and v.lstrip().startswith("#") else v


settings = Settings()
