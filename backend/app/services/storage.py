"""File storage under DATA_DIR. A key is a relative path such as 'renders/12.mp4'; the api serves
DATA_DIR at /media, so a key's URL is /media/<key>.

ponytail: local disk only (localhost-first, PLAN D10). R2 replaces this module, same four functions,
when Clipper leaves localhost. Blocking I/O: call from sync tasks or a threadpool.
"""

import shutil
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from app.core.config import settings


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


def url_for(key: str) -> str:
    return "/media/" + quote(key)


def delete(key: str) -> None:
    path_for(key).unlink(missing_ok=True)
