import os
from pathlib import Path

import sys
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return False

# If frozen (PyInstaller), use executable folder for user data
if getattr(sys, 'frozen', False):
    EXE_DIR = Path(sys.executable).resolve().parent
else:
    EXE_DIR = Path(__file__).resolve().parent.parent

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(EXE_DIR / ".env")
load_dotenv(BASE_DIR / ".env", override=False)
DATA_DIR = EXE_DIR / "data"
DB_DIR = DATA_DIR / "db"
PROJECTS_DIR = DATA_DIR / "projects"
DOWNLOADS_DIR = DATA_DIR / "downloads"
SUBTITLES_DIR = DATA_DIR / "subtitles"
VOICES_DIR = DATA_DIR / "voices"
EXPORTS_DIR = DATA_DIR / "exports"
TEMPLATES_DIR = DATA_DIR / "templates"
PRESETS_DIR = DATA_DIR / "presets"
CACHE_DIR = DATA_DIR / "cache"
ASSETS_DIR = DATA_DIR / "downloads"

DB_PATH = DB_DIR / "app.db"

FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")
FFPROBE_PATH = os.environ.get("FFPROBE_PATH", "ffprobe")
YTDLP_PATH = os.environ.get("YTDLP_PATH", "yt-dlp")
LIBOPENSHOT_PATH = os.environ.get("LIBOPENSHOT_PATH", "")

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "auto")

def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value
    return default


OPENAI_API_KEY = _env_first("OPENAI_API_KEY", "OPENAI_API")
GEMINI_API_KEY = _env_first("GEMINI_API_KEY", "GEMINI_API")
AZURE_TTS_KEY = os.environ.get("AZURE_TTS_KEY", "")
AZURE_TTS_REGION = os.environ.get("AZURE_TTS_REGION", "eastus")
ELEVENLABS_API_KEY = _env_first("ELEVENLABS_API_KEY", "ELEVENLABS_API")
FPT_API_KEY = os.environ.get("FPT_API_KEY", "")
VALTEC_TTS_DIR = os.environ.get("VALTEC_TTS_DIR", "")
CAPCUT_TTS_DIR = os.environ.get("CAPCUT_TTS_DIR", str(BASE_DIR / "vendor" / "capcut-tts-api"))
CAPCUT_SSCRONET_DLL = os.environ.get("CAPCUT_SSCRONET_DLL", "")
F5_TTS_MODEL = os.environ.get("F5_TTS_MODEL", "F5TTS_v1_Base")
F5_TTS_DEVICE = os.environ.get("F5_TTS_DEVICE", "auto")
VOICE_CLONE_PYTHON = os.environ.get("VOICE_CLONE_PYTHON", "")

VOCAL_SEPARATION_ENABLED = os.environ.get("VOCAL_SEPARATION_ENABLED", "false").lower() == "true"
MAX_QUEUE_WORKERS = int(os.environ.get("MAX_QUEUE_WORKERS", "1"))
QUEUE_JOB_TIMEOUT_MINUTES = int(os.environ.get("QUEUE_JOB_TIMEOUT_MINUTES", "60"))
PORT = int(os.environ.get("PORT", "7860"))
HOST = os.environ.get("HOST", "127.0.0.1")
DEFAULT_FPS = 30
DEFAULT_RESOLUTION = "1920x1080"

for d in [DB_DIR, PROJECTS_DIR, DOWNLOADS_DIR, SUBTITLES_DIR, VOICES_DIR, EXPORTS_DIR, TEMPLATES_DIR, PRESETS_DIR, CACHE_DIR, ASSETS_DIR]:
    d.mkdir(parents=True, exist_ok=True)
