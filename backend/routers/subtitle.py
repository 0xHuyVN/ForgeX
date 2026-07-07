from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from ..models.schemas import SubtitleRequest, TranslateRequest
from ..services.translator import translate_text, get_job, semantic_segment_srt
from ..services.queue_manager import add_queue_item
from ..services.path_allowlist import is_allowed_path
from ..services.path_guard import (
    http_safe_filename,
    http_safe_inside_data,
    http_safe_media_input,
)
from ..database import db_cursor
from ..config import SUBTITLES_DIR, DATA_DIR
import json

router = APIRouter()


@router.post("/transcribe")
def transcribe_subtitle(data: SubtitleRequest):
    try:
        item_id = add_queue_item(
            data.project_id,
            "transcribe",
            data.source_path,
            {"language": data.language, "whisperx": False, "vocal_separation": False},
            priority=1,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": item_id, "message": "Da dua tien trinh chuyen am vao hang doi", "project_id": data.project_id}


@router.post("/import")
async def import_subtitle(project_id: int = 0, file: UploadFile = File(...)):
    """Import a subtitle file via multipart upload.

    IMPORTANT: ``text`` here is the *decoded contents of the upload*, not a
    filesystem path. Earlier versions of this endpoint treated user-controlled
    upload content as a path and would silently swap in the contents of any
    matching file on disk — a content-confusion / arbitrary-read bug. The
    endpoint now always stores the uploaded bytes verbatim.
    """
    content = await file.read()
    text = content.decode("utf-8", errors="replace")
    safe_name = http_safe_filename(file.filename or f"upload_{project_id}.srt",
                                   field="filename")
    sub_dir = http_safe_inside_data(SUBTITLES_DIR, field="subtitles dir")
    sub_path = (sub_dir / f"sub_{project_id}_{safe_name}").resolve()
    try:
        sub_path.relative_to(sub_dir)
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    sub_path.write_text(text, encoding="utf-8")
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO subtitles (project_id, source, content) VALUES (?,?,?)",
            (project_id, safe_name, text),
        )
        sid = cur.lastrowid
    try:
        from ..services.timeline_service import sync_timeline_subtitle
        from ..services.event_bus import event_bus
        sync_timeline_subtitle(project_id, text)
        event_bus.publish("subtitle_updated", {"project_id": project_id, "source": safe_name, "path": str(sub_path)})
    except Exception:
        pass
    return {"id": sid, "path": str(sub_path)}


@router.post("/import-path")
async def import_subtitle_path(data: dict):
    """Import subtitle by file path; more reliable than UploadFile for local paths."""
    import os
    from ..services.timeline_service import sync_timeline_subtitle
    raw_path = (data.get("path") or "").strip()
    project_id = data.get("project_id", 0)

    # Try user-provided path first, then fallback to project_id-based path.
    # Both branches are validated against the path-guard so we never read files
    # outside the allow-listed roots.
    file_path = ""
    if raw_path:
        try:
            file_path = str(_resolve_path(raw_path))
        except HTTPException:
            file_path = ""
    if (not file_path or not os.path.exists(file_path)) and project_id:
        candidate = str((SUBTITLES_DIR / f"project_{project_id}_stt.srt").resolve())
        if os.path.exists(candidate):
            file_path = candidate

    if not file_path or not os.path.exists(file_path):
        raise HTTPException(400, f"Khong tim thay tep: {raw_path or '(empty)'}")

    filename = os.path.basename(file_path)
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    sub_dir = http_safe_inside_data(SUBTITLES_DIR, field="subtitles dir")
    sub_path = (sub_dir / f"sub_{project_id}_{http_safe_filename(filename, field='filename')}").resolve()
    try:
        sub_path.relative_to(sub_dir)
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    sub_path.write_text(text, encoding="utf-8")
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO subtitles (project_id, source, content) VALUES (?,?,?)",
            (project_id, filename, text),
        )
        sid = cur.lastrowid
    try:
        sync_timeline_subtitle(project_id, text)
    except Exception as e:
        print(f"Error syncing timeline subtitle: {e}")
    try:
        from ..services.event_bus import event_bus
        event_bus.publish("subtitle_updated", {"project_id": project_id, "source": filename, "path": str(sub_path)})
    except Exception:
        pass
    return {"id": sid, "path": str(sub_path)}


def _resolve_path(path: str) -> str:
    """Resolve a potentially relative path against SUBTITLES_DIR.

    The candidate path MUST resolve inside an allow-listed root. This rejects
    arbitrary absolute paths outside the data tree.
    """
    p = path.strip()
    if not p:
        return ""

    downloads_dir = Path.home() / "Downloads"
    roots = [SUBTITLES_DIR.resolve(), DATA_DIR.resolve(), downloads_dir.resolve()]
    candidates = []
    raw = Path(p).expanduser()
    if raw.is_absolute():
        candidates.append(raw)
    candidates.append(SUBTITLES_DIR / Path(p.replace("\\", "/")).name)

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        if not resolved.exists() or not resolved.is_file():
            continue
        if resolved.suffix.lower() not in {".srt", ".ass", ".vtt", ".ssa"}:
            continue
        if is_allowed_path(str(resolved)):
            return str(resolved)
        for root in roots:
            try:
                resolved.relative_to(root)
                return str(resolved)
            except ValueError:
                pass
    return ""


@router.post("/read-file")
def read_subtitle_file(data: dict):
    """Read an SRT/ASS file from a local path and return its content."""
    import os
    path = (data.get("path") or "").strip()
    project_id = data.get("project_id", 0)
    if path:
        path = _resolve_path(path)
    # Fallback: build path from project_id
    if (not path or not os.path.exists(path)) and project_id:
        candidate = str(SUBTITLES_DIR / f"project_{project_id}_stt.srt")
        if os.path.exists(candidate):
            path = candidate
    if not path or not os.path.exists(path):
        raise HTTPException(404, f"Khong tim thay tep: {path or '(empty)'}")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"content": content, "filename": os.path.basename(path)}
    except Exception as e:
        raise HTTPException(500, f"Doc tep that bai: {e}")


@router.post("/translate")
def translate_subtitle(data: TranslateRequest):
    """Enqueue SRT translation as a tracked queue job; return integer job_id for progress polling."""
    import os
    text = data.text
    # If text is a local file path, read it
    resolved_text_path = _resolve_path(text.strip())
    if resolved_text_path and os.path.exists(resolved_text_path):
        try:
            with open(resolved_text_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception:
            pass
    # Decide SRT vs plain text
    if "-->" in text:
        item_id = add_queue_item(
            data.project_id or 0,
            "translate",
            "",
            {
                "srt_content": text,
                "source_lang": data.source_lang,
                "target_lang": data.target_lang,
                "translate_engine": data.engine,
                "translate_model": data.model,
                "semantic_segmentation": data.semantic_segmentation,
            },
            priority=1,
        )
        return {"job_id": item_id, "status": "running", "progress": 0}
    else:
        # For plain text use blocking translate (fast)
        result = translate_text(text, data.source_lang, data.target_lang, data.engine, model=data.model)
        return {"job_id": None, "translated": result, "status": "done", "progress": 100}


@router.post("/semantic-segment")
def semantic_segment_subtitle(data: TranslateRequest):
    """Improve subtitle line breaks/readability while preserving original SRT timecodes."""
    import os
    text = data.text
    resolved_text_path = _resolve_path(text.strip())
    if resolved_text_path and os.path.exists(resolved_text_path):
        with open(resolved_text_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    if "-->" not in text:
        return {"segmented": text, "status": "done", "progress": 100}
    result = semantic_segment_srt(text, target_lang=data.target_lang, engine=data.engine, model=data.model)
    if data.project_id:
        with db_cursor() as cur:
            cur.execute(
                "INSERT INTO subtitles (project_id, source, content) VALUES (?,?,?)",
                (data.project_id, f"semantic_{data.engine}.srt", result),
            )
        try:
            from ..services.timeline_service import sync_timeline_subtitle
            from ..services.event_bus import event_bus
            sync_timeline_subtitle(data.project_id, result)
            event_bus.publish("subtitle_updated", {"project_id": data.project_id, "source": f"semantic_{data.engine}.srt"})
        except Exception:
            pass
    return {"segmented": result, "status": "done", "progress": 100}


@router.get("/translate-progress/{job_id}")
def translate_progress(job_id: str):
    """Poll translation job status. Supports queue-backed (int id) and legacy thread-backed (uuid) jobs."""
    # Try queue-backed first (integer id from new /translate endpoint)
    try:
        item_id = int(job_id)
    except (TypeError, ValueError):
        item_id = None

    if item_id is not None:
        with db_cursor() as cur:
            row = cur.execute(
                "SELECT id, project_id, status, progress, error, output_path FROM queue_items WHERE id=?",
                (item_id,),
            ).fetchone()
        if row:
            status_map = {"waiting": "running", "running": "running", "paused": "running",
                          "completed": "done", "failed": "error", "cancelled": "error"}
            status = status_map.get(row["status"], row["status"])
            translated = None
            if status == "done" and row["output_path"]:
                try:
                    with open(row["output_path"], "r", encoding="utf-8", errors="replace") as f:
                        translated = f.read()
                except Exception:
                    translated = None
            return {
                "job_id": job_id,
                "status": status,
                "progress": int(row["progress"] or 0),
                "translated": translated,
                "error": row["error"],
            }

    # Fallback: legacy in-memory thread job (used by translate_srt blocking path's helpers)
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Khong tim thay tien trinh")
    return {
        "job_id": job_id,
        "status": job.get("status", "unknown"),
        "progress": job.get("progress", 0),
        "translated": job.get("result") if job.get("status") == "done" else None,
        "error": job.get("error"),
    }


@router.post("/detect-streams")
def detect_streams(data: dict):
    import subprocess
    import json
    from ..config import FFPROBE_PATH

    video_path = data.get("path", "")
    if not video_path:
        raise HTTPException(400, "Duong dan video khong hop le")
    safe_video = http_safe_media_input(video_path, field="video path")
    video_path = str(safe_video)

    cmd = [
        FFPROBE_PATH, "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-select_streams", "s",
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        info = json.loads(result.stdout)
        streams = info.get("streams", [])

        detected = []
        for i, s in enumerate(streams):
            tags = s.get("tags", {})
            lang = tags.get("language", "und")
            title = tags.get("title", f"Track {i+1}")
            index = s.get("index", i)
            detected.append({
                "index": index,
                "language": lang,
                "title": f"{title} ({lang})"
            })
        return {"streams": detected}
    except Exception as e:
        print(f"[Subtitle] Error detecting subtitle streams: {e}")
        return {"streams": []}


@router.post("/extract-stream")
def extract_stream(data: dict):
    video_path = data.get("path", "")
    stream_index = data.get("index", 0)
    project_id = data.get("project_id", 0)

    safe_video = http_safe_media_input(video_path, field="video path")
    video_path = str(safe_video)

    output_name = f"sub_{project_id}_extracted_{stream_index}.srt"
    sub_dir = http_safe_inside_data(SUBTITLES_DIR, field="subtitles dir")
    output_path = str((sub_dir / output_name).resolve())
    try:
        item_id = add_queue_item(
            project_id,
            "extract_subtitle_stream",
            video_path,
            {"stream_index": stream_index, "output_path": output_path},
            priority=1,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": item_id, "path": output_path, "message": "Da dua trich xuat phu de vao hang doi"}


@router.post("/transcribe-video")
def transcribe_video_endpoint(data: dict):
    video_path = data.get("path", "")
    project_id = data.get("project_id", 0)
    language = data.get("language", "vi")
    vocal_separation = data.get("vocal_separation")
    use_whisperx = data.get("whisperx", False)

    if not video_path:
        raise HTTPException(400, "Duong dan video khong hop le")
    safe_video = http_safe_media_input(video_path, field="video path")
    video_path = str(safe_video)

    try:
        item_id = add_queue_item(
            project_id,
            "transcribe",
            video_path,
            {"language": language, "vocal_separation": bool(vocal_separation), "whisperx": bool(use_whisperx)},
            priority=1,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": item_id, "message": "Da dua Whisper STT vao hang doi"}


@router.post("/export")
def export_subtitle(project_id: int, fmt: str = "srt", font: str = "Arial", size: int = 42, color: str = "#FFFFFF", shadow: str = "Soft"):
    from ..services.ffmpeg_utils import export_subtitle_file
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT content FROM subtitles WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Khong tim thay phu de nao")

    style = {"font": font, "size": size, "color": color, "shadow": shadow}
    try:
        out = export_subtitle_file(row["content"], fmt, project_id, style)
        return {"path": out}
    except Exception as e:
        raise HTTPException(500, f"Xuat phu de that bai: {e}")


@router.post("/ocr-video")
def ocr_video_endpoint(data: dict):
    video_path = data.get("path", "")
    project_id = data.get("project_id", 0)
    region = data.get("region")

    if not video_path:
        raise HTTPException(400, "Duong dan video khong hop le")
    safe_video = http_safe_media_input(video_path, field="video path")
    video_path = str(safe_video)

    from ..services.ocr_service import is_ocr_available
    if not is_ocr_available():
        raise HTTPException(400, "RapidOCR hoac OpenCV chua duoc cai dat.")

    try:
        item_id = add_queue_item(project_id, "ocr_hardsub", video_path, {"region": region}, priority=1)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": item_id,
        "message": "Da dua RapidOCR sub cung vao hang cho.",
    }


@router.get("/{project_id}")
def get_subtitles(project_id: int):
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM subtitles WHERE project_id=? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
