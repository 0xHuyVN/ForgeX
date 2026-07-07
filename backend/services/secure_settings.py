from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ..config import DB_DIR


SENSITIVE_SETTING_KEYS = {
    "chatgpt_cookies",
    "gemini_cookies",
    "ai_api_key",
    "openai_key",
    "elevenlabs_key",
    "youtube_cookie",
}

_PREFIX = "enc:v1:"


def _key_path() -> Path:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    return DB_DIR / ".settings.key"


def _fernet() -> Fernet:
    path = _key_path()
    if not path.exists():
        path.write_bytes(Fernet.generate_key())
    return Fernet(path.read_bytes())


def protect_setting(key: str, value: str) -> str:
    if key not in SENSITIVE_SETTING_KEYS or not value:
        return value
    if value.startswith(_PREFIX):
        return value
    token = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def reveal_setting(key: str, value: str | None) -> str:
    if not value:
        return ""
    if key not in SENSITIVE_SETTING_KEYS or not value.startswith(_PREFIX):
        return value
    token = value[len(_PREFIX):].encode("ascii")
    try:
        return _fernet().decrypt(token).decode("utf-8")
    except InvalidToken:
        return ""


def is_protected(value: str | None) -> bool:
    return bool(value and value.startswith(_PREFIX))
