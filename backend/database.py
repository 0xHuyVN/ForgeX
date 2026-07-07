import sqlite3
import threading
from contextlib import contextmanager
from .config import DB_PATH

_local = threading.local()


def get_conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        import os as _os
        _local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=5.0)
        _local.conn.row_factory = sqlite3.Row
        if not _os.environ.get("DISABLE_WAL"):
            _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
        # Tests set ``DISABLE_FK=1`` so they can insert orphans without
        # pre-creating parent project rows.
        if _os.environ.get("DISABLE_FK"):
            _local.conn.execute("PRAGMA foreign_keys=OFF")
        else:
            _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


@contextmanager
def db_cursor():
    conn = get_conn()
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def init_db():
    with db_cursor() as cur:
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            path TEXT,
            preset TEXT DEFAULT 'Movie Review',
            resolution TEXT DEFAULT '1920x1080',
            fps INTEGER DEFAULT 30,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS queue_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER REFERENCES projects(id),
            type TEXT NOT NULL,
            status TEXT DEFAULT 'waiting',
            input_path TEXT,
            output_path TEXT,
            params TEXT DEFAULT '{}',
            progress REAL DEFAULT 0,
            error TEXT,
            priority INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS subtitles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER REFERENCES projects(id),
            source TEXT,
            language TEXT DEFAULT 'vi',
            content TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            platform TEXT,
            status TEXT DEFAULT 'waiting',
            output_path TEXT,
            progress REAL DEFAULT 0,
            error TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            config TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT NOT NULL,
            tokens INTEGER DEFAULT 0,
            seconds REAL DEFAULT 0,
            cost REAL DEFAULT 0,
            date TEXT DEFAULT (date('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS scenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER REFERENCES projects(id),
            scene_index INTEGER,
            start_time REAL,
            end_time REAL,
            thumbnail TEXT
        );

        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            size INTEGER DEFAULT 0,
            duration REAL,
            tags TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        -- Track: a timeline track (video, audio, subtitle, etc.)
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            type TEXT NOT NULL DEFAULT 'video',
            name TEXT,
            track_index INTEGER DEFAULT 0,
            muted INTEGER DEFAULT 0,
            locked INTEGER DEFAULT 0,
            config TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        -- Clip: a clip on a track
        CREATE TABLE IF NOT EXISTS clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
            source_path TEXT,
            name TEXT,
            start_frame INTEGER DEFAULT 0,
            end_frame INTEGER DEFAULT 0,
            position_frame INTEGER DEFAULT 0,
            speed REAL DEFAULT 1.0,
            volume REAL DEFAULT 1.0,
            opacity REAL DEFAULT 1.0,
            effects TEXT DEFAULT '[]',
            config TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        -- Marker: timeline markers / cue points
        CREATE TABLE IF NOT EXISTS markers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            frame INTEGER NOT NULL,
            label TEXT,
            color TEXT DEFAULT '#f8b400',
            type TEXT DEFAULT 'note',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        -- Transition: transition between clips
        CREATE TABLE IF NOT EXISTS transitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            clip_a_id INTEGER REFERENCES clips(id) ON DELETE CASCADE,
            clip_b_id INTEGER REFERENCES clips(id) ON DELETE CASCADE,
            type TEXT DEFAULT 'crossfade',
            duration_frames INTEGER DEFAULT 15,
            config TEXT DEFAULT '{}'
        );

        -- Voices: voice profiles and clones
        CREATE TABLE IF NOT EXISTS voices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            provider TEXT DEFAULT 'edge',
            gender TEXT,
            language TEXT DEFAULT 'vi',
            sample_path TEXT,
            model_path TEXT,
            is_clone INTEGER DEFAULT 0,
            config TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        -- Job logs: detailed execution logs
        CREATE TABLE IF NOT EXISTS job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_item_id INTEGER REFERENCES queue_items(id) ON DELETE CASCADE,
            level TEXT DEFAULT 'info',
            message TEXT,
            timestamp TEXT DEFAULT (datetime('now','localtime'))
        );

        -- Job steps: track individual steps in a job for timeline visualization
        CREATE TABLE IF NOT EXISTS job_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_item_id INTEGER REFERENCES queue_items(id) ON DELETE CASCADE,
            step_name TEXT NOT NULL,
            step_index INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            progress REAL DEFAULT 0,
            started_at TEXT,
            completed_at TEXT,
            duration_ms INTEGER,
            error TEXT,
            metadata TEXT DEFAULT '{}',
            UNIQUE(queue_item_id, step_index)
        );

        -- Settings: app-wide key-value settings
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        -- Exports: export history
        CREATE TABLE IF NOT EXISTS exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            input_path TEXT,
            output_path TEXT,
            format TEXT DEFAULT 'mp4',
            resolution TEXT,
            codec TEXT DEFAULT 'h264',
            bitrate TEXT,
            file_size INTEGER DEFAULT 0,
            duration REAL,
            status TEXT DEFAULT 'completed',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        """)
        _ensure_column(cur, "downloads", "error", "TEXT")
        _ensure_column(cur, "tracks", "hidden", "INTEGER DEFAULT 0")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS telemetry_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            payload TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        """)
        
        # Create indexes for performance
        cur.execute("CREATE INDEX IF NOT EXISTS idx_job_steps_queue_item ON job_steps(queue_item_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_job_logs_queue_item ON job_logs(queue_item_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_queue_items_status ON queue_items(status, priority DESC, created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_events_type ON telemetry_events(event_type, created_at)")
        _cleanup_orphan_job_rows(cur)
        _protect_sensitive_settings(cur)


def _ensure_column(cur, table: str, column: str, definition: str):
    existing = [row["name"] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _cleanup_orphan_job_rows(cur):
    """Remove historical log/step rows that point to deleted queue items.

    Older queue cleanup deleted queue rows before foreign keys were consistently
    enforced, leaving orphaned job_logs/job_steps. A commercial build should not
    ship with PRAGMA foreign_key_check failures, so normalize existing DBs on
    startup.
    """
    cur.execute(
        """
        DELETE FROM job_logs
        WHERE queue_item_id IS NOT NULL
          AND queue_item_id NOT IN (SELECT id FROM queue_items)
        """
    )
    cur.execute(
        """
        DELETE FROM job_steps
        WHERE queue_item_id IS NOT NULL
          AND queue_item_id NOT IN (SELECT id FROM queue_items)
        """
    )


def _protect_sensitive_settings(cur):
    from .services.secure_settings import SENSITIVE_SETTING_KEYS, is_protected, protect_setting

    for key in SENSITIVE_SETTING_KEYS:
        row = cur.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not row or not row["value"] or is_protected(row["value"]):
            continue
        cur.execute("UPDATE settings SET value=? WHERE key=?", (protect_setting(key, row["value"]), key))
