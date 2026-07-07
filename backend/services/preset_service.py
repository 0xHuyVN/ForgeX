"""
Preset Manager — normalized preset structure with JSON file storage.
Structure: { voice: {}, subtitle: {}, export: {}, enhance: {} }
Stored in data/presets/ as individual JSON files.
"""
import json
from ..database import db_cursor
from ..config import PRESETS_DIR

PRESET_EXT = ".json"

DEFAULT_PRESETS = {
    "draft_fast": {
        "name": "Draft Fast",
        "description": "Preview nhanh 720p, uu tien toc do",
        "voice": {"provider": "edge", "voice": "vi-VN-NamMinhNeural", "speed": 1.0, "keep_bgm": True, "bgm_volume": 0.1},
        "subtitle": {"font": "Arial", "size": 36, "color": "#FFFFFF", "stroke": 2, "shadow": "soft", "position": "bottom", "burn": True, "region": {"x": 0.1, "y": 0.78, "width": 0.8, "height": 0.15}},
        "export": {"resolution": "1280x720", "fps": 30, "codec": "h264", "bitrate": "auto", "audio_bitrate": "128k", "format": "mp4", "gpu": "auto", "preset": "veryfast", "quality": "draft"},
        "enhance": {"lut": "Cinematic", "brightness": 50, "contrast": 55, "saturation": 60, "vignette": 0, "watermark": False},
    },
    "nvenc_fast": {
        "name": "NVENC Fast",
        "description": "Render nhanh bang NVIDIA NVENC neu co GPU",
        "voice": {"provider": "edge", "voice": "vi-VN-NamMinhNeural", "speed": 1.0, "keep_bgm": True, "bgm_volume": 0.1},
        "subtitle": {"font": "Arial", "size": 42, "color": "#FFFFFF", "stroke": 2, "shadow": "soft", "position": "bottom", "burn": True, "region": {"x": 0.1, "y": 0.78, "width": 0.8, "height": 0.15}},
        "export": {"resolution": "1920x1080", "fps": 30, "codec": "h264", "bitrate": "8M", "audio_bitrate": "192k", "format": "mp4", "gpu": "nvenc", "preset": "fast", "quality": "fast"},
        "enhance": {"lut": "Cinematic", "brightness": 50, "contrast": 55, "saturation": 60, "vignette": 8, "watermark": True},
    },
    "quality": {
        "name": "Quality",
        "description": "Ban xuat chat luong cao, uu tien do net",
        "voice": {"provider": "edge", "voice": "vi-VN-NamMinhNeural", "speed": 1.0, "keep_bgm": True, "bgm_volume": 0.1},
        "subtitle": {"font": "Arial", "size": 42, "color": "#FFFFFF", "stroke": 2, "shadow": "soft", "position": "bottom", "burn": True, "region": {"x": 0.1, "y": 0.78, "width": 0.8, "height": 0.15}},
        "export": {"resolution": "1920x1080", "fps": 30, "codec": "h264", "bitrate": "auto", "audio_bitrate": "192k", "format": "mp4", "gpu": "auto", "preset": "slow", "quality": "quality", "crf": "18"},
        "enhance": {"lut": "Cinematic", "brightness": 50, "contrast": 55, "saturation": 60, "vignette": 12, "watermark": True},
    },
    "movie_review": {
        "name": "Movie Review",
        "description": "Review phim 16:9 full HD",
        "voice": {"provider": "edge", "voice": "vi-VN-NamMinhNeural", "speed": 1.0, "keep_bgm": True, "bgm_volume": 0.1},
        "subtitle": {"font": "Arial", "size": 42, "color": "#FFFFFF", "stroke": 2, "shadow": "soft", "position": "bottom", "burn": True, "region": {"x": 0.1, "y": 0.78, "width": 0.8, "height": 0.15}},
        "export": {"resolution": "1920x1080", "fps": 30, "codec": "h264", "bitrate": "8M", "audio_bitrate": "192k", "format": "mp4", "gpu": "cpu"},
        "enhance": {"lut": "Cinematic", "brightness": 50, "contrast": 55, "saturation": 60, "vignette": 12, "watermark": True},
    },
    "tiktok_recap": {
        "name": "TikTok Recap",
        "description": "TikTok dọc 9:16",
        "voice": {"provider": "edge", "voice": "vi-VN-NamMinhNeural", "speed": 1.1, "keep_bgm": False, "bgm_volume": 0.0},
        "subtitle": {"font": "Roboto", "size": 38, "color": "#FFFFFF", "stroke": 3, "shadow": "hard", "position": "center", "burn": True, "region": {"x": 0.05, "y": 0.7, "width": 0.9, "height": 0.2}},
        "export": {"resolution": "1080x1920", "fps": 30, "codec": "h264", "bitrate": "6M", "audio_bitrate": "128k", "format": "mp4", "gpu": "cpu"},
        "enhance": {"lut": "Warm Film", "brightness": 52, "contrast": 58, "saturation": 65, "vignette": 15, "watermark": False},
    },
    "shorts_auto": {
        "name": "Shorts Auto",
        "description": "YouTube Shorts 9:16 60fps",
        "voice": {"provider": "edge", "voice": "vi-VN-NamMinhNeural", "speed": 1.0, "keep_bgm": True, "bgm_volume": 0.08},
        "subtitle": {"font": "Arial", "size": 44, "color": "#FFD700", "stroke": 2, "shadow": "soft", "position": "bottom", "burn": True, "region": {"x": 0.05, "y": 0.75, "width": 0.9, "height": 0.18}},
        "export": {"resolution": "1080x1920", "fps": 60, "codec": "h264", "bitrate": "10M", "audio_bitrate": "192k", "format": "mp4", "gpu": "nvenc"},
        "enhance": {"lut": "Cinematic", "brightness": 48, "contrast": 60, "saturation": 55, "vignette": 10, "watermark": True},
    },
    "anime_recap": {
        "name": "Anime Recap",
        "description": "Anime recap với subtitle đẹp",
        "voice": {"provider": "edge", "voice": "vi-VN-NamMinhNeural", "speed": 0.95, "keep_bgm": True, "bgm_volume": 0.12},
        "subtitle": {"font": "Roboto", "size": 48, "color": "#FFFFFF", "stroke": 2, "shadow": "soft", "position": "bottom", "burn": True, "region": {"x": 0.1, "y": 0.78, "width": 0.8, "height": 0.15}},
        "export": {"resolution": "1920x1080", "fps": 24, "codec": "h265", "bitrate": "6M", "audio_bitrate": "192k", "format": "mp4", "gpu": "cpu"},
        "enhance": {"lut": "Anime", "brightness": 45, "contrast": 65, "saturation": 70, "vignette": 8, "watermark": True},
    },
}


def init_presets():
    """Load default presets into DB + files if empty."""
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM presets")
        if cur.fetchone()[0] > 0:
            return

    for key, preset in DEFAULT_PRESETS.items():
        save_preset(key, preset)


def list_presets() -> dict:
    """Return all presets as {name: config} dict. Files override DB."""
    presets = {}

    with db_cursor() as cur:
        rows = cur.execute("SELECT * FROM presets ORDER BY name").fetchall()
        for r in rows:
            try:
                presets[r["name"]] = json.loads(r["config"])
            except (json.JSONDecodeError, TypeError):
                pass

    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted(PRESETS_DIR.glob(f"*{PRESET_EXT}")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            name = data.get("name", f.stem)
            presets[name] = data
        except (json.JSONDecodeError, OSError):
            pass

    return presets


def get_preset(name: str) -> dict:
    """Get a single preset by name."""
    presets = list_presets()
    if name in presets:
        return presets[name]

    f = PRESETS_DIR / f"{name.lower().replace(' ', '_')}{PRESET_EXT}"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    key = name.lower().replace(" ", "_")
    if key in DEFAULT_PRESETS:
        return DEFAULT_PRESETS[key]

    return None


def save_preset(name: str, config: dict):
    """Save a preset to DB + JSON file."""
    with db_cursor() as cur:
        cur.execute(
            """INSERT INTO presets (name, config) VALUES (?,?)
               ON CONFLICT(name) DO UPDATE SET config=excluded.config""",
            (name, json.dumps(config, ensure_ascii=False)),
        )

    key = name.lower().replace(" ", "_")
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    f = PRESETS_DIR / f"{key}{PRESET_EXT}"
    f.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def delete_preset(name: str):
    """Delete a preset from DB + file."""
    with db_cursor() as cur:
        cur.execute("DELETE FROM presets WHERE name=?", (name,))

    key = name.lower().replace(" ", "_")
    f = PRESETS_DIR / f"{key}{PRESET_EXT}"
    if f.exists():
        f.unlink()


def export_preset(name: str) -> str:
    """Export preset to JSON string."""
    preset = get_preset(name)
    return json.dumps(preset, indent=2, ensure_ascii=False) if preset else "{}"


def import_preset(json_str: str) -> str:
    """Import preset from JSON string."""
    try:
        data = json.loads(json_str)
        name = data.get("name", "Imported Preset")
        save_preset(name, data)
        return name
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")
