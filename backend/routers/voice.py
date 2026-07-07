import hashlib

from fastapi import APIRouter, UploadFile, File, HTTPException

from ..config import VOICES_DIR
from ..models.schemas import TTSRequest
from ..services.queue_manager import add_queue_item
from ..services.tts_engine import synthesize
from ..services.path_guard import (
    http_safe_filename,
    http_safe_inside_data,
    http_safe_media_input,
    http_safe_output_path,
)

router = APIRouter()


def _stable_key(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


@router.post("/tts")
def text_to_speech(data: TTSRequest):
    out = str(http_safe_output_path(data.output_path or (VOICES_DIR / f"tts_{_stable_key(data.provider, data.voice, data.speed, data.text)}.wav"), field="tts output", extensions={".wav", ".mp3", ".m4a"}))
    item_id = add_queue_item(data.project_id, "tts_text", "", {
        "text": data.text,
        "provider": data.provider,
        "voice": data.voice,
        "speed": data.speed,
        "output_path": out,
    })
    return {"id": item_id, "message": "Da dua tien trinh TTS vao hang doi", "output": out}


@router.get("/voices")
def list_voices():
    VOICES_DIR.mkdir(exist_ok=True)
    files = [f for f in VOICES_DIR.iterdir() if f.suffix in (".wav", ".mp3")]
    return [{"name": f.stem, "path": str(f), "size": f.stat().st_size} for f in files]


@router.post("/clone/upload")
async def upload_sample(file: UploadFile = File(...)):
    content = await file.read()
    safe_name = http_safe_filename(file.filename or "sample.bin", field="filename")
    target_root = http_safe_inside_data(VOICES_DIR, field="upload target")
    out = (target_root / f"sample_{safe_name}").resolve()
    try:
        out.relative_to(target_root)
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    out.write_bytes(content)
    return {"path": str(out), "message": "Da tai len tep mau"}


@router.post("/clone/train")
def train_voice(data: dict):
    sample_path = data.get("sample_path", "")
    sample_path = str(http_safe_media_input(sample_path, field="sample path", extensions={".wav", ".mp3", ".m4a", ".flac", ".ogg"}))
    name = http_safe_filename(data.get("name", "default"), field="clone name")
    engine = (data.get("engine") or "bark").lower()
    ref_text = data.get("ref_text", "")
    if engine == "f5":
        from ..services.voice_clone import train_f5_clone
        profile = train_f5_clone(sample_path, name, ref_text=ref_text)
        return {"id": None, "message": f"F5 voice profile ready: {name}", "profile": profile}
    item_id = add_queue_item(data.get("project_id", 0), "train_voice", "", {"sample_path": sample_path, "name": name})
    return {"id": item_id, "message": f"Da bat dau huan luyen cho {name}"}


@router.get("/clone/list")
def list_clones():
    clones_dir = VOICES_DIR / "clones"
    clones_dir.mkdir(exist_ok=True)
    result = []
    for d in clones_dir.iterdir():
        if d.is_dir():
            has_prompt = (d / "voice_prompt.npz").exists()
            has_f5 = (d / "f5_profile.json").exists()
            preview_path = str(d / "preview.wav") if (d / "preview.wav").exists() else None
            done_text = ""
            done_file = d / "done.txt"
            if done_file.exists():
                done_text = done_file.read_text()
            result.append({"name": d.name, "ready": has_prompt or has_f5, "bark_ready": has_prompt, "f5_ready": has_f5, "preview": preview_path, "status": done_text})
    return result


@router.post("/clone/generate")
def generate_clone_tts(text: str, clone_name: str, project_id: int = 0):
    safe_clone = http_safe_filename(clone_name, field="clone name")
    out = str(http_safe_output_path(VOICES_DIR / f"clone_{_stable_key(safe_clone, text)}_{safe_clone}.wav", field="clone output", extensions={".wav"}))
    item_id = add_queue_item(project_id, "tts_text", "", {
        "text": text,
        "provider": "clone",
        "voice": safe_clone,
        "speed": 1.0,
        "output_path": out,
    })
    return {"id": item_id, "message": "Da dua tien trinh tao giong clone vao hang doi", "output": out}


@router.post("/clone/oneclick")
def clone_oneclick(data: dict):
    """1-Click Clone Review: clone a voice from an uploaded sample, then run the full
    Auto-Review pipeline (download → STT → translate → rewrite → TTS → render) using it."""
    sample_path = data.get("sample_path", "")
    if not sample_path:
        return {"error": "Thiếu sample_path — hãy tải lên mẫu giọng trước"}
    sample_path = str(http_safe_media_input(sample_path, field="sample path", extensions={".wav", ".mp3", ".m4a", ".flac", ".ogg"}))
    engine = (data.get("engine") or "f5").lower()
    name = http_safe_filename(data.get("name") or "oneclick", field="clone name")
    project_id = data.get("project_id", 0)

    params = {
        # clone settings
        "clone_engine": engine,
        "clone_name": name,
        "sample_path": sample_path,
        "ref_text": data.get("ref_text", ""),
        # pipeline settings
        "url": data.get("url", ""),
        "platform": data.get("platform", "auto"),
        "quality": data.get("quality", "best"),
        "language": data.get("language", "vi"),
        "source_lang": data.get("source_lang", "vi"),
        "target_lang": data.get("target_lang", "vi"),
        "translate_enabled": data.get("translate_enabled", True),
        "translate_engine": data.get("translate_engine", "nllb"),
        "translate_model": data.get("translate_model"),
        "rewrite_enabled": data.get("rewrite_enabled", False),
        "rewrite_style": data.get("rewrite_style", "review"),
        "tts_align": data.get("tts_align", True),
        "speed": data.get("speed", 1.0),
        "burn_subtitle": data.get("burn_subtitle", False),
    }
    item_id = add_queue_item(project_id, "clone_pipeline", data.get("url", ""), params)
    return {"id": item_id, "message": f"Đã đưa tiến trình 1-Click Clone ({engine}:{name}) vào hàng đợi"}


@router.get("/clone/export")
def export_clone_voices():
    clones_dir = VOICES_DIR / "clones"
    clones_dir.mkdir(exist_ok=True)
    exports = []
    for d in clones_dir.iterdir():
        if d.is_dir():
            wavs = list(d.glob("*.wav")) + list(d.glob("*.pth")) + list(d.glob("f5_profile.json"))
            if wavs:
                exports.append({"name": d.name, "path": str(wavs[0]), "files": [str(f) for f in wavs]})
    return {"path": str(clones_dir / "export" / "voice_pack.zip") if exports else None, "clones": exports}


def _play_voice(text: str, provider: str, voice: str, api_key: str = None):
    key = _stable_key(provider, voice, text)
    out = str(VOICES_DIR / f"preview_{key}.wav")
    synthesize(text, provider, voice, 1.0, out, api_key=api_key)
    return {"ready": True, "message": "Da tao file nghe thu", "output": out}


@router.post("/play")
def play_voice_post(data: dict):
    return _play_voice(
        data.get("text") or "Xin chao, day la giong doc thu nghiem",
        data.get("provider") or "edge",
        data.get("voice") or "vi-VN-NamMinhNeural",
        data.get("fpt_api_key"),
    )


@router.get("/play")
def play_voice(text: str = "Xin chao, day la giong doc thu nghiem", provider: str = "edge", voice: str = "vi-VN-NamMinhNeural", project_id: int = 0):
    return _play_voice(text, provider, voice)


@router.get("/edge-voices")
def get_edge_voices():
    try:
        import asyncio
        import edge_tts

        async def fetch():
            return await edge_tts.VoicesManager.create()

        manager = asyncio.run(fetch())
        return [
            {
                "short_name": v["ShortName"],
                "gender": "Nam" if v["Gender"] == "Male" else "Nu",
                "locale": v["Locale"],
                "friendly_name": v["FriendlyName"],
            }
            for v in manager.voices
        ]
    except Exception:
        return [
            {"short_name": "vi-VN-HoaiMyNeural", "gender": "Nu", "locale": "vi-VN", "friendly_name": "Microsoft HoaiMy Online"},
            {"short_name": "vi-VN-NamMinhNeural", "gender": "Nam", "locale": "vi-VN", "friendly_name": "Microsoft NamMinh Online"},
        ]


@router.get("/providers")
def list_providers():
    clones_dir = VOICES_DIR / "clones"
    clones_dir.mkdir(exist_ok=True)
    clone_voices = [d.name for d in clones_dir.iterdir() if d.is_dir() and (d / "voice_prompt.npz").exists()]
    f5_voices = [d.name for d in clones_dir.iterdir() if d.is_dir() and (d / "f5_profile.json").exists()]
    return {
        "providers": [
            {"id": "edge", "name": "Edge TTS (free)", "voices": ["vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural"]},
            {"id": "google", "name": "Google TTS (free)", "voices": ["vi", "en"]},
            {"id": "valtec", "name": "Valtec TTS", "voices": ["NF", "SF", "NM1", "SM", "NM2"]},
            {"id": "capcut", "name": "CapCut TTS", "voices": ["BV074_streaming_dsp|7550087831092251920|sami"]},
            {"id": "f5", "name": "F5-TTS Local", "voices": f5_voices or ["default"]},
            {"id": "azure", "name": "Azure TTS", "voices": ["vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural"]},
            {"id": "elevenlabs", "name": "ElevenLabs", "voices": ["Rachel", "Domi", "Bella"]},
            {"id": "clone", "name": "Voice Clone (Bark)", "voices": clone_voices or ["Chua co giong clone nao"]},
        ]
    }


@router.get("/f5/status")
def f5_status():
    try:
        import f5_tts  # noqa: F401
        installed = True
    except Exception:
        installed = False
    clones_dir = VOICES_DIR / "clones"
    clones_dir.mkdir(exist_ok=True)
    voices = [d.name for d in clones_dir.iterdir() if d.is_dir() and (d / "f5_profile.json").exists()]
    return {"installed": installed, "voices": voices}
