from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..services.analytics_service import analyze_render

router = APIRouter()


class AnalyticsRequest(BaseModel):
    project_id: int
    video_path: str | None = None
    subtitle_path: str | None = None
    thumbnail_path: str | None = None


@router.post("/render")
def render_analytics(req: AnalyticsRequest):
    return analyze_render(
        req.project_id,
        video_path=req.video_path,
        subtitle_path=req.subtitle_path,
        thumbnail_path=req.thumbnail_path,
    )


@router.get("/project/{project_id}")
def project_analytics(project_id: int):
    return analyze_render(project_id)
