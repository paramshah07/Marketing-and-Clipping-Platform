"""Post error codes: classify Zernio's errorCategory, and the plain-language cause + one remedy per code."""

from app.core.config import settings
from app.schemas import Remedy

# docs.zernio.com/guides/post-lifecycle: platforms[].errorCategory -> our error_code
CATEGORY = {
    "auth_expired": "ACCOUNT_DISCONNECTED",
    "account_issue": "ACCOUNT_DISCONNECTED",
    "platform_rate_limit": "RATE_LIMITED",
    "quota_exhausted": "RATE_LIMITED",
    "user_content": "CONTENT_REJECTED",
    "platform_rejected": "CONTENT_REJECTED",
    "platform_error": "NETWORK_ERROR",
    "system_error": "NETWORK_ERROR",
    "user_abuse": "UNKNOWN",
    "unknown": "UNKNOWN",
}


def classify(error_category: str | None) -> str:
    """Zernio platforms[].errorCategory -> error_code. Never look at message strings."""
    return CATEGORY.get(error_category or "", "UNKNOWN")


RECONNECT = Remedy(action="reconnect", label="Reconnect account")
RERENDER = Remedy(action="rerender", label="Re-render and retry")
AUTO = Remedy(action="auto", label="Moved to next free slot")
RETRY = Remedy(action="retry", label="Retry now")

CAUSES: dict[str, tuple[str, Remedy]] = {
    "ACCOUNT_DISCONNECTED": ("The Instagram account is disconnected in Zernio (login expired, revoked or a setup "
                             "problem). Reconnect it in Zernio; its failed posts then move to the next free slots.",
                             RECONNECT),
    "CONTENT_REJECTED": ("Instagram rejected the video or caption (format, length or policy).", RERENDER),
    "RATE_LIMITED": ("Instagram or Zernio rate limit reached. The post was moved to the next free slot.", AUTO),
    "NETWORK_ERROR": ("Zernio or Instagram kept failing with a temporary error, three retries in a row.", RETRY),
    "UNKNOWN": ("Publishing failed for a reason Clipper could not classify. The details below have the message.",
                RETRY),
    "TOO_LONG": (f"The render is longer than {settings.ZERNIO_MAX_REEL_SECONDS // 60} min, the Reel limit.", RETRY),
    "TOO_SHORT": (f"The render is shorter than {settings.ZERNIO_MIN_REEL_SECONDS} s, the Reel minimum.", RETRY),
    "RENDER_FAILED": ("The render failed, so there was nothing to publish.", RERENDER),
    "WORKER_CRASHED": ("The worker stopped in the middle of publishing, several times in a row.", RETRY),
    "MISSED": ("Clipper was not running at the scheduled time. The post was moved to the next free slot.", AUTO),
    "NO_FREE_SLOT": ("The post had to move, but the account has no free slot in the next 30 days.", RETRY),
    "WINDOW_EXPIRED": ("Zernio can no longer tell whether this Reel went out: the first attempt was over 20 h ago. "
                       "Check Instagram first; only if it is not there, re-render and schedule it again.", RERENDER),
}  # fmt: skip


def describe(error_code: str | None) -> tuple[str | None, Remedy | None]:
    """(cause, remedy) for the recovery screen and PostOut. Pure; (None, None) when there is no error."""
    if not error_code:
        return None, None
    return CAUSES.get(error_code, (f"Publishing failed ({error_code}).", RETRY))
