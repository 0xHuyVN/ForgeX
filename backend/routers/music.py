from fastapi import APIRouter, HTTPException, UploadFile, File

from ..config import DATA_DIR
from ..services.queue_manager import add_queue_item
from ..services.path_guard import (
    http_safe_filename,
    http_safe_inside_data,
    http_safe_media_input,
    http_safe_output_path,
)

router = APIRouter()


@router.post("/upload")
async def upload_music(file: UploadFile = File(...)):
    content = await file.read()
    safe_name = http_safe_filename(file.filename or "upload.bin", field="filename")
    target_root = http_safe_inside_data(DATA_DIR / "downloads", field="upload target")
    out = (target_root / safe_name).resolve()
    try:
        out.relative_to(target_root)
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    out.write_bytes(content)
    return {"path": str(out)}


@router.post("/process")
def process_audio(
    input_path: str,
    volume: float = 1.0,
    fade_in: float = 0,
    fade_out: float = 0,
    normalize: bool = False,
    project_id: int = 0,
):
    input_path = str(http_safe_media_input(input_path, field="music input", extensions={".mp3", ".wav", ".m4a", ".flac", ".ogg"}))
    item_id = add_queue_item(
        project_id,
        "process_music",
        input_path,
        {"volume": volume, "fade_in": fade_in, "fade_out": fade_out, "normalize": normalize},
    )
    return {"id": item_id, "message": "Da dua tien trinh xu ly vao hang doi"}


@router.post("/duck")
def auto_ducking(data: dict):
    music_path = str(http_safe_media_input(data.get("music_path", ""), field="music path", extensions={".mp3", ".wav", ".m4a", ".flac", ".ogg"}))
    raw_voice_path = data.get("voice_path") or ""
    voice_path = (
        str(http_safe_media_input(raw_voice_path, field="voice path", extensions={".mp3", ".wav", ".m4a", ".flac", ".ogg"}))
        if raw_voice_path
        else ""
    )
    item_id = add_queue_item(data.get("project_id", 0), "duck_music", music_path, {"voice_path": voice_path})
    return {"id": item_id, "message": "Da dua tien trinh ducking vao hang doi"}


@router.get("/files")
def list_music(folder: str = ""):
    """List music files. The ``folder`` query param, if provided, MUST resolve
    inside an allowed root (DATA_DIR by default)."""
    if folder:
        music_dir = http_safe_inside_data(folder, field="folder", require_exists=True)
    else:
        music_dir = (DATA_DIR / "downloads").resolve()
        music_dir.mkdir(parents=True, exist_ok=True)

    if not music_dir.is_dir():
        raise HTTPException(400, "Thu muc nhac khong ton tai")

    files = []
    for f in music_dir.iterdir():
        if f.is_file() and f.suffix.lower() in (".mp3", ".wav", ".m4a", ".flac", ".ogg"):
            files.append({"name": f.name, "path": str(f), "size": f.stat().st_size})
    return files


@router.post("/crossfade")
def crossfade_music(audio_a: str, audio_b: str, duration: float = 2.0, project_id: int = 0):
    import os
    a = http_safe_inside_data(audio_a, field="audio_a", require_exists=True)
    b = http_safe_inside_data(audio_b, field="audio_b", require_exists=True)
    out_name = http_safe_filename(f"crossfade_{a.name}", field="output filename")
    out = str(http_safe_output_path((DATA_DIR / "downloads" / out_name).resolve(), field="crossfade output", extensions={".mp3", ".wav", ".m4a", ".flac", ".ogg"}))
    cmd = [
        "-i", str(a), "-i", str(b),
        "-filter_complex", f"acrossfade=d={duration}",
        "-y", out,
    ]
    item_id = add_queue_item(project_id, "ffmpeg_command", str(a), {"cmd": cmd, "output_path": out, "category": "audio"})
    return {"id": item_id, "output": out}


@router.get("/playlist")
def list_playlists():
    playlist_dir = DATA_DIR / "playlists"
    playlist_dir.mkdir(parents=True, exist_ok=True)
    playlists = []
    for f in sorted(playlist_dir.glob("*.json")):
        try:
            import json
            data = json.loads(f.read_text(encoding="utf-8"))
            playlists.append({
                "name": data.get("name", f.stem),
                "file": f.name,
                "tracks": data.get("tracks", []),
                "count": len(data.get("tracks", [])),
            })
        except (json.JSONDecodeError, OSError):
            pass
    return playlists


@router.post("/playlist")
def save_playlist(data: dict):
    name = data.get("name", "Untitled Playlist")
    safe = http_safe_filename(name, field="playlist name")
    playlist_dir = DATA_DIR / "playlists"
    playlist_dir.mkdir(parents=True, exist_ok=True)
    import json
    f = playlist_dir / f"{safe.lower().replace(' ', '_')}.json"
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"message": f"Da luu Playlist '{name}'", "file": str(f)}


@router.delete("/playlist/{name}")
def delete_playlist(name: str):
    safe = http_safe_filename(name, field="playlist name")
    playlist_dir = DATA_DIR / "playlists"
    f = playlist_dir / f"{safe.lower().replace(' ', '_')}.json"
    if f.exists():
        f.unlink()
    return {"message": f"Da xoa Playlist '{name}'"}
