"""Shared pytest fixtures.

We point DATA_DIR-style paths at a tmp_path-derived tree so the suite never
touches the real user data directory and the path-allow-list tests can assert
allow / deny behaviour without writing outside the sandbox.

For the database we use a per-test SQLite file with WAL disabled and keep the
``database`` module's per-thread connection open for the duration of the test.
The TestClient's worker thread re-opens the same file (fresh sqlite3 handle)
and sees the same schema because the schema is persisted to disk before the
test body runs.
"""
import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND.parent) not in sys.path:
    sys.path.insert(0, str(BACKEND.parent))

# Disable WAL — the TestClient's worker-thread sees stale schema otherwise.
os.environ.setdefault("DISABLE_WAL", "1")
# Disable FKs — many tests insert with project_id=0 without first creating a
# project row, which would otherwise trip the FK constraint.
os.environ.setdefault("DISABLE_FK", "1")


@pytest.fixture
def sandbox_data(tmp_path, monkeypatch):
    """Create an isolated data dir tree and rebind the config + path_guard."""
    from backend import config
    from backend.services import path_guard
    from backend import database as db_module

    data_dir = tmp_path / "data"
    db_dir = data_dir / "db"
    for sub in ("downloads", "subtitles", "voices", "exports",
                "projects", "templates", "presets", "cache",
                "db", "playlists"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)

    # Use a per-test file-based SQLite DB. WAL is disabled (see top of file).
    sandbox_db = db_dir / f"test_{uuid.uuid4().hex}.sqlite"

    config.DATA_DIR = data_dir
    config.DOWNLOADS_DIR = data_dir / "downloads"
    config.SUBTITLES_DIR = data_dir / "subtitles"
    config.VOICES_DIR = data_dir / "voices"
    config.EXPORTS_DIR = data_dir / "exports"
    config.PROJECTS_DIR = data_dir / "projects"
    config.TEMPLATES_DIR = data_dir / "templates"
    config.PRESETS_DIR = data_dir / "presets"
    config.CACHE_DIR = data_dir / "cache"
    config.DB_DIR = db_dir
    config.ASSETS_DIR = data_dir / "downloads"
    config.DB_PATH = sandbox_db

    # Drop any cached sqlite connection from a previous test run.
    try:
        if hasattr(db_module._local, "conn") and db_module._local.conn is not None:
            db_module._local.conn.close()
    except Exception:
        pass
    db_module._local.conn = None
    # Point the database module at the sandbox file BEFORE init_db runs.
    db_module.DB_PATH = sandbox_db

    # Initialise the schema in the sandbox DB.
    db_module.init_db()
    # Keep the connection open and cached so subsequent test code sees it;
    # the TestClient worker thread opens a separate sqlite3 handle against
    # the same file and reads the schema straight off disk.
    db_module.get_conn()

    path_guard._DATA_ROOTS = tuple(
        path_guard.Path(p).resolve() for p in (
            config.DATA_DIR, config.DOWNLOADS_DIR, config.SUBTITLES_DIR,
            config.VOICES_DIR, config.EXPORTS_DIR, config.PROJECTS_DIR,
            config.TEMPLATES_DIR, config.PRESETS_DIR, config.CACHE_DIR,
            config.ASSETS_DIR,
        )
    )
    path_guard.clear_extra_roots()

    # Rebind on every backend module that captured these names at import time.
    rebind = {
        "SUBTITLES_DIR": config.SUBTITLES_DIR,
        "DOWNLOADS_DIR": config.DOWNLOADS_DIR,
        "VOICES_DIR": config.VOICES_DIR,
        "EXPORTS_DIR": config.EXPORTS_DIR,
        "PROJECTS_DIR": config.PROJECTS_DIR,
        "TEMPLATES_DIR": config.TEMPLATES_DIR,
        "PRESETS_DIR": config.PRESETS_DIR,
        "CACHE_DIR": config.CACHE_DIR,
        "DATA_DIR": config.DATA_DIR,
        "ASSETS_DIR": config.ASSETS_DIR,
        "DB_DIR": config.DB_DIR,
        "DB_PATH": sandbox_db,
    }
    for name, mod in list(sys.modules.items()):
        if not name.startswith("backend."):
            continue
        if mod is None:
            continue
        for attr, value in rebind.items():
            if hasattr(mod, attr):
                setattr(mod, attr, value)
    return data_dir