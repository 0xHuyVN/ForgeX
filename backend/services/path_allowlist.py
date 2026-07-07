from pathlib import Path
import threading

_allowed_paths = set()
_lock = threading.Lock()


def allow_path(path: str) -> str:
    resolved = str(Path(path).expanduser().resolve())
    with _lock:
        _allowed_paths.add(resolved)
    return resolved


def is_allowed_path(path: str) -> bool:
    try:
        resolved = str(Path(path).expanduser().resolve())
    except Exception:
        return False
    with _lock:
        return resolved in _allowed_paths
