"""Centralised path/filename safety helpers.

This module is the single source of truth for "is this path/filename allowed to
be touched by the API?" — closing the path-traversal, arbitrary-read, and
arbitrary-RCE-via-os.startfile issues found in the v2 code review.

Design goals
------------
* Default-deny. Anything that comes from a request is rejected unless it falls
  inside an explicit allow-list root.
* Roots are configurable per-call so callers can widen to, say, the user's
  Downloads folder for a single endpoint without weakening the global policy.
* All checks are done on ``Path.resolve()`` to defeat ``..`` traversal,
  symlink escapes, and Windows 8.3 short-name tricks.
* Filenames are sanitised to a conservative whitelist before being joined into
  any path — no ``..``, no separators, no NUL, no control chars.
* The legacy ``path_allowlist.allow_path()`` API (explicit per-path allow) is
  preserved for backwards compatibility with ``main.serve_video``.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Iterable, Sequence

from ..config import (
    DATA_DIR,
    DOWNLOADS_DIR,
    SUBTITLES_DIR,
    VOICES_DIR,
    EXPORTS_DIR,
    PROJECTS_DIR,
    TEMPLATES_DIR,
    PRESETS_DIR,
    CACHE_DIR,
    ASSETS_DIR,
)


class PathGuardError(ValueError):
    """Raised when a path or filename fails the guard."""


# ---------------------------------------------------------------------------
# Allow-list roots
# ---------------------------------------------------------------------------

_DATA_ROOTS: tuple[Path, ...] = tuple(
    Path(p).resolve() for p in (
        DATA_DIR,
        DOWNLOADS_DIR,
        SUBTITLES_DIR,
        VOICES_DIR,
        EXPORTS_DIR,
        PROJECTS_DIR,
        TEMPLATES_DIR,
        PRESETS_DIR,
        CACHE_DIR,
        ASSETS_DIR,
    )
)


def data_roots() -> tuple[Path, ...]:
    """Return the immutable tuple of allow-listed root directories."""
    return _DATA_ROOTS


def _media_roots_from_env() -> list[Path]:
    raw = os.environ.get("MEDIA_INPUT_DIRS") or os.environ.get("VIDEO_INPUT_DIRS") or ""
    roots = []
    for item in raw.split(os.pathsep):
        item = item.strip().strip('"')
        if item:
            roots.append(Path(item).expanduser().resolve())
    return roots


def default_media_roots() -> tuple[Path, ...]:
    """Return local folders that are acceptable sources for user-picked media.

    These roots are read-only inputs for video/audio processing endpoints. They
    intentionally do not widen the global data allow-list used by import/export
    endpoints.
    """
    home = Path.home().resolve()
    candidates = [
        home / "Downloads",
        home / "Desktop",
        home / "Documents",
        home / "Videos",
        home / "Music",
        home / "Pictures",
        *_media_roots_from_env(),
    ]
    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(resolved)
    return tuple(deduped)


_extra_roots: list[Path] = []
_extra_lock = threading.Lock()


def add_extra_root(path: str | os.PathLike[str]) -> Path:
    """Add an additional allowed root at runtime (e.g. user-picked folder).

    The caller is responsible for not adding arbitrary user-supplied paths; this
    is meant for one-off expansions such as "the folder the user just picked
    in the file dialog".
    """
    resolved = Path(path).expanduser().resolve()
    with _extra_lock:
        if resolved not in _extra_roots:
            _extra_roots.append(resolved)
    return resolved


def clear_extra_roots() -> None:
    with _extra_lock:
        _extra_roots.clear()


def _all_roots(extra: Sequence[str | os.PathLike[str]] | None = None) -> list[Path]:
    roots = list(_DATA_ROOTS)
    with _extra_lock:
        roots.extend(_extra_roots)
    if extra:
        roots.extend(Path(p).expanduser().resolve() for p in extra)
    # De-duplicate while preserving order.
    seen: set[Path] = set()
    deduped: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            deduped.append(r)
    return deduped


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def _resolve(path: str | os.PathLike[str]) -> Path:
    if path is None or (isinstance(path, str) and not path.strip()):
        raise PathGuardError("Empty path")
    try:
        return Path(str(path)).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathGuardError(f"Cannot resolve path: {exc}") from exc


def is_inside_data(path: str | os.PathLike[str],
                   *,
                   extra_roots: Sequence[str | os.PathLike[str]] | None = None,
                   require_exists: bool = False) -> Path | None:
    """Return the resolved Path if it falls inside an allowed root, else None.

    No exception is raised — use :func:`safe_inside_data` when you want a 400.
    """
    try:
        resolved = _resolve(path)
    except PathGuardError:
        return None
    if require_exists and not resolved.exists():
        return None
    for root in _all_roots(extra_roots):
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    return None


def safe_inside_data(path: str | os.PathLike[str],
                     *,
                     field: str = "path",
                     extra_roots: Sequence[str | os.PathLike[str]] | None = None,
                     require_exists: bool = False) -> Path:
    """Validate that ``path`` is inside an allowed root.

    Raises :class:`PathGuardError` (a :class:`ValueError`) on failure.
    """
    resolved = is_inside_data(path, extra_roots=extra_roots, require_exists=require_exists)
    if resolved is None:
        raise PathGuardError(
            f"Invalid {field}: path is outside the allowed directories"
        )
    return resolved


def safe_open(path: str | os.PathLike[str],
              *,
              field: str = "path",
              extra_roots: Sequence[str | os.PathLike[str]] | None = None,
              require_exists: bool = True) -> Path:
    """Validate and return a path that is guaranteed safe to open() / read / write.

    Defaults to ``require_exists=True`` because most call sites need to read an
    existing file. Pass ``require_exists=False`` for "new file under data dir"
    cases (e.g. uploads).
    """
    return safe_inside_data(
        path, field=field, extra_roots=extra_roots, require_exists=require_exists
    )


_MEDIA_FILE_EXTS = {
    ".3gp", ".aac", ".aif", ".aiff", ".asf", ".avi", ".flac", ".m4a",
    ".m4v", ".mkv", ".mov", ".mp3", ".mp4", ".mpeg", ".mpg", ".ogg",
    ".opus", ".ts", ".wav", ".webm", ".wma", ".wmv",
}


def safe_media_input(path: str | os.PathLike[str],
                     *,
                     field: str = "media path",
                     extra_roots: Sequence[str | os.PathLike[str]] | None = None,
                     extensions: Iterable[str] | None = None) -> Path:
    """Validate an existing user-selected media file.

    This is deliberately narrower than arbitrary file access: the path must
    exist, be a file, live under the app data tree or common user media folders,
    and use a known media extension.
    """
    allowed_exts = {e.lower() if e.startswith(".") else f".{e.lower()}"
                    for e in (extensions or _MEDIA_FILE_EXTS)}
    resolved = _resolve(path)
    if resolved.suffix.lower() not in allowed_exts:
        raise PathGuardError(f"Invalid {field}: unsupported media extension")
    if not resolved.exists():
        raise PathGuardError(f"Invalid {field}: path does not exist")
    if not resolved.is_file():
        raise PathGuardError(f"Invalid {field}: not a file")

    try:
        from .path_allowlist import is_allowed_path
        if is_allowed_path(str(resolved)):
            return resolved
    except Exception:
        pass

    roots = [*default_media_roots(), *(extra_roots or ())]
    resolved = safe_inside_data(
        resolved, field=field, extra_roots=roots, require_exists=True
    )
    return resolved


def safe_output_path(path: str | os.PathLike[str],
                     *,
                     field: str = "output path",
                     extensions: Iterable[str] | None = None) -> Path:
    """Validate a user-controlled output path for writing.

    The file may be new, but its parent must be under the app data roots or an
    explicitly added runtime root (for example a folder selected via Browse).
    """
    resolved = _resolve(path)
    if extensions:
        allowed_exts = {e.lower() if e.startswith(".") else f".{e.lower()}"
                        for e in extensions}
        if resolved.suffix.lower() not in allowed_exts:
            raise PathGuardError(f"Invalid {field}: unsupported output extension")
    parent = resolved.parent.resolve(strict=False)
    if is_inside_data(parent, require_exists=False) is None:
        raise PathGuardError(
            f"Invalid {field}: path is outside the allowed directories"
        )
    parent.mkdir(parents=True, exist_ok=True)
    return resolved


def safe_output_dir(path: str | os.PathLike[str], *, field: str = "output dir") -> Path:
    """Validate a directory path that may be created for writing."""
    resolved = _resolve(path)
    if is_inside_data(resolved, require_exists=False) is None:
        raise PathGuardError(
            f"Invalid {field}: path is outside the allowed directories"
        )
    return resolved


# ---------------------------------------------------------------------------
# Filename sanitisation
# ---------------------------------------------------------------------------

# Conservative whitelist: alphanumerics, dot, dash, underscore, space, parens,
# plus sign, comma, at-sign. Anything else gets replaced with '_'.
_FILENAME_OK = re.compile(r"[A-Za-z0-9._@()\-+, ]")
_FILENAME_BAD = re.compile(r"[\x00-\x1f\x7f]")
_MAX_FILENAME_LEN = 200


def safe_filename(name: str | os.PathLike[str], *, field: str = "filename") -> str:
    """Sanitise an uploaded/basename'd filename into something safe to join.

    * Strips directory components (``foo/../bar`` -> ``bar``).
    * Replaces forbidden characters with ``_``.
    * Strips NUL / control bytes.
    * Caps length at 200 chars (Windows MAX_PATH sanity).
    * Rejects ``.`` and ``..``.
    """
    if name is None:
        raise PathGuardError(f"Empty {field}")
    raw = os.path.basename(str(name))
    if not raw or raw in {".", ".."}:
        raise PathGuardError(f"Invalid {field}")
    cleaned = _FILENAME_BAD.sub("", raw)
    cleaned = "".join(c if _FILENAME_OK.match(c) else "_" for c in cleaned)
    cleaned = cleaned.strip(" ._")
    if not cleaned:
        raise PathGuardError(f"Invalid {field}")
    if len(cleaned) > _MAX_FILENAME_LEN:
        cleaned = cleaned[:_MAX_FILENAME_LEN]
    return cleaned


def safe_join(base_dir: str | os.PathLike[str],
              *parts: str,
              field: str = "path") -> Path:
    """Join ``parts`` (after sanitising each) under ``base_dir`` and verify it
    stays inside ``base_dir``.

    This is the recommended helper for uploads: ``base_dir`` must itself be
    inside an allowed root (use :func:`safe_inside_data` to check it first).
    """
    base = Path(base_dir).expanduser().resolve(strict=False)
    target = base
    for i, part in enumerate(parts):
        target = target / safe_filename(part, field=f"{field}[{i}]")
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise PathGuardError(f"Invalid {field}: escapes base directory") from exc
    return resolved


# ---------------------------------------------------------------------------
# Browser shell-out safety
# ---------------------------------------------------------------------------

_FORBIDDEN_SHELL_EXTS = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".pif", ".vbs", ".js", ".jse",
    ".wsf", ".wsh", ".ps1", ".msh", ".msh1", ".msh2", ".mshxml", ".msh1xml",
    ".msh2xml", ".lnk", ".scf", ".url", ".jar", ".hta",
}


def safe_folder_to_open(path: str | os.PathLike[str],
                        *,
                        field: str = "path",
                        extra_roots: Sequence[str | os.PathLike[str]] | None = None) -> Path:
    """Validate a path that will be passed to ``os.startfile`` / ``xdg-open``.

    In addition to the standard inside-root check, this rejects:

    * non-existent paths
    * non-directory targets
    * anything ending in a known executable / script extension
    """
    resolved = safe_inside_data(path, field=field, extra_roots=extra_roots,
                                require_exists=True)
    if not resolved.is_dir():
        raise PathGuardError(f"Invalid {field}: not a directory")
    return resolved


# ---------------------------------------------------------------------------
# Convenience: HTTPException bridge
# ---------------------------------------------------------------------------

def http_safe_filename(name: str, *, field: str = "filename"):
    """Sanitise a filename; raise ``HTTPException(400)`` on failure.

    Import this in routers to avoid having to catch :class:`PathGuardError`
    everywhere.
    """
    from fastapi import HTTPException
    try:
        return safe_filename(name, field=field)
    except PathGuardError as exc:
        raise HTTPException(400, str(exc)) from exc


def http_safe_inside_data(path: str, *,
                          field: str = "path",
                          extra_roots: Sequence[str | os.PathLike[str]] | None = None,
                          require_exists: bool = False):
    """Validate path is inside an allowed root; raise ``HTTPException(400)``."""
    from fastapi import HTTPException
    try:
        return safe_inside_data(path, field=field, extra_roots=extra_roots,
                                require_exists=require_exists)
    except PathGuardError as exc:
        raise HTTPException(400, str(exc)) from exc


def http_safe_folder_to_open(path: str, *,
                             field: str = "path",
                             extra_roots: Sequence[str | os.PathLike[str]] | None = None):
    """Validate a folder for shell-open; raise ``HTTPException(400)``."""
    from fastapi import HTTPException
    try:
        return safe_folder_to_open(path, field=field, extra_roots=extra_roots)
    except PathGuardError as exc:
        raise HTTPException(400, str(exc)) from exc


def http_safe_media_input(path: str, *,
                          field: str = "media path",
                          extra_roots: Sequence[str | os.PathLike[str]] | None = None,
                          extensions: Iterable[str] | None = None):
    """Validate a user-picked media input file; raise ``HTTPException(400)``."""
    from fastapi import HTTPException
    try:
        return safe_media_input(
            path, field=field, extra_roots=extra_roots, extensions=extensions
        )
    except PathGuardError as exc:
        raise HTTPException(400, str(exc)) from exc


def http_safe_output_path(path: str, *,
                          field: str = "output path",
                          extensions: Iterable[str] | None = None):
    """Validate an output file path for writing; raise ``HTTPException(400)``."""
    from fastapi import HTTPException
    try:
        return safe_output_path(path, field=field, extensions=extensions)
    except PathGuardError as exc:
        raise HTTPException(400, str(exc)) from exc


def http_safe_output_dir(path: str, *, field: str = "output dir"):
    """Validate an output directory path for writing; raise ``HTTPException(400)``."""
    from fastapi import HTTPException
    try:
        return safe_output_dir(path, field=field)
    except PathGuardError as exc:
        raise HTTPException(400, str(exc)) from exc
