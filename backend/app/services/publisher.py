"""Zernio publish calls (docs.zernio.com: media/get-media-presigned-url, posts/create-post, posts/get-post,
posts/retry-post). HTTP only, no database: the state machine is app/tasks/publish.py. Every call returns a
Zernio post dict or raises NetworkError / Later / Rejected. Never log request headers (API key)."""

from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import httpx

from app.services import zernio
from app.services.errors import classify

IN_FLIGHT = {"scheduled", "publishing", "pending", "processing", "uploading"}
# Rejections about the user's key or Zernio account, not the post: the user's key goes invalid (publish._finish)
KEY_CODES = {"ZERNIO_KEY_INVALID", "ZERNIO_PAYMENT_REQUIRED"}


class NetworkError(Exception):
    """Timeout, transport error or 5xx: retry with the same Idempotency-Key (Zernio replays, no second post)."""


class Later(Exception):
    """409 idempotency_conflict (first request still running), 409 retry-in-progress or 429: again in `seconds`."""

    def __init__(self, seconds: int, body: dict):
        super().__init__(f"retry in {seconds} s")
        self.seconds, self.body = seconds, body


class Rejected(Exception):
    """Zernio answered and nothing was published; `code` is our error_code."""

    def __init__(self, code: str, status: int, body: dict):
        super().__init__(f"{code} (HTTP {status})")
        self.code, self.status, self.body = code, status, body


def client(
    key: str | None, timeout: httpx.Timeout | float = httpx.Timeout(30, read=300), **kw  # noqa: B008
) -> httpx.AsyncClient:
    """Zernio client for the user's key, by default with a 300 s read timeout (publishNow waits for Instagram);
    pass a short one for a quick GET inside an API request. Raises zernio.ZernioError without a key."""
    return zernio.client(key, timeout=timeout, **kw)


def _seconds(retry_after: str | None) -> int:
    try:
        return max(1, int(retry_after or 60))
    except ValueError:  # an HTTP-date: not worth parsing
        return 60


async def _send(c: httpx.AsyncClient, req: httpx.Request) -> httpx.Response:
    try:
        return await c.send(req)
    except httpx.TransportError as e:  # connect/read/write timeouts included
        raise NetworkError(f"{req.method} {req.url.path}: {type(e).__name__}") from e


async def _call(c: httpx.AsyncClient, method: str, url: str, **kw) -> dict:
    r = await _send(c, c.build_request(method, url, **kw))
    if r.status_code >= 500:
        raise NetworkError(f"{method} {url}: HTTP {r.status_code}")
    try:
        body = r.json() if r.content else {}
    except ValueError:
        if r.is_success:  # a 2xx we can't read: replaying with the same key is safe
            raise NetworkError(f"{method} {url}: HTTP {r.status_code}, body is not JSON") from None
        body = {"text": r.text[:500]}
    if r.status_code == 429 or (r.status_code == 409 and body.get("code") == "idempotency_conflict"):
        raise Later(_seconds(r.headers.get("Retry-After")), body)
    # On status, code, required_group and type only (docs: ErrorResponse, ResourceGroupForbidden, create/retry-post),
    # never the message. The key or the Zernio account itself: 401, 402 (a failed payment), an authentication_error,
    # a 403 naming the resource group a restricted (zrk_) key lacks, or any 403 on presign (it names no account).
    # A 403 without those is about the post or its account (not the key's, outside its profiles): per post.
    if (r.status_code in (401, 402) or body.get("type") == "authentication_error"
            or (r.status_code == 403 and (body.get("required_group") or url == "/media/presign"))):  # fmt: skip
        raise Rejected("ZERNIO_PAYMENT_REQUIRED" if r.status_code == 402 else "ZERNIO_KEY_INVALID", r.status_code, body)
    if r.status_code == 403 and body.get("code") in ("ACCOUNT_DISCONNECTED", "PROFILE_OVER_LIMIT"):
        raise Rejected(body["code"], 403, body)
    if r.status_code == 400 and body.get("code") == "instagram_audio_requires_facebook_login":
        raise Rejected("MUSIC_NEEDS_FACEBOOK_LOGIN", 400, body)
    if r.status_code >= 400:
        raise Rejected("UNKNOWN", r.status_code, body)
    return body


async def _chunks(path: Path) -> AsyncIterator[bytes]:
    async with await anyio.open_file(path, "rb") as f:  # reads run in a thread: the event loop never blocks
        while chunk := await f.read(1 << 20):
            yield chunk


async def upload(c: httpx.AsyncClient, path: Path, content_type: str = "video/mp4") -> str:
    """Presign, PUT the file (streamed, never read into memory), return its publicUrl (mediaItems, or the
    cover's instagramThumbnail)."""
    size = (await anyio.Path(path).stat()).st_size
    pre = await _call(
        c, "POST", "/media/presign", json={"filename": path.name, "contentType": content_type, "size": size}
    )
    req = c.build_request(
        "PUT",
        pre["uploadUrl"],
        content=_chunks(path),
        headers={"Content-Type": content_type, "Content-Length": str(size)},
    )
    req.headers.pop("Authorization", None)  # a presigned URL is its own credential: never hand our key to the bucket
    r = await _send(c, req)
    if not r.is_success:  # e.g. an expired signature: the URL was never committed, so the retry presigns again
        raise NetworkError(f"PUT upload: HTTP {r.status_code}")
    return pre["publicUrl"]


async def create_post(
    c: httpx.AsyncClient, key: str, caption: str, media_url: str, zernio_account_id: str, cover_url: str | None = None,
    music: dict | None = None, audio_name: str | None = None,
) -> dict:
    """POST /v1/posts with publishNow and the post's Idempotency-Key. A 409 duplicate resolves to the
    existing post (GET details.existingPostId). cover_url: the Reel cover (instagramThumbnail), sent only when set.
    music: posts.music, Instagram's catalog track (audioConfiguration), sent only when set. audio_name: the label of
    the Reel's own audio instead of "Original audio" (audioName: the song mixed into the render)."""
    ig = {"shareToFeed": True} | ({"instagramThumbnail": cover_url} if cover_url else {})
    if audio_name:
        ig["audioName"] = audio_name
    if music:
        ig["audioConfiguration"] = {
            "audioId": music["id"], "audioVolume": music["volume"], "videoVolume": music["video_volume"]
        }
    body = {
        "content": caption,
        "mediaItems": [{"type": "video", "url": media_url}],
        "platforms": [{"platform": "instagram", "accountId": zernio_account_id, "platformSpecificData": ig}],
        "publishNow": True,
    }
    try:
        return (await _call(c, "POST", "/posts", json=body, headers={"Idempotency-Key": key}))["post"]
    except Rejected as e:
        existing = e.status == 409 and (e.body.get("details") or {}).get("existingPostId")
        if not existing:
            raise
        return await get_post(c, existing)


async def get_post(c: httpx.AsyncClient, zernio_post_id: str) -> dict:
    return (await _call(c, "GET", f"/posts/{zernio_post_id}"))["post"]


async def retry_post(c: httpx.AsyncClient, zernio_post_id: str) -> dict:
    """POST /v1/posts/{id}/retry: Zernio re-publishes the failed platforms of the same post (no new post)."""
    try:
        return (await _call(c, "POST", f"/posts/{zernio_post_id}/retry"))["post"]
    except Rejected as e:
        if e.status == 409:  # "Post is currently publishing"
            raise Later(60, e.body) from e
        raise


def instagram(zpost: dict) -> dict:
    return next((p for p in zpost.get("platforms") or [] if p.get("platform") == "instagram"), {})


def outcome(zpost: dict) -> str:
    """PUBLISHED, PROCESSING (Zernio is still on it, poll) or an error_code. Branches on post.status
    (a 207 is not success); on `partial`, on our single platform's status."""
    status = zpost.get("status")
    if status == "partial":
        status = instagram(zpost).get("status")
    if status == "published":
        return "PUBLISHED"
    if status in IN_FLIGHT:
        return "PROCESSING"
    return classify(instagram(zpost).get("errorCategory"))
