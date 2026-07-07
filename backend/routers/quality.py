from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import SUBTITLES_DIR
from ..database import db_cursor
from ..services.quality_checker import run_quality_check

router = APIRouter()


class QualityCheckRequest(BaseModel):
    video_path: str
    subtitle_path: str | None = None
    font_path: str | None = None
    target: str = "short_9_16"
    source_subtitle_path: str | None = None
    aligned_subtitle_path: str | None = None


def _latest_project_paths(project_id: int) -> dict:
    with db_cursor() as cur:
        export = cur.execute(
            "SELECT output_path FROM exports WHERE project_id=? AND output_path IS NOT NULL ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    data = {"video_path": export["output_path"] if export else ""}
    candidates = sorted(SUBTITLES_DIR.glob(f"project_{project_id}*.srt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        data["subtitle_path"] = str(candidates[0])
    return data


@router.post("/check")
def check_quality(req: QualityCheckRequest):
    return run_quality_check(
        video_path=req.video_path,
        subtitle_path=req.subtitle_path,
        font_path=req.font_path,
        target=req.target,
        source_subtitle_path=req.source_subtitle_path,
        aligned_subtitle_path=req.aligned_subtitle_path,
    )


@router.get("/project/{project_id}")
def check_project_quality(project_id: int, target: str = "short_9_16"):
    paths = _latest_project_paths(project_id)
    if not paths.get("video_path"):
        raise HTTPException(404, "Project has no exported video to check")
    return run_quality_check(target=target, **paths)
