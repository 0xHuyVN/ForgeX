import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import EXPORTS_DIR
from ..models.schemas import EnhanceRequest
from ..services.queue_manager import add_queue_item
from ..services.ffmpeg_utils import drawtext_filter
from ..services.path_guard import http_safe_media_input, http_safe_output_path

router = APIRouter()


def _escape_drawtext(text: str) -> str:
    return str(text or "").replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("\n", " ")


@router.post("/apply")
def apply_enhancements(data: EnhanceRequest):
    video_path = str(http_safe_media_input(data.video_path, field="video path"))
    filters = []
    if data.brightness is not None:
        filters.append(f"eq=brightness={data.brightness/50 - 1}")
    if data.contrast is not None:
        filters.append(f"eq=contrast={data.contrast/50}")
    if data.saturation is not None:
        filters.append(f"eq=saturation={data.saturation/50}")
    if data.vignette is not None:
        filters.append(f"vignette=PI*{data.vignette/100}")
    if data.temperature is not None:
        filters.append(f"colorbalance=rs={max(-1,(data.temperature-50)/100)}:bs={max(-1,(50-data.temperature)/100)}")
    film_look = (data.film_look or "").lower()
    if "kịch" in film_look or "kich" in film_look:
        filters.append("eq=contrast=1.18:saturation=0.92")
        filters.append("unsharp=5:5:0.5")
    elif "hành" in film_look or "hanh" in film_look:
        filters.append("eq=contrast=1.12:saturation=1.2")
    elif "review" in film_look:
        filters.append("eq=contrast=1.05:saturation=1.05")
    if getattr(data, "motion_blur", False):
        filters.append("minterpolate=mi_mode=mci:mc_mode=aobmc:vsbmc=1:fps=60")
    if getattr(data, "zoom", False):
        filters.append("scale=iw*1.03:ih*1.03,crop=iw/1.03:ih/1.03")
    if getattr(data, "shake", False):
        filters.append("crop=in_w-20:in_h-20:10+10*sin(t*10):10+10*cos(t*10)")
    if getattr(data, "transition", False):
        filters.append("fade=t=in:st=0:d=1")
    watermark_text = str(getattr(data, "watermark_text", "") or "").strip()
    if getattr(data, "watermark", False) and watermark_text:
        filters.append(drawtext_filter(watermark_text, fontcolor="white@0.5", fontsize=48, x="w-tw-20", y="h-th-20"))
    if getattr(data, "speed_ramp", False) or getattr(data, "fast_motion", False):
        filters.append("setpts=0.5*PTS")
    if getattr(data, "slow_motion", False):
        filters.append("setpts=2.0*PTS")
    if getattr(data, "particles", False):
        filters.append(drawtext_filter(".", fontcolor="white@0.3", fontsize=1, x="random(1)*w", y="random(1)*h"))

    out_path = str(http_safe_output_path(EXPORTS_DIR / f"project_{data.project_id}" / f"{Path(video_path).stem}_enhanced.mp4", field="enhance output", extensions={".mp4"}))
    item_id = None
    if filters:
        cmd = ["-i", video_path, "-vf", ",".join(filters), "-c:a", "copy", "-y", out_path]
        item_id = add_queue_item(data.project_id, "ffmpeg_command", video_path, {"cmd": cmd, "output_path": out_path})
    return {"id": item_id, "output": out_path, "filters": filters}


@router.post("/branding/logo")
def logo_overlay(data: dict):
    video_path = data.get("video_path", "")
    logo_path = data.get("logo_path", "")
    position = data.get("position", "top_right")
    opacity = data.get("opacity", 0.7)
    if not video_path or not logo_path:
        raise HTTPException(400, "Yeu cau cung cap video_path va logo_path")
    video_path = str(http_safe_media_input(video_path, field="video path"))
    logo_path = str(http_safe_media_input(logo_path, field="logo path", extensions={".png", ".jpg", ".jpeg", ".webp"}))
    pos_map = {"top_right": "(W-w-20):20", "bottom_right": "(W-w-20):(H-h-20)", "center": "(W-w)/2:(H-h)/2", "top_left": "20:20", "bottom_left": "20:(H-h-20)"}
    pos = pos_map.get(position, "(W-w-20):20")
    out = str(http_safe_output_path(EXPORTS_DIR / f"project_{int(data.get('project_id', 0) or 0)}" / f"{Path(video_path).stem}_logo.mp4", field="logo output", extensions={".mp4"}))
    cmd = ["-i", video_path, "-i", logo_path, "-filter_complex", f"[1:v]format=rgba,colorchannelmixer=aa={opacity}[logo];[0:v][logo]overlay={pos}", "-c:a", "copy", "-y", out]
    item_id = add_queue_item(data.get("project_id", 0), "ffmpeg_command", video_path, {"cmd": cmd, "output_path": out})
    return {"id": item_id, "output": out}


@router.post("/branding/text")
def text_overlay(data: dict):
    video_path = data.get("video_path", "")
    text = data.get("text", "0xForge")
    position = data.get("position", "bottom")
    font_size = data.get("font_size", 48)
    color = data.get("color", "white")
    if not video_path:
        raise HTTPException(400, "Yeu cau cung cap video_path")
    video_path = str(http_safe_media_input(video_path, field="video path"))
    pos_map = {"top": "x=(w-text_w)/2:y=20", "bottom": "x=(w-text_w)/2:y=h-th-20", "center": "x=(w-text_w)/2:y=(h-text_h)/2"}
    pos = pos_map.get(position, "x=(w-text_w)/2:y=h-th-20")
    out = str(http_safe_output_path(EXPORTS_DIR / f"project_{int(data.get('project_id', 0) or 0)}" / f"{Path(video_path).stem}_text.mp4", field="text output", extensions={".mp4"}))
    x_expr, y_expr = pos.split(":", 1)
    cmd = ["-i", video_path, "-vf", drawtext_filter(text, fontcolor=f"{color}@0.8", fontsize=font_size, x=x_expr.removeprefix("x="), y=y_expr.removeprefix("y=")), "-c:a", "copy", "-y", out]
    item_id = add_queue_item(data.get("project_id", 0), "ffmpeg_command", video_path, {"cmd": cmd, "output_path": out})
    return {"id": item_id, "output": out}


@router.post("/branding/qr")
def qr_overlay(data: dict):
    video_path = data.get("video_path", "")
    content = data.get("content", "https://example.com")
    position = data.get("position", "bottom_right")
    size = data.get("size", 120)
    if not video_path:
        raise HTTPException(400, "Yeu cau cung cap video_path")
    video_path = str(http_safe_media_input(video_path, field="video path"))
    qr_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    try:
        import qrcode
        img = qrcode.make(content)
        img.save(qr_file)
    except ImportError:
        try:
            import os
            os.remove(qr_file)
        except Exception:
            pass
        raise HTTPException(503, "QR branding requires qrcode[pil]. Install dependencies from requirements.txt.")
    pos_map = {"top_right": "(W-w-20):20", "bottom_right": "(W-w-20):(H-h-20)", "center": "(W-w)/2:(H-h)/2"}
    pos = pos_map.get(position, "(W-w-20):(H-h-20)")
    out = str(http_safe_output_path(EXPORTS_DIR / f"project_{int(data.get('project_id', 0) or 0)}" / f"{Path(video_path).stem}_qr.mp4", field="qr output", extensions={".mp4"}))
    cmd = ["-i", video_path, "-i", qr_file, "-filter_complex", f"[1:v]scale={size}:{size}[qr];[0:v][qr]overlay={pos}", "-c:a", "copy", "-y", out]
    item_id = add_queue_item(data.get("project_id", 0), "ffmpeg_command", video_path, {"cmd": cmd, "output_path": out, "temp_files": [qr_file]})
    return {"id": item_id, "output": out}
