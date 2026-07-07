from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..config import EXPORTS_DIR, SUBTITLES_DIR
from ..database import db_cursor
from .quality_checker import parse_subtitle_events, probe_media, run_quality_check


CTA_PATTERNS = [
    r"\bsubscribe\b",
    r"\bfollow\b",
    r"\blike\b",
    r"\bcomment\b",
    r"\bshare\b",
    r"đăng ký",
    r"theo dõi",
    r"bình luận",
    r"chia sẻ",
    r"thả tim",
]


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(10.0, value)), 1)


def _latest_export(project_id: int) -> str:
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT output_path FROM exports WHERE project_id=? AND output_path IS NOT NULL ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    if row and row["output_path"]:
        return row["output_path"]
    candidates = sorted((EXPORTS_DIR / f"project_{project_id}").glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0]) if candidates else ""


def _latest_subtitle(project_id: int) -> str:
    candidates = sorted(SUBTITLES_DIR.glob(f"project_{project_id}*.srt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0]) if candidates else ""


def _latest_thumbnail(project_id: int) -> str:
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT path FROM assets WHERE type IN ('thumbnail','image') AND path LIKE ? ORDER BY id DESC LIMIT 1",
            (f"%project_{project_id}%",),
        ).fetchone()
    return row["path"] if row else ""


def _duration_seconds(probe: dict[str, Any]) -> float:
    try:
        return float(probe.get("format", {}).get("duration") or 0)
    except Exception:
        return 0.0


def _score_subtitle(quality: dict[str, Any]) -> float:
    issues = quality.get("issues", [])
    score = 10.0
    for item in issues:
        code = item.get("code", "")
        if code.startswith("SUB_"):
            score -= 0.8 if item.get("severity") == "warning" else 1.8
        if code == "VOICE_DRIFT":
            score -= 0.5
    return _clamp_score(score)


def _score_hook(events: list[dict[str, Any]], probe: dict[str, Any]) -> float:
    duration = _duration_seconds(probe)
    if duration <= 0:
        return 5.0
    first_3s = [ev for ev in events if int(ev.get("start_ms", 0)) < 3000]
    first_text = " ".join(ev.get("text", "") for ev in first_3s)
    score = 5.5
    if first_text:
        score += min(2.0, len(first_text) / 80)
    if "?" in first_text or any(w in first_text.lower() for w in ["shock", "bí mật", "su that", "sự thật", "bat ngo", "bất ngờ"]):
        score += 1.0
    if duration <= 90:
        score += 0.8
    return _clamp_score(score)


def _score_retention(events: list[dict[str, Any]], probe: dict[str, Any]) -> float:
    duration = _duration_seconds(probe)
    if duration <= 0:
        return 5.0
    subtitle_density = len(events) / max(1.0, duration / 60)
    score = 5.0 + min(2.0, subtitle_density / 18)
    if 30 <= duration <= 180:
        score += 1.0
    elif duration > 600:
        score -= 1.0
    return _clamp_score(score)


def _score_cta(events: list[dict[str, Any]], probe: dict[str, Any]) -> float:
    duration_ms = int(_duration_seconds(probe) * 1000)
    if not events:
        return 3.0
    tail_start = max(0, int(duration_ms * 0.75))
    tail_text = " ".join(ev.get("text", "") for ev in events if int(ev.get("start_ms", 0)) >= tail_start).lower()
    any_cta = any(re.search(pattern, tail_text, flags=re.IGNORECASE) for pattern in CTA_PATTERNS)
    return 8.0 if any_cta else 4.5


def _score_thumbnail(path: str) -> float:
    if not path or not Path(path).exists():
        return 5.0
    try:
        from PIL import Image, ImageStat
        image = Image.open(path).convert("L")
        width, height = image.size
        contrast = ImageStat.Stat(image).stddev[0]
        score = 5.0
        if width >= 720 and height >= 720:
            score += 1.5
        score += min(2.5, contrast / 28)
        return _clamp_score(score)
    except Exception:
        return 6.0


def analyze_render(project_id: int, video_path: str | None = None, subtitle_path: str | None = None, thumbnail_path: str | None = None) -> dict[str, Any]:
    video_path = video_path or _latest_export(project_id)
    subtitle_path = subtitle_path or _latest_subtitle(project_id)
    thumbnail_path = thumbnail_path or _latest_thumbnail(project_id)
    if not video_path or not Path(video_path).exists():
        return {"ok": False, "error": "No render output found"}

    probe = probe_media(video_path)
    events = parse_subtitle_events(subtitle_path) if subtitle_path and Path(subtitle_path).exists() else []
    quality = run_quality_check(video_path, subtitle_path=subtitle_path if subtitle_path else None)
    scores = {
        "hook": _score_hook(events, probe),
        "retention": _score_retention(events, probe),
        "subtitle": _score_subtitle(quality),
        "cta": _score_cta(events, probe),
        "thumbnail": _score_thumbnail(thumbnail_path),
    }
    return {
        "ok": True,
        "project_id": project_id,
        "scores": scores,
        "quality_status": quality.get("status"),
        "paths": {
            "video": video_path,
            "subtitle": subtitle_path,
            "thumbnail": thumbnail_path,
        },
    }
