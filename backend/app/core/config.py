from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Read from the environment (compose passes .env). Names match docs/PLAN.md section 6."""

    DATABASE_URL: str  # postgresql+psycopg://..., set by compose
    DATA_DIR: Path  # set by compose
    ZERNIO_API_KEY: str = ""  # required to publish
    ZERNIO_BASE_URL: str = "https://zernio.com/api/v1"
    # Meta's API Reel limit (15 min). Zernio's docs say 90 s, but a 120 s Reel published live on 2026-09-26.
    ZERNIO_MAX_REEL_SECONDS: int = 900
    ZERNIO_MIN_REEL_SECONDS: int = 3
    APP_BASE_URL: str = "http://localhost:5173"
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    PUBLISHING_ENABLED: bool = False
    PUBLISH_DEBUG_PAUSE: Literal["", "after_upload", "before_post", "after_post"] = ""
    FFMPEG_THREADS: int = 2
    YTDLP_COOKIES_FILE: str = ""
    MAX_UPLOAD_BYTES: int = 2 * 1024**3

    @field_validator("ZERNIO_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "YTDLP_COOKIES_FILE", mode="before")
    @classmethod
    def blank_if_comment(cls, v):
        # compose's env_file keeps `KEY=   # comment` as the value "# comment"; treat it as unset
        return "" if isinstance(v, str) and v.lstrip().startswith("#") else v


settings = Settings()
