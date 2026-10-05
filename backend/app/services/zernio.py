"""Zernio REST API (https://docs.zernio.com): the read-only calls. Every call takes the user's own key
(users.zernio_key_enc, unsealed by the caller). Never log a request's headers: they hold the key."""

import time

import httpx

from app.core.config import settings
from app.schemas import Quota

QUOTA_TTL_S = 300
_quota_cache: dict[str, tuple[float, Quota | None]] = {}  # zernio_account_id -> (monotonic, quota)


class ZernioError(Exception):
    """status: Zernio's HTTP status (401: the key is refused), None when there was no answer or no key. code: the
    error body's documented `code`, if any."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status, self.code = status, code


def client(key: str | None, **kw) -> httpx.AsyncClient:
    if not key:
        raise ZernioError("no Zernio key: add yours in Settings")
    return httpx.AsyncClient(
        base_url=settings.ZERNIO_BASE_URL,
        headers={"Authorization": f"Bearer {key}"},
        timeout=kw.pop("timeout", 20),
        **kw,
    )


async def _get(key: str | None, path: str, **params) -> dict:
    try:
        async with client(key) as c:
            r = await c.get(path, params=params)
    except httpx.HTTPError as e:
        raise ZernioError(f"GET {path}: {type(e).__name__}") from e
    if not r.is_success:
        try:
            code = r.json().get("code")
        except (ValueError, AttributeError):  # not JSON, or not an object
            code = None
        raise ZernioError(f"GET {path}: HTTP {r.status_code} {r.text[:200]}", r.status_code, code)
    return r.json()


async def verify(key: str) -> dict:
    """GET /v1/auth/verify (api-keys/verify-credential): {valid, userId, name, email, authType, scope} for a
    working key, ZernioError with status 401 for one Zernio refuses. Reads no data."""
    return await _get(key, "/auth/verify")


async def list_accounts(key: str | None, over_limit: bool = False) -> dict:
    """GET /v1/accounts (raw body). Zernio lists only the accounts within the plan's limit unless over_limit
    (includeOverLimit=true). Raises ZernioError on any failure."""
    return await _get(key, "/accounts", **({"includeOverLimit": "true"} if over_limit else {}))


async def search_audio(key: str | None, zernio_account_id: str, kind: str, q: str | None = None) -> list[dict]:
    """GET /v1/accounts/{id}/instagram/audio (instagram/search-instagram-audio): up to ~30 assets of Instagram's
    catalog, kind music or original_sound, trending without q. Only through an account connected with Facebook
    Login: an Instagram Login one is a 400 with code instagram_audio_requires_facebook_login."""
    path = f"/accounts/{zernio_account_id}/instagram/audio"
    return (await _get(key, path, audioType=kind, **({"q": q} if q else {}))).get("audio") or []


def parse_accounts(body: dict) -> list[dict]:
    """Instagram accounts from a GET /v1/accounts body, as Account column values (connection fields only;
    operator fields are the database's)."""
    out = []
    for a in body.get("accounts", []):
        if a.get("platform") != "instagram":
            continue
        profile = a.get("profileId")
        out.append({
            "zernio_account_id": a["_id"],
            "zernio_profile_id": profile["_id"] if isinstance(profile, dict) else profile,
            "username": a.get("username") or a.get("displayName") or a["_id"],
            "avatar_url": a.get("profilePicture"),
            "connection_status": "connected" if a.get("isActive") and not a.get("needsReconnection") else "disconnected",
        })  # fmt: skip
    return out


def parse_quota(body: dict) -> Quota:
    return Quota(used=body["quotaUsage"], total=body["quotaTotal"], duration_s=body["quotaDurationSeconds"])


async def publishing_limit(key: str | None, zernio_account_id: str) -> Quota | None:
    """GET /v1/accounts/{id}/instagram/publishing-limit, cached 5 min (failures too, so a Zernio outage
    costs one timeout per 5 min, not one per page load). None when it can't be read, or there is no key."""
    if not key:
        return None
    hit = _quota_cache.get(zernio_account_id)
    if hit and time.monotonic() - hit[0] < QUOTA_TTL_S:
        return hit[1]
    quota = None
    try:
        async with client(key, timeout=5) as c:
            r = await c.get(f"/accounts/{zernio_account_id}/instagram/publishing-limit")
        if r.is_success:
            quota = parse_quota(r.json())
    except (ZernioError, httpx.HTTPError, KeyError, ValueError):
        pass
    _quota_cache[zernio_account_id] = (time.monotonic(), quota)
    return quota
