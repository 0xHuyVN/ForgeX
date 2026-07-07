from pydantic import BaseModel
from typing import Optional, List


class ProjectCreate(BaseModel):
    name: str
    preset: str = "Movie Review"
    project_preset: Optional[str] = None
    resolution: str = "1920x1080"
    fps: int = 30


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    preset: Optional[str] = None
    resolution: Optional[str] = None
    fps: Optional[int] = None


class ProjectOut(BaseModel):
    id: int
    name: str
    path: Optional[str]
    preset: str
    resolution: str
    fps: int
    created_at: str
    updated_at: str


class DownloadRequest(BaseModel):
    url: str
    platform: Optional[str] = None
    output_format: str = "mp4"
    quality: str = "best"
    cookie_file: Optional[str] = None
    proxy: Optional[str] = None
    output_dir: Optional[str] = None


class SubtitleRequest(BaseModel):
    project_id: int
    source_path: str
    language: str = "vi"
    engine: str = "whisper"
    translate: bool = False
    target_language: str = "vi"
    translate_engine: str = "nllb"


class VoiceRequest(BaseModel):
    text: str
    provider: str = "edge"
    voice: str = "vi-VN-NamMinhNeural"
    speed: float = 1.0


class QueueItemCreate(BaseModel):
    project_id: int
    type: str
    input_path: str
    output_path: Optional[str] = None
    params: dict = {}
    priority: int = 0


class SceneDetectRequest(BaseModel):
    project_id: int
    video_path: str
    threshold: float = 27.0


class EnhanceRequest(BaseModel):
    project_id: int = 0
    video_path: str
    lut: Optional[str] = None
    brightness: Optional[float] = None
    contrast: Optional[float] = None
    saturation: Optional[float] = None
    temperature: Optional[float] = None
    vignette: Optional[float] = None
    film_look: Optional[str] = None
    watermark: Optional[bool] = False
    watermark_text: Optional[str] = "0xForge"
    transition: Optional[bool] = False
    motion_blur: Optional[bool] = False
    zoom: Optional[bool] = False
    shake: Optional[bool] = False
    particles: Optional[bool] = False
    speed_ramp: Optional[bool] = False
    slow_motion: Optional[bool] = False
    fast_motion: Optional[bool] = False


class EditRequest(BaseModel):
    project_id: int = 0
    video_path: str
    operations: List[dict] = []


class TTSRequest(BaseModel):
    project_id: int = 0
    text: str
    provider: str = "edge"
    voice: str = "vi-VN-NamMinhNeural"
    speed: float = 1.0
    output_path: Optional[str] = None


class TranslateRequest(BaseModel):
    text: str
    source_lang: str = "zh"
    target_lang: str = "vi"
    engine: str = "gpt"
    model: Optional[str] = None
    project_id: Optional[int] = None
    semantic_segmentation: bool = False


class AISummaryRequest(BaseModel):
    project_id: int = 0
    text: str
    max_length: int = 200
    engine: str = "gpt"


class AIRecapRequest(BaseModel):
    video_path: str
    style: str = "review"
    language: str = "vi"


class AIProjectRequest(BaseModel):
    project_id: int
    video_path: Optional[str] = None
    text: Optional[str] = None


class AISceneDetectRequest(BaseModel):
    project_id: int
    video_path: str
    threshold: float = 27.0


class AIThumbnailRequest(BaseModel):
    project_id: int
    video_path: str
    time: float = 0.0


class AITitleRequest(BaseModel):
    project_id: int
    video_path: Optional[str] = None
    style: str = "review"


class AIHashtagRequest(BaseModel):
    project_id: int = 0
    text: str
    count: int = 5
