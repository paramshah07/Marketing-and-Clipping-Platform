"""The bot's two HTTP clients: Telegram's Bot API, and Clipper's own api (the endpoints the web app uses).
Nothing here logs or raises a Telegram URL: it holds the bot token."""

import asyncio
import json
import logging

import httpx

from app.core.config import settings

logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs request URLs at INFO

UNREACHABLE = "Clipper isn't answering right now. Try again in a moment."


class TelegramError(Exception):
    def __init__(self, code: int, description: str):
        super().__init__(f"Telegram {code}: {description}")
        self.code, self.description = code, description


class Telegram:
    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self.token = token
        # read > getUpdates' 50 s long poll
        self.http = httpx.AsyncClient(base_url="https://api.telegram.org", timeout=httpx.Timeout(30, read=75), transport=transport)

    async def __call__(self, method: str, files: dict | None = None, **params):
        """One Bot API call -> its result. None values are dropped; files ({field: (name, bytes, mime)}) make it
        multipart. A 429 is waited out once; then TelegramError."""
        params = {k: v for k, v in params.items() if v is not None}
        for attempt in (1, 2):
            try:
                if files:
                    data = {k: v if isinstance(v, str) else json.dumps(v) for k, v in params.items()}
                    r = await self.http.post(f"/bot{self.token}/{method}", data=data, files=files)
                else:
                    r = await self.http.post(f"/bot{self.token}/{method}", json=params)
                body = r.json()
            except (httpx.HTTPError, ValueError) as e:
                raise TelegramError(0, type(e).__name__) from None  # its message would carry the URL
            if body.get("ok"):
                return body.get("result")
            wait = (body.get("parameters") or {}).get("retry_after")
            if attempt == 1 and wait and wait <= 30:
                await asyncio.sleep(wait)
                continue
            raise TelegramError(body.get("error_code") or r.status_code, body.get("description") or "")

    async def download(self, file_id: str) -> bytes:
        """A file the operator sent (bots can download up to 20 MB)."""
        path = (await self("getFile", file_id=file_id))["file_path"]
        try:
            r = await self.http.get(f"/file/bot{self.token}/{path}")
        except httpx.HTTPError as e:
            raise TelegramError(0, type(e).__name__) from None
        if not r.is_success:
            raise TelegramError(r.status_code, "file download failed")
        return r.content


class ApiError(Exception):
    """A refused api call: message is what the web app would show; code is e.g. STATE_CONFLICT, detail the body."""

    def __init__(self, status: int, message: str, code: str | None = None, detail: dict | None = None):
        super().__init__(message)
        self.status, self.message, self.code, self.detail = status, message, code, detail or {}


def _error(status: int, body) -> ApiError:
    d = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(d, dict):  # {"code": ..., "message": ...} (scheduling / recovery)
        return ApiError(status, d.get("message") or json.dumps(d)[:200], d.get("code"), d)
    if isinstance(d, list):  # FastAPI validation errors
        return ApiError(status, "; ".join(f"{'.'.join(map(str, x.get('loc', [])[1:]))}: {x.get('msg')}" for x in d if isinstance(x, dict)))
    return ApiError(status, str(d) if d else f"HTTP {status}")


class Clipper:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None, user_id: int | None = 1):
        # every call (and /media fetch) as user_id: the api honours X-Clipper-User only with the service's bearer.
        # None: the service itself (the supervisor's /api/internal/* calls)
        headers = {"Authorization": f"Bearer {settings.BOT_SERVICE_SECRET}"}
        if user_id is not None:
            headers["X-Clipper-User"] = str(user_id)
        self.http = httpx.AsyncClient(base_url=base_url, timeout=120, transport=transport, headers=headers)

    async def __call__(self, method: str, path: str, **kw):
        try:
            r = await self.http.request(method, path, **kw)
        except httpx.HTTPError:
            raise ApiError(0, UNREACHABLE, "UNREACHABLE") from None
        if r.status_code == 204:
            return None
        try:
            body = r.json()
        except ValueError:
            body = r.text
        if not r.is_success:
            raise _error(r.status_code, body)
        return body

    async def get(self, path: str, **params):
        return await self("GET", path, params={k: v for k, v in params.items() if v is not None})

    async def post(self, path: str, json: dict | None = None, **kw):
        return await self("POST", path, json=json, **kw)

    async def patch(self, path: str, json: dict):
        return await self("PATCH", path, json=json)

    async def delete(self, path: str):
        return await self("DELETE", path)

    async def media(self, url: str) -> bytes:
        """A file under /media (thumbnail, logo, MP4)."""
        try:
            r = await self.http.get(url)
        except httpx.HTTPError:
            raise ApiError(0, UNREACHABLE, "UNREACHABLE") from None
        if not r.is_success:
            raise ApiError(r.status_code, f"{url}: HTTP {r.status_code}")
        return r.content
