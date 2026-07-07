from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import EXPORTS_DIR
from ..database import db_cursor
from ..models.schemas import EditRequest, SceneDetectRequest
from ..services.queue_manager import add_queue_item
from ..services.path_guard import http_safe_media_input, http_safe_output_path

router = APIRouter()


def _clamp_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _atempo_chain(speed: float) -> str:
    filters = []
    remaining = max(0.25, min(4.0, speed))
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.6g}")
    return ",".join(filters)


def _edit_output_path(video_path: str, project_id: int, requested: str | None) -> str:
    default = EXPORTS_DIR / f"project_{project_id}" / f"{Path(video_path).stem}_adjusted.mp4"
    if requested and str(requested).strip():
        target = Path(str(requested).strip())
        if target.suffix.lower() in {".mp4", ".mov", ".mkv"}:
            return str(http_safe_output_path(target, field="edit adjust output", extensions={".mp4", ".mov", ".mkv"}))
        return str(http_safe_output_path(target / f"{Path(video_path).stem}_adjusted.mp4", field="edit adjust output", extensions={".mp4"}))
    return str(http_safe_output_path(default, field="edit adjust output", extensions={".mp4"}))


@router.post("/scene-detect")
def scene_detect(data: SceneDetectRequest):
    video_path = str(http_safe_media_input(data.video_path, field="video path"))
    item_id = add_queue_item(data.project_id, "scene_detect", video_path, {"threshold": data.threshold}, priority=1)
    return {"id": item_id, "message": "Scene detection queued", "project_id": data.project_id}


@router.get("/scenes/{project_id}")
def get_scenes(project_id: int):
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM scenes WHERE project_id=? ORDER BY scene_index",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]


@router.post("/crop")
def crop(data: EditRequest):
    video_path = str(http_safe_media_input(data.video_path, field="video path"))
    out_path = str(http_safe_output_path(EXPORTS_DIR / f"project_{data.project_id}" / f"{Path(video_path).stem}_cropped.mp4", field="crop output", extensions={".mp4"}))
    item_id = None
    for op in data.operations:
        if op.get("type") == "crop":
            vf = f"crop={op.get('w', 1920)}:{op.get('h', 1080)}:{op.get('x', 0)}:{op.get('y', 0)}"
        elif op.get("type") == "rotate":
            angle = float(op.get("angle", 90))
            vf = {90.0: "transpose=1", 180.0: "hflip,vflip", 270.0: "transpose=2"}.get(angle, f"rotate={angle * 3.14159 / 180}:fillcolor=black")
        elif op.get("type") == "hflip":
            vf = "hflip"
        elif op.get("type") == "vflip":
            vf = "vflip"
        else:
            continue
        cmd = [
            "-i", video_path,
            "-vf", vf,
            "-c:a", "copy",
            "-y", out_path,
        ]
        item_id = add_queue_item(data.project_id, "ffmpeg_command", video_path, {"cmd": cmd, "output_path": out_path})
    return {"id": item_id, "output": out_path}


@router.post("/resize")
def resize(data: EditRequest):
    video_path = str(http_safe_media_input(data.video_path, field="video path"))
    out_path = str(http_safe_output_path(EXPORTS_DIR / f"project_{data.project_id}" / f"{Path(video_path).stem}_resized.mp4", field="resize output", extensions={".mp4"}))
    item_id = None
    for op in data.operations:
        if op.get("type") == "resize":
            cmd = [
                "-i", video_path,
                "-vf", f"scale={op.get('width', 1920)}:{op.get('height', 1080)}",
                "-c:a", "copy",
                "-y", out_path,
            ]
            item_id = add_queue_item(data.project_id, "ffmpeg_command", video_path, {"cmd": cmd, "output_path": out_path})
    return {"id": item_id, "output": out_path}


@router.post("/adjust")
def adjust_video(data: dict):
    video_path = str(http_safe_media_input(data.get("video_path", ""), field="video path"))
    project_id = int(data.get("project_id", 0) or 0)
    speed = _clamp_float(data.get("speed"), 1.0, 0.25, 4.0)
    brightness = _clamp_float(data.get("brightness"), 100.0, 20.0, 180.0)
    contrast = _clamp_float(data.get("contrast"), 100.0, 20.0, 200.0)
    saturation = _clamp_float(data.get("saturation"), 100.0, 0.0, 200.0)
    volume = _clamp_float(data.get("volume"), 100.0, 0.0, 200.0)
    rotate = int(_clamp_float(data.get("rotate"), 0.0, 0.0, 270.0)) % 360
    flip_horizontal = bool(data.get("flip_horizontal", False))
    flip_vertical = bool(data.get("flip_vertical", False))

    video_filters = []
    audio_filters = []
    if abs(speed - 1.0) > 0.001:
        video_filters.append(f"setpts={1.0 / speed:.6g}*PTS")
        audio_filters.append(_atempo_chain(speed))
    if any(abs(v - 100.0) > 0.001 for v in (brightness, contrast, saturation)):
        video_filters.append(
            f"eq=brightness={(brightness - 100.0) / 100.0:.6g}:"
            f"contrast={contrast / 100.0:.6g}:"
            f"saturation={saturation / 100.0:.6g}"
        )
    if rotate == 90:
        video_filters.append("transpose=1")
    elif rotate == 180:
        video_filters.append("hflip,vflip")
    elif rotate == 270:
        video_filters.append("transpose=2")
    elif rotate:
        raise HTTPException(400, "rotate must be 0, 90, 180, or 270")
    if flip_horizontal:
        video_filters.append("hflip")
    if flip_vertical:
        video_filters.append("vflip")
    if abs(volume - 100.0) > 0.001:
        audio_filters.append(f"volume={volume / 100.0:.6g}")

    if not video_filters and not audio_filters:
        raise HTTPException(400, "No edit adjustment selected")

    out_path = _edit_output_path(video_path, project_id, data.get("output_path"))
    cmd = ["-i", video_path]
    if video_filters:
        cmd.extend(["-vf", ",".join(video_filters)])
    else:
        cmd.extend(["-c:v", "copy"])
    if audio_filters:
        cmd.extend(["-af", ",".join(audio_filters), "-c:a", "aac"])
    else:
        cmd.extend(["-c:a", "copy"])
    if video_filters:
        cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])
    if Path(out_path).suffix.lower() in {".mp4", ".mov"}:
        cmd.extend(["-movflags", "+faststart"])
    cmd.extend(["-y", out_path])
    item_id = add_queue_item(project_id, "ffmpeg_command", video_path, {
        "cmd": cmd,
        "output_path": out_path,
        "category": "videos",
    })
    return {
        "id": item_id,
        "output": out_path,
        "video_filters": video_filters,
        "audio_filters": audio_filters,
    }


@router.post("/auto-reframe")
def auto_reframe(data: dict):
    video_path = str(http_safe_media_input(data.get("video_path", ""), field="video path"))
    project_id = int(data.get("project_id", 0) or 0)
    output_path = str(http_safe_output_path(
        data.get("output_path") or (EXPORTS_DIR / f"project_{project_id}" / f"{Path(video_path).stem}_reframe_9x16.mp4"),
        field="auto-reframe output",
        extensions={".mp4"},
    ))
    item_id = add_queue_item(
        project_id,
        "auto_reframe",
        video_path,
        {
            "output_path": output_path,
            "width": int(data.get("width", 1080)),
            "height": int(data.get("height", 1920)),
            "fps": int(data.get("fps", 30)),
        },
        priority=1,
    )
    return {"id": item_id, "output": output_path}


@router.post("/split")
def split(data: EditRequest):
    video_path = str(http_safe_media_input(data.video_path, field="video path"))
    out_paths = []
    ids = []
    for i, op in enumerate(data.operations):
        if op.get("type") == "split":
            out = str(http_safe_output_path(EXPORTS_DIR / f"project_{data.project_id}" / f"{Path(video_path).stem}_part{i}.mp4", field="split output", extensions={".mp4"}))
            out_paths.append(out)
            ids.append(add_queue_item(data.project_id, "split", video_path, {"start": op.get("start", 0), "end": op.get("end", 10), "output_path": out}))
    return {"ids": ids, "outputs": out_paths}


@router.post("/merge")
def merge(data: dict):
    file_paths = data.get("video_paths") or data.get("file_paths") or []
    if not file_paths:
        return {"id": None, "output": None, "error": "video_paths or file_paths required"}
    project_id = int(data.get("project_id", 0) or 0)
    safe_files = [str(http_safe_media_input(p, field="merge input")) for p in file_paths]
    out = str(http_safe_output_path(EXPORTS_DIR / f"project_{project_id}" / "merged.mp4", field="merge output", extensions={".mp4"}))
    item_id = add_queue_item(project_id, "merge_videos", "", {"file_paths": safe_files, "output_path": out})
    return {"id": item_id, "output": out}


@router.post("/crossfade")
def crossfade_video(data: EditRequest):
    video_path = str(http_safe_media_input(data.video_path, field="video path"))
    out = str(http_safe_output_path(EXPORTS_DIR / f"project_{data.project_id}" / f"{Path(video_path).stem}_crossfade.mp4", field="crossfade output", extensions={".mp4"}))
    item_id = None
    for op in data.operations:
        if op.get("type") == "crossfade":
            duration = op.get("duration", 2)
            cmd = [
                "-i", video_path,
                "-vf", f"fade=t=in:st=0:d={duration},fade=t=out:st={duration}:d={duration}",
                "-af", f"afade=t=in:st=0:d={duration},afade=t=out:st={duration}:d={duration}",
                "-y", out,
            ]
            item_id = add_queue_item(data.project_id, "ffmpeg_command", video_path, {"cmd": cmd, "output_path": out})
    return {"id": item_id, "output": out}


@router.get("/timeline-data/{project_id}")
def get_timeline_data(project_id: int):
    from ..services.timeline_service import timeline_to_json
    return timeline_to_json(project_id)
