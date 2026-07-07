import os
from ..config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, SUBTITLES_DIR, VOCAL_SEPARATION_ENABLED
from ..database import db_cursor


def log_to_db(message: str, level: str = "INFO", project_id: int = 0):
    from .job_logger import get_current_job_id, job_log
    job_id = get_current_job_id()
    if job_id:
        job_log(level, f"[Whisper STT] {message}")
    else:
        try:
            with db_cursor() as cur:
                cur.execute(
                    "INSERT INTO job_logs (queue_item_id, level, message) VALUES (NULL,?,?)",
                    (level.lower(), f"[Whisper STT] {message}")
                )
        except Exception as e:
            print(f"Error logging to db: {e}")


def transcribe(audio_path: str, language: str = "vi", project_id: int = 0, use_whisperx: bool = False) -> dict:
    """Transcribe audio file to SRT via subprocess worker. Returns {srt_path, text, segments}."""
    log_to_db(f"Bắt đầu nhận dạng giọng nói cho tệp: {os.path.basename(audio_path)} (Ngôn ngữ: {language}){' với WhisperX' if use_whisperx else ''}", project_id=project_id)

    from .stt_worker import transcribe_subprocess
    whisperx_timeout = int(os.environ.get("WHISPERX_TIMEOUT_SECONDS", "180"))
    result = transcribe_subprocess(
        audio_path,
        language,
        WHISPER_MODEL,
        use_whisperx=use_whisperx,
        device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE,
        timeout_seconds=whisperx_timeout if use_whisperx else 600,
    )

    if result.get("error") and use_whisperx:
        log_to_db(
            f"WhisperX khong hoan thanh ({result['error']}), chuyen sang Whisper thuong.",
            level="WARNING",
            project_id=project_id,
        )
        result = transcribe_subprocess(
            audio_path,
            language,
            WHISPER_MODEL,
            use_whisperx=False,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
            timeout_seconds=600,
        )

    if result.get("error"):
        log_to_db(f"Lỗi nhận dạng giọng nói: {result['error']}", level="ERROR", project_id=project_id)
        return {"srt_path": "", "text": "", "segments": 0, "error": result["error"]}

    srt_content = result.get("srt_content", "")
    sub_path = SUBTITLES_DIR / f"project_{project_id}_stt.srt"
    sub_path.write_text(srt_content, encoding="utf-8")

    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO subtitles (project_id, source, content) VALUES (?,?,?)",
            (project_id, f"whisper_{language}{'_aligned' if result.get('whisperx_aligned') else ''}", srt_content),
        )
    try:
        from .timeline_service import sync_timeline_subtitle
        sync_timeline_subtitle(project_id, srt_content)
    except Exception as e:
        log_to_db(f"Khong the dong bo subtitle vao timeline: {e}", level="WARNING", project_id=project_id)
    try:
        from .event_bus import event_bus
        event_bus.publish("subtitle_updated", {
            "project_id": project_id,
            "source": f"whisper_{language}{'_aligned' if result.get('whisperx_aligned') else ''}",
            "path": str(sub_path),
            "segments": result.get("segments", 0),
        })
    except Exception:
        pass

    log_to_db(f"Chuyển âm giọng nói hoàn thành! Số phân đoạn: {result.get('segments', 0)}", project_id=project_id)
    return {
        "srt_path": str(sub_path),
        "text": result.get("text", ""),
        "segments": result.get("segments", 0),
        "whisperx_aligned": bool(result.get("whisperx_aligned")),
    }


def transcribe_video(video_path: str, language: str = "vi", project_id: int = 0, vocal_separation: bool = None, use_whisperx: bool = False) -> dict:
    """Extract audio from video, optionally separate vocals, then transcribe.

    Args:
        vocal_separation: Override config toggle. None = use VOCAL_SEPARATION_ENABLED.
    """
    from .ffmpeg_utils import extract_audio
    log_to_db(f"Đang trích xuất âm thanh từ video: {os.path.basename(video_path)}...", project_id=project_id)
    audio_path = extract_audio(video_path, sample_rate=16000)

    if vocal_separation is None:
        vocal_separation = VOCAL_SEPARATION_ENABLED
    if vocal_separation:
        try:
            from .vocal_separation import separate_vocals
            log_to_db("Đang tách giọng nói khỏi nhạc nền bằng Demucs...", project_id=project_id)
            vocal_path = separate_vocals(audio_path)
            if vocal_path != audio_path:
                log_to_db(f"Tách giọng nói hoàn tất: {os.path.basename(vocal_path)}", project_id=project_id)
            else:
                log_to_db("Audio là giọng nói sạch, bỏ qua bước tách.", project_id=project_id)
            return transcribe(vocal_path, language, project_id, use_whisperx=use_whisperx)
        except ImportError:
            log_to_db("Demucs chưa được cài đặt, bỏ qua bước tách giọng nói.", level="WARNING", project_id=project_id)
        except Exception as e:
            log_to_db(f"Tách giọng nói thất bại ({e}), chuyển sang nhận dạng trực tiếp.", level="WARNING", project_id=project_id)

    return transcribe(audio_path, language, project_id, use_whisperx=use_whisperx)


def transcribe_file(audio_path: str, use_whisperx: bool = False) -> str:
    """Transcribe and return raw text only."""
    from .stt_worker import transcribe_subprocess
    result = transcribe_subprocess(
        audio_path,
        use_whisperx=use_whisperx,
        device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE,
    )
    return result.get("text", "")
