"""Zernio REST API (https://docs.zernio.com). Phase 4 part: read-only account calls.
Never log a request's headers: they hold the API key."""

import time

import httpx

from app.core.config import settings
from app.schemas import Quota

QUOTA_TTL_S = 300
_quota_cache: dict[str, tuple[float, Quota | None]] = {}  # zernio_account_id -> (monotonic, quota)


class ZernioError(Exception):
    pass


def client(**kw) -> httpx.AsyncClient:
    if not settings.ZERNIO_API_KEY:
        raise ZernioError("ZERNIO_API_KEY is not set")
    return httpx.AsyncClient(
        base_url=settings.ZERNIO_BASE_URL,
        headers={"Authorization": f"Bearer {settings.ZERNIO_API_KEY}"},
        timeout=kw.pop("timeout", 20),
        **kw,
    )


async def list_accounts() -> dict:
    """GET /v1/accounts (raw body). Raises ZernioError on any failure."""
    try:
        async with client() as c:
            r = await c.get("/accounts")
    except httpx.HTTPError as e:
        raise ZernioError(f"GET /accounts: {type(e).__name__}") from e
    if not r.is_success:
        raise ZernioError(f"GET /accounts: HTTP {r.status_code} {r.text[:200]}")
    return r.json()


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


async def publishing_limit(zernio_account_id: str) -> Quota | None:
    """GET /v1/accounts/{id}/instagram/publishing-limit, cached 5 min (failures too, so a Zernio outage
    costs one timeout per 5 min, not one per page load). None when it can't be read."""
    hit = _quota_cache.get(zernio_account_id)
    if hit and time.monotonic() - hit[0] < QUOTA_TTL_S:
        return hit[1]
    quota = None
    try:
        async with client(timeout=5) as c:
            r = await c.get(f"/accounts/{zernio_account_id}/instagram/publishing-limit")
        if r.is_success:
            quota = parse_quota(r.json())
    except (ZernioError, httpx.HTTPError, KeyError, ValueError):
        pass
    _quota_cache[zernio_account_id] = (time.monotonic(), quota)
    return quota
