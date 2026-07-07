import json
import math
import os
import subprocess
from pathlib import Path

from ..config import CACHE_DIR, FFMPEG_PATH, FFPROBE_PATH
from .ffmpeg_utils import get_video_info, run_ffmpeg


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _probe_duration(path: str) -> float:
    try:
        result = subprocess.run(
            [FFPROBE_PATH, "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return float(json.loads(result.stdout).get("format", {}).get("duration", 0) or 0)
    except Exception:
        return 0.0


def detect_subject_centers(video_path: str, sample_interval: float = 0.75, max_samples: int = 240) -> list[dict]:
    """Return sampled subject centers as [{time, x, confidence}] in source-pixel coordinates."""
    try:
        import cv2
    except Exception as e:
        raise RuntimeError("opencv-python is required for auto-reframing") from e

    detector = None
    detector_kind = "haar"
    try:
        import mediapipe as mp
        if hasattr(mp, "solutions"):
            detector = mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.45)
            detector_kind = "mediapipe"
    except Exception:
        detector = None
    if detector is None:
        cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        detector = cv2.CascadeClassifier(cascade_path)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = frame_count / fps if frame_count else _probe_duration(video_path)
    step_frames = max(1, int(fps * sample_interval))
    total_samples = min(max_samples, int(math.ceil((duration * fps) / step_frames)) if duration else max_samples)

    points = []
    frame_idx = 0
    samples = 0
    last_x = None

    try:
        while samples < total_samples:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            best = None
            if detector_kind == "mediapipe":
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = detector.process(rgb)
                if result.detections:
                    for det in result.detections:
                        box = det.location_data.relative_bounding_box
                        score = float(det.score[0]) if det.score else 0.0
                        area = max(0.0, box.width) * max(0.0, box.height)
                        rank = score * (1.0 + area)
                        if best is None or rank > best[0]:
                            cx = (box.xmin + box.width / 2.0) * w
                            best = (rank, cx, score)
            else:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 40))
                for (x, y, fw, fh) in faces:
                    rank = fw * fh
                    if best is None or rank > best[0]:
                        best = (rank, x + fw / 2.0, 0.6)
            if best:
                detected_x = best[1]
                confidence = best[2]
            else:
                detected_x = last_x if last_x is not None else w / 2.0
                confidence = 0.0

            if last_x is None:
                smooth_x = detected_x
            else:
                smooth_x = last_x * 0.72 + detected_x * 0.28
            last_x = smooth_x
            points.append({"time": frame_idx / fps, "x": smooth_x, "confidence": confidence})
            frame_idx += step_frames
            samples += 1
    finally:
        cap.release()
        if detector_kind == "mediapipe":
            detector.close()

    if not points:
        info = get_video_info(video_path)
        points = [{"time": 0.0, "x": (info.get("width", 1920) or 1920) / 2.0, "confidence": 0.0}]
    return points


def build_dynamic_crop_filter(video_path: str, out_w: int = 1080, out_h: int = 1920, sample_interval: float = 0.75) -> tuple[str, dict]:
    info = get_video_info(video_path)
    src_w = int(info.get("width", 1920) or 1920)
    src_h = int(info.get("height", 1080) or 1080)
    target_aspect = out_w / out_h
    src_aspect = src_w / src_h if src_h else 16 / 9

    points = detect_subject_centers(video_path, sample_interval=sample_interval)
    if src_aspect >= target_aspect:
        crop_h = src_h
        crop_w = max(2, int(src_h * target_aspect) // 2 * 2)
        max_x = max(0, src_w - crop_w)
        xs = [int(_clamp(p["x"] - crop_w / 2, 0, max_x)) for p in points]
        expr = str(xs[-1])
        for point, x in reversed(list(zip(points[:-1], xs[:-1]))):
            expr = f"if(lt(t,{point['time'] + sample_interval:.3f}),{x},{expr})"
        crop = f"crop={crop_w}:{crop_h}:x='{expr}':y=0"
    else:
        crop_w = src_w
        crop_h = max(2, int(src_w / target_aspect) // 2 * 2)
        max_y = max(0, src_h - crop_h)
        y = int(max_y / 2)
        crop = f"crop={crop_w}:{crop_h}:x=0:y={y}"

    vf = f"{crop},scale={out_w}:{out_h}:flags=lanczos"
    metadata = {"source": {"width": src_w, "height": src_h}, "output": {"width": out_w, "height": out_h}, "points": points}
    return vf, metadata


def render_auto_reframe(video_path: str, output_path: str, out_w: int = 1080, out_h: int = 1920, fps: int = 30) -> dict:
    vf, metadata = build_dynamic_crop_filter(video_path, out_w=out_w, out_h=out_h)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_PATH, "-y", "-i", video_path,
        "-vf", vf,
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k",
        output_path,
    ]
    if not run_ffmpeg(cmd[1:]):
        raise RuntimeError("Auto-reframe FFmpeg render failed")
    meta_path = Path(CACHE_DIR) / f"auto_reframe_{Path(output_path).stem}.json"
    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"output": output_path, "metadata": str(meta_path), "filter": vf}
