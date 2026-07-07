import os
from ..database import db_cursor

try:
    import cv2
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    RapidOCR = None
    cv2 = None

def is_ocr_available() -> bool:
    return RapidOCR is not None and cv2 is not None

def log_to_db(message: str, level: str = "INFO", project_id: int = 0):
    from .job_logger import get_current_job_id, job_log
    job_id = get_current_job_id()
    if job_id:
        job_log(level, f"[RapidOCR] {message}")
    else:
        try:
            with db_cursor() as cur:
                cur.execute(
                    "INSERT INTO job_logs (queue_item_id, level, message) VALUES (NULL,?,?)",
                    (level.lower(), f"[RapidOCR] {message}")
                )
        except Exception as e:
            print(f"Error logging to db: {e}")

def extract_hard_subtitles(video_path: str, project_id: int, region: dict = None) -> dict:
    """Extract hardcoded subtitles from video using RapidOCR and save as SRT."""
    log_to_db(f"Bắt đầu trích xuất sub cứng cho video: {os.path.basename(video_path)}", project_id=project_id)
    
    if not is_ocr_available():
        msg = "RapidOCR hoặc OpenCV chưa được cài đặt. Vui lòng cài đặt: pip install rapidocr_onnxruntime opencv-python"
        log_to_db(msg, level="ERROR", project_id=project_id)
        return {"error": msg}
        
    try:
        engine = RapidOCR()
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return {"error": "Không thể mở file video"}
            
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps
        
        # Sample every 0.5 seconds
        interval_secs = 0.5
        frame_step = int(fps * interval_secs)
        
        segments = []
        current_text = ""
        
        # Default region: bottom 25% of the video if not provided
        # region coords are fractions (0-1)
        rx, ry, rw, rh = 0.0, 0.75, 1.0, 0.25
        if region:
            rx = region.get("x", rx)
            ry = region.get("y", ry)
            rw = region.get("width", rw)
            rh = region.get("height", rh)
            
        frame_idx = 0
        last_progress_pct = 0
        
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
                
            if frame_idx % frame_step == 0:
                h, w, _ = frame.shape
                # Calculate pixel coords
                x1 = int(rx * w)
                y1 = int(ry * h)
                x2 = int((rx + rw) * w)
                y2 = int((ry + rh) * h)
                
                # Crop to subtitle region
                cropped = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
                
                text = ""
                if cropped.size > 0:
                    # Run OCR
                    res, _ = engine(cropped)
                    if res:
                        lines = [line[1].strip() for line in res if line[2] > 0.4]
                        text = " ".join(lines).strip()
                
                t_sec = frame_idx / fps
                
                if text:
                    if text == current_text:
                        # Continue segment
                        if segments:
                            segments[-1]["end"] = t_sec + interval_secs
                    else:
                        # New segment
                        current_text = text
                        segments.append({
                            "start": t_sec,
                            "end": t_sec + interval_secs,
                            "text": text
                        })
                else:
                    current_text = ""
                    
                progress_pct = int((frame_idx / total_frames) * 100)
                if progress_pct - last_progress_pct >= 10:
                    log_to_db(f"Đang trích xuất: {progress_pct}%...", project_id=project_id)
                    last_progress_pct = progress_pct
            
            frame_idx += 1
            # Skip frames to speed up processing
            if frame_step > 1:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx + frame_step - 1)
                frame_idx += frame_step - 1
                
        cap.release()
        
        # Generate SRT
        srt_lines = []
        for idx, seg in enumerate(segments, 1):
            start_str = fmt_srt_time(seg["start"])
            end_str = fmt_srt_time(seg["end"])
            srt_lines.append(f"{idx}\n{start_str} --> {end_str}\n{seg['text']}\n")
            
        srt_content = "\n".join(srt_lines)
        
        from ..config import SUBTITLES_DIR
        sub_path = SUBTITLES_DIR / f"project_{project_id}_ocr.srt"
        sub_path.write_text(srt_content, encoding="utf-8")
        
        with db_cursor() as cur:
            cur.execute(
                "INSERT INTO subtitles (project_id, source, content) VALUES (?, 'rapidocr', ?)",
                (project_id, srt_content),
            )
        try:
            from .timeline_service import sync_timeline_subtitle
            sync_timeline_subtitle(project_id, srt_content)
        except Exception as e:
            log_to_db(f"Khong the dong bo OCR subtitle vao timeline: {e}", level="WARNING", project_id=project_id)

        try:
            from .event_bus import event_bus
            event_bus.publish("subtitle_updated", {
                "project_id": project_id,
                "source": "rapidocr",
                "path": str(sub_path),
                "segments": len(segments),
            })
        except Exception:
            pass

        log_to_db(f"Trích xuất sub cứng hoàn tất! Số câu tìm thấy: {len(segments)}", project_id=project_id)
        return {"srt_path": str(sub_path), "segments": len(segments)}
        
    except Exception as e:
        log_to_db(f"Lỗi khi chạy OCR: {str(e)}", level="ERROR", project_id=project_id)
        return {"error": str(e)}

def fmt_srt_time(secs):
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    ms = int((secs - int(secs)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
