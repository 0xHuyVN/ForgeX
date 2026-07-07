from __future__ import annotations

import json
import time
from typing import Any

from ..database import db_cursor

TELEMETRY_OPT_IN_KEY = "telemetry.opt_in"


def is_enabled() -> bool:
    with db_cursor() as cur:
        row = cur.execute("SELECT value FROM settings WHERE key=?", (TELEMETRY_OPT_IN_KEY,)).fetchone()
    return str(row["value"]).lower() in {"1", "true", "yes", "on"} if row else False


def set_enabled(enabled: bool) -> dict:
    value = "true" if enabled else "false"
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now','localtime')",
            (TELEMETRY_OPT_IN_KEY, value),
        )
    return {"enabled": enabled}


def system_snapshot() -> dict[str, Any]:
    try:
        import psutil
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.05),
            "ram_percent": psutil.virtual_memory().percent,
            "ram_available_gb": round(psutil.virtual_memory().available / 1024**3, 2),
            "timestamp_ms": int(time.time() * 1000),
        }
    except Exception:
        return {"timestamp_ms": int(time.time() * 1000)}


def record_event(event_type: str, payload: dict[str, Any] | None = None, force: bool = False) -> dict:
    if not force and not is_enabled():
        return {"recorded": False, "enabled": False}
    data = dict(payload or {})
    data.setdefault("system", system_snapshot())
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO telemetry_events (event_type, payload) VALUES (?,?)",
            (event_type, json.dumps(data, ensure_ascii=False)),
        )
        event_id = cur.lastrowid
    return {"recorded": True, "enabled": True, "id": event_id}


def recent_events(limit: int = 100) -> dict:
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT id, event_type, payload, created_at FROM telemetry_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    items = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            payload = {}
        items.append({"id": row["id"], "event_type": row["event_type"], "payload": payload, "created_at": row["created_at"]})
    return {"enabled": is_enabled(), "items": items}
