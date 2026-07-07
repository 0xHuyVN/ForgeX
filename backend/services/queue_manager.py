from ..database import db_cursor
from datetime import datetime
import threading


_SENSITIVE_KEYS = {"fpt_api_key", "api_key", "access_token", "password", "secret"}
_QUEUE_PAUSED_KEY = "queue_paused"
_sensitive_params = {}
_sensitive_lock = threading.Lock()


def _split_sensitive_params(params: dict) -> tuple[dict, dict]:
    public = dict(params or {})
    sensitive = {}
    for key in list(public.keys()):
        if key.lower() in _SENSITIVE_KEYS and public.get(key):
            sensitive[key] = public.pop(key)
            public[key] = "__redacted__"
    return public, sensitive


def pop_sensitive_params(item_id: int) -> dict:
    with _sensitive_lock:
        return _sensitive_params.pop(int(item_id), {})


def is_queue_paused() -> bool:
    with db_cursor() as cur:
        row = cur.execute("SELECT value FROM settings WHERE key=?", (_QUEUE_PAUSED_KEY,)).fetchone()
    return bool(row and str(row["value"]).strip().lower() in {"1", "true", "yes", "on"})


def set_queue_paused(paused: bool):
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (_QUEUE_PAUSED_KEY, "1" if paused else "0", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )


def ensure_project_id(project_id: int = None) -> int:
    """Return an existing project id, creating a local project only for None."""
    if project_id is None:
        from .project_service import create_project
        project = create_project(f"project_{datetime.now().strftime('%Y%m%d_%H%M%S')}", preset="Movie Review")
        return int(project["id"])

    try:
        pid = int(project_id)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid project_id: {project_id}")

    if pid == 0:
        return 0

    with db_cursor() as cur:
        row = cur.execute("SELECT id FROM projects WHERE id=?", (pid,)).fetchone()
        if row:
            return pid
    raise ValueError(f"Project {pid} not found")


def add_queue_item(project_id: int, item_type: str, input_path: str, params: dict = None, priority: int = 0) -> int:
    import json
    project_id = ensure_project_id(project_id)
    db_project_id = None if project_id == 0 else project_id
    public_params, sensitive = _split_sensitive_params(params or {})
    initial_status = "paused" if is_queue_paused() else "waiting"
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO queue_items (project_id, type, status, input_path, params, priority) VALUES (?,?,?,?,?,?)",
            (db_project_id, item_type, initial_status, input_path, json.dumps(public_params), priority),
        )
        item_id = cur.lastrowid
    if sensitive:
        with _sensitive_lock:
            _sensitive_params[int(item_id)] = sensitive
    from .event_bus import event_bus
    event_bus.publish("queue_changed")
    return item_id


def update_item_status(item_id: int, status: str, progress: float = None, error: str = None):
    with db_cursor() as cur:
        fields = ["status=?"]
        vals = [status]
        if progress is not None:
            fields.append("progress=?")
            vals.append(progress)
        if error is not None:
            fields.append("error=?")
            vals.append(error)
        vals.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        vals.append(item_id)
        cur.execute(
            f"UPDATE queue_items SET {', '.join(fields)}, updated_at=? WHERE id=?",
            vals,
        )
    from .event_bus import event_bus
    event_bus.publish("queue_changed")


def retry_failed(item_id: int = None):
    with db_cursor() as cur:
        if item_id:
            cur.execute("UPDATE queue_items SET status='waiting', error=NULL WHERE id=? AND status='failed'", (item_id,))
        else:
            cur.execute("UPDATE queue_items SET status='waiting', error=NULL WHERE status='failed'")
    from .event_bus import event_bus
    event_bus.publish("queue_changed")


def pause_all() -> int:
    set_queue_paused(True)
    with db_cursor() as cur:
        cur.execute("UPDATE queue_items SET status='paused' WHERE status='waiting'")
        count = cur.rowcount
    from .event_bus import event_bus
    event_bus.publish("queue_changed")
    return count


def resume_all() -> int:
    set_queue_paused(False)
    with db_cursor() as cur:
        cur.execute("UPDATE queue_items SET status='waiting' WHERE status='paused'")
        count = cur.rowcount
    from .event_bus import event_bus
    event_bus.publish("queue_changed")
    return count


def get_queue(status: str = None) -> list:
    with db_cursor() as cur:
        if status:
            rows = cur.execute("SELECT * FROM queue_items WHERE status=? ORDER BY priority DESC, created_at", (status,)).fetchall()
        else:
            rows = cur.execute("SELECT * FROM queue_items ORDER BY priority DESC, created_at").fetchall()
        return [dict(r) for r in rows]


def clear_all():
    with db_cursor() as cur:
        cur.execute("DELETE FROM job_logs WHERE queue_item_id IN (SELECT id FROM queue_items WHERE status!='running') OR queue_item_id IS NULL")
        cur.execute("DELETE FROM queue_items WHERE status!='running'")
    from .event_bus import event_bus
    event_bus.publish("queue_changed")


def reset_stale_running(reason: str = "App restarted before job finished") -> int:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE queue_items SET status='failed', error=?, updated_at=? WHERE status='running'",
            (reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        count = cur.rowcount
    if count:
        from .event_bus import event_bus
        event_bus.publish("queue_changed")
    return count
