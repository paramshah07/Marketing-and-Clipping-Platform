"""Users' secrets at rest (users.zernio_key_enc): Fernet under SECRETS_KEY. A comma-separated SECRETS_KEY is a
MultiFernet: the first key seals, any of them opens, so a new key goes first and the old one stays until every
value is re-sealed. A database dump or the review stack's copy holds only sealed values; without the key they
read as "no key"."""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings


def _fernet() -> MultiFernet | None:
    keys = [k.strip() for k in settings.SECRETS_KEY.split(",") if k.strip()]
    try:
        return MultiFernet([Fernet(k) for k in keys]) if keys else None
    except ValueError:  # not a Fernet key
        return None


def ready() -> bool:
    """SECRETS_KEY holds a Fernet key: values can be sealed."""
    return _fernet() is not None


def seal(value: str) -> bytes:
    if (f := _fernet()) is None:
        raise RuntimeError("SECRETS_KEY is not set (or not a Fernet key): secrets can't be stored")
    return f.encrypt(value.encode())


def unseal(token: bytes | None) -> str | None:
    """The value, or None when there is none, SECRETS_KEY is blank, or it can't open it (another key)."""
    if not token or (f := _fernet()) is None:
        return None
    try:
        return f.decrypt(bytes(token)).decode()
    except InvalidToken:
        return None
