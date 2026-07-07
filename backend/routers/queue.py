import asyncio
import json
import os
import queue as qmod
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from ..database import db_cursor
from ..config import CACHE_DIR, SUBTITLES_DIR
from ..services.path_guard import safe_inside_data
from ..services.event_bus import event_bus
from ..services.job_tracker import get_job_steps, get_job_timeline
from ..services.queue_manager import add_queue_item, clear_all, is_queue_paused, pause_all, resume_all, retry_failed

router = APIRouter()


@router.get("/events")
async def queue_events(request: Request):
    q = event_bus.register()
    try:
        async def generate():
            try:
                yield "data: {\"type\":\"connected\"}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        payload = await asyncio.to_thread(q.get, timeout=5)
                        try:
                            event = json.loads(payload)
                        except Exception:
                            event = {"type": "queue_changed", "data": None}

                        if event.get("type") == "queue_changed":
                            with db_cursor() as cur:
                                rows = cur.execute(
                                    "SELECT * FROM queue_items ORDER BY priority DESC, created_at"
                                ).fetchall()
                            event["data"] = [_enrich_queue_item(dict(r)) for r in rows]

                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except qmod.Empty:
                        yield ": heartbeat\n\n"
            finally:
                event_bus.unregister(q)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception:
        event_bus.unregister(q)
        raise


@router.post("/clear-all")
def clear():
    clear_all()
    return {"message": "Da xoa sach hang doi"}


@router.post("/")
def create_queue_item(data: dict):
    project_id = data.get("project_id")
    ptype = data.get("type", "render")
    input_path = data.get("input_path", "")
    params = data.get("params", {})
    if not project_id:
        raise HTTPException(400, "Yeu cau cung cap project_id")
    item_id = add_queue_item(project_id, ptype, input_path, params, data.get("priority", 0))
    return {"id": item_id, "message": "Da dua phan tu vao hang doi"}


@router.get("/")
def list_queue(status: str = None):
    with db_cursor() as cur:
        if status:
            rows = cur.execute(
                "SELECT * FROM queue_items WHERE status=? ORDER BY priority DESC, created_at",
                (status,),
            ).fetchall()
        else:
            rows = cur.execute("SELECT * FROM queue_items ORDER BY priority DESC, created_at").fetchall()
        return [_enrich_queue_item(dict(r)) for r in rows]


@router.get("/stats")
def queue_stats():
    with db_cursor() as cur:
        cur.execute("SELECT status, COUNT(*) as cnt FROM queue_items GROUP BY status")
        stats = {r["status"]: r["cnt"] for r in cur.fetchall()}
    return {
        "running": stats.get("running", 0),
        "waiting": stats.get("waiting", 0),
        "paused": stats.get("paused", 0),
        "completed": stats.get("completed", 0),
        "failed": stats.get("failed", 0),
        "queue_paused": is_queue_paused(),
    }


@router.get("/worker")
def worker_status():
    import psutil

    from ..config import QUEUE_JOB_TIMEOUT_MINUTES
    from ..workers.ffmpeg_worker import get_worker

    worker = get_worker()
    gpu_count = 0
    try:
        import subprocess

        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=count", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            gpu_count = len(result.stdout.strip().split("\n"))
    except Exception:
        pass

    return {
        "alive": worker.is_alive,
        "active": worker.active_count,
        "max_workers": worker.max_workers,
        "queue_paused": is_queue_paused(),
        "job_timeout_minutes": QUEUE_JOB_TIMEOUT_MINUTES,
        "gpu_count": gpu_count,
        "cpu_count": psutil.cpu_count(),
        "memory_available_gb": round(psutil.virtual_memory().available / 1024**3, 1),
    }


@router.put("/{item_id}")
def update_queue_item(item_id: int, data: dict):
    """Update queue item for the recovery system."""
    with db_cursor() as cur:
        fields = []
        values = []

        if "status" in data:
            fields.append("status = ?")
            values.append(data["status"])
        if "error" in data:
            fields.append("error = ?")
            values.append(data["error"])
        if "progress" in data:
            fields.append("progress = ?")
            values.append(data["progress"])
        if not fields:
            raise HTTPException(400, "No fields to update")

        fields.append("updated_at = ?")
        values.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        values.append(item_id)
        cur.execute(f"UPDATE queue_items SET {', '.join(fields)} WHERE id = ?", values)

    event_bus.publish("queue_changed")
    return {"message": "Updated"}


@router.post("/{item_id}/retry")
def retry(item_id: int):
    # Also reset progress to 0 so the item appears fresh
    with db_cursor() as cur:
        cur.execute(
            "UPDATE queue_items SET progress=0 WHERE id=? AND status='failed'",
            (item_id,),
        )
    retry_failed(item_id)
    return {"message": "Dang thu lai"}


@router.post("/retry-all")
def retry_all():
    # Reset progress for all failed items so they appear fresh
    with db_cursor() as cur:
        cur.execute("UPDATE queue_items SET progress=0 WHERE status='failed'")
    retry_failed()
    return {"message": "Dang thu lai tat ca task loi"}


@router.post("/pause-all")
def pause():
    count = pause_all()
    return {"message": f"Da tam dung {count} phan tu dang cho. Task dang chay se chay not.", "paused": True, "count": count}


@router.post("/resume-all")
def resume():
    count = resume_all()
    return {"message": f"Da tiep tuc {count} phan tu", "paused": False, "count": count}


@router.get("/logs")
def get_logs(queue_item_id: int = None, limit: int = 100):
    with db_cursor() as cur:
        if queue_item_id:
            rows = cur.execute(
                "SELECT * FROM job_logs WHERE queue_item_id=? ORDER BY timestamp DESC LIMIT ?",
                (queue_item_id, limit),
            ).fetchall()
        else:
            rows = cur.execute("SELECT * FROM job_logs ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


@router.post("/log")
def add_log(data: dict):
    """Store frontend log messages in job_logs."""
    level = data.get("level", "info")
    message = data.get("message", "")
    item_id = data.get("queue_item_id")
    try:
        with db_cursor() as cur:
            if item_id:
                cur.execute(
                    "INSERT INTO job_logs (queue_item_id, level, message) VALUES (?,?,?)",
                    (item_id, level, message),
                )
            else:
                cur.execute(
                    "INSERT INTO job_logs (queue_item_id, level, message) VALUES (NULL,?,?)",
                    (level, message),
                )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/{item_id}/timeline")
def get_timeline(item_id: int):
    try:
        timeline = get_job_timeline(item_id)
        return {"timeline": timeline}
    except Exception as e:
        raise HTTPException(500, f"Failed to get timeline: {str(e)}")


@router.get("/{item_id}/steps")
def get_steps(item_id: int):
    try:
        steps = get_job_steps(item_id)
        return {"steps": steps}
    except Exception as e:
        raise HTTPException(500, f"Failed to get steps: {str(e)}")


@router.get("/{item_id}/output")
def get_output(item_id: int):
    with db_cursor() as cur:
        row = cur.execute("SELECT output_path FROM queue_items WHERE id=?", (item_id,)).fetchone()
    if not row or not row["output_path"]:
        raise HTTPException(404, "Output not found")
    path = safe_inside_data(
        row["output_path"],
        field="queue output",
        extra_roots=[CACHE_DIR],
        require_exists=True,
    )
    if path.suffix.lower() not in {".json", ".txt", ".srt", ".ass", ".vtt"}:
        raise HTTPException(400, "Output is not a readable text result")
    return PlainTextResponse(Path(path).read_text(encoding="utf-8", errors="replace"))


def _enrich_queue_item(item: dict) -> dict:
    """Add sub_source and sub_translated fields to a queue item."""
    project_id = item.get("project_id")
    params = item.get("params") or "{}"
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except Exception:
            params = {}

    sub_source = "-"
    sub_translated = "-"

    if project_id:
        try:
            with db_cursor() as cur:
                # Find original subtitle (STT or imported)
                orig = cur.execute(
                    "SELECT source, content FROM subtitles WHERE project_id=? AND source NOT LIKE 'translated_%' ORDER BY created_at ASC LIMIT 1",
                    (project_id,),
                ).fetchone()
                if orig:
                    sub_source = orig["content"] if orig["content"] else (orig["source"] or "original")

                # Find translated subtitle
                trans = cur.execute(
                    "SELECT source, content FROM subtitles WHERE project_id=? AND source LIKE 'translated_%' ORDER BY created_at DESC LIMIT 1",
                    (project_id,),
                ).fetchone()
                if trans:
                    sub_translated = trans["content"] if trans["content"] else (trans["source"] or "translated")
        except Exception as e:
            print(f"Error enriching subtitles: {e}")

    # Also check output_path for subtitle-related jobs
    output_path = item.get("output_path") or ""
    item_type = item.get("type") or ""
    if item_type == "translate" and output_path:
        if os.path.exists(output_path):
            try:
                with open(output_path, "r", encoding="utf-8", errors="replace") as f:
                    sub_translated = f.read()
            except Exception:
                sub_translated = os.path.basename(output_path)
        else:
            sub_translated = os.path.basename(output_path)
    elif item_type == "transcribe" and output_path:
        if os.path.exists(output_path):
            try:
                with open(output_path, "r", encoding="utf-8", errors="replace") as f:
                    sub_source = f.read()
            except Exception:
                sub_source = os.path.basename(output_path)
        else:
            sub_source = os.path.basename(output_path)

    # Use params/files for source info if available
    if project_id:
        if sub_source == "-" or not sub_source.strip() or "-->" not in sub_source:
            srt_file = SUBTITLES_DIR / f"project_{project_id}_stt.srt"
            if srt_file.exists():
                try:
                    sub_source = srt_file.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
        if sub_translated == "-" or not sub_translated.strip() or "-->" not in sub_translated:
            trans_file = SUBTITLES_DIR / f"project_{project_id}_translated.srt"
            if trans_file.exists():
                try:
                    sub_translated = trans_file.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
        if sub_source != "-" and "-->" in sub_source and (sub_translated == "-" or "-->" not in sub_translated):
            fallback = _find_recent_translation_with_same_cue_count(sub_source)
            if fallback:
                sub_translated = fallback

    item["sub_source"] = sub_source
    item["sub_translated"] = sub_translated
    return item


def _srt_cue_count(text: str) -> int:
    if not text or "-->" not in text:
        return 0
    return sum(1 for line in text.splitlines() if "-->" in line)


def _find_recent_translation_with_same_cue_count(source_text: str) -> str:
    source_count = _srt_cue_count(source_text)
    if not source_count:
        return ""
    try:
        with db_cursor() as cur:
            rows = cur.execute(
                "SELECT content FROM subtitles WHERE source LIKE 'translated_%' ORDER BY created_at DESC, id DESC LIMIT 20"
            ).fetchall()
        for row in rows:
            content = row["content"] or ""
            if _srt_cue_count(content) == source_count:
                return content
    except Exception:
        pass
    try:
        candidates = sorted(
            SUBTITLES_DIR.glob("project_*translated*.srt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in candidates[:20]:
            content = path.read_text(encoding="utf-8", errors="replace")
            if _srt_cue_count(content) == source_count:
                return content
    except Exception:
        pass
    return ""
