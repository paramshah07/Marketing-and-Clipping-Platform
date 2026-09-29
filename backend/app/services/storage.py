"""File storage under DATA_DIR. A key is a relative path such as 'u/2/renders/12.mp4': a user's files live under
u/{user_id}/ (key()); keys from before users, with no prefix, are user 1's (owner()). The api serves DATA_DIR at
/media, each file to its owner only, so a key's URL is /media/<key>.

ponytail: local disk only (localhost-first, PLAN D10). R2 replaces this module, same four functions,
when Clipper leaves localhost. ponytail: legacy keys = the operator's; mv data/* data/u/1 + rewrite the *_key
columns if it bites. Blocking I/O: call from sync tasks or a threadpool.
"""

import shutil
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from sqlalchemy import text

from app.core.config import settings

# A user's storage: their clips and renders (logos and covers are small), and their cap (null: unlimited)
USAGE = text(
    "SELECT u.quota_bytes, (SELECT coalesce(sum(size_bytes), 0) FROM source_clips WHERE user_id = u.id)"
    " + (SELECT coalesce(sum(size_bytes), 0) FROM renders WHERE user_id = u.id) FROM users u WHERE u.id = :u"
)


def key(user_id: int, rel: str) -> str:
    """A new file's key, e.g. key(2, 'raw/5.mp4') = 'u/2/raw/5.mp4'."""
    return f"u/{user_id}/{rel}"


def owner(key: str) -> int | None:
    """Whose file a key (or /media path) is: u/{id}/... is that user's, a key without the prefix user 1's.
    None for a u/ path without a user id."""
    parts = key.split("/")
    if parts[0] != "u":
        return 1
    uid = parts[1] if len(parts) > 2 else ""
    return int(uid) if uid.isascii() and uid.isdecimal() else None


def room(used: int, quota: int | None, adding: int = 0) -> tuple[str, str] | None:
    """(code, message) when adding bytes would take DATA_DIR's disk under MIN_FREE_BYTES, or a user who has `used`
    bytes of their quota to it; None when there's room."""
    if shutil.disk_usage(settings.DATA_DIR).free - adding < settings.MIN_FREE_BYTES:
        return "DISK_FULL", "the server is almost out of disk space: try again later"
    if quota is not None and used + adding >= quota:
        return "QUOTA_EXCEEDED", (f"your storage is full ({used / 1024**3:.1f} of {quota / 1024**3:g} GB): "
                                  "delete clips or renders to make room")  # fmt: skip
    return None


def path_for(key: str) -> Path:
    root = settings.DATA_DIR.resolve()
    path = (root / key).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f"storage key escapes DATA_DIR: {key!r}")
    return path


def save(key: str, stream: BinaryIO) -> Path:
    """Copy a stream to key. Written to a .part file and renamed, so readers never see half a file."""
    path = path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    with part.open("wb") as f:
        shutil.copyfileobj(stream, f, 1024 * 1024)
    part.replace(path)
    return path


def copy(src: str, dst: str) -> None:
    path = path_for(dst)
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path_for(src), path)


def url_for(key: str) -> str:
    return "/media/" + quote(key)


def delete(key: str) -> None:
    path_for(key).unlink(missing_ok=True)
