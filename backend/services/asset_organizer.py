from __future__ import annotations

import shutil
from pathlib import Path

from ..config import PROJECTS_DIR, SUBTITLES_DIR, VOICES_DIR, EXPORTS_DIR, CACHE_DIR
from ..database import db_cursor


PROJECT_ASSET_FOLDERS = {
    "videos": "Videos",
    "audio": "Audio",
    "subtitle": "Subtitle",
    "thumbnail": "Thumbnail",
    "export": "Export",
    "temp": "Temp",
    "voice": "Voice",
    "ai": "AI",
}

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma"}
SUB_EXT = {".srt", ".ass", ".vtt", ".ssa"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def project_asset_root(project_id: int) -> Path:
    return PROJECTS_DIR / f"project_{project_id}"


def ensure_project_asset_tree(project_id: int) -> dict[str, str]:
    root = project_asset_root(project_id)
    root.mkdir(parents=True, exist_ok=True)
    result = {}
    for key, folder in PROJECT_ASSET_FOLDERS.items():
        path = root / folder
        path.mkdir(parents=True, exist_ok=True)
        result[key] = str(path)
    return result


def _category_for_path(path: Path, asset_type: str = "") -> str:
    ext = path.suffix.lower()
    typ = (asset_type or "").lower()
    if typ in {"video", "videos"} or ext in VIDEO_EXT:
        return "videos"
    if typ in {"voice"}:
        return "voice"
    if typ in {"audio", "music"} or ext in AUDIO_EXT:
        return "audio"
    if typ in {"subtitle", "subtitles"} or ext in SUB_EXT:
        return "subtitle"
    if typ in {"thumbnail", "image"} or ext in IMAGE_EXT:
        return "thumbnail"
    if typ in {"export", "exports"}:
        return "export"
    if typ in {"ai"}:
        return "ai"
    return "temp"


def _unique_target(folder: Path, name: str) -> Path:
    target = folder / name
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for idx in range(1, 10_000):
        candidate = folder / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Cannot allocate unique filename for {name}")


def _copy_into(src: Path, dst_folder: Path, move: bool = False) -> Path:
    dst_folder.mkdir(parents=True, exist_ok=True)
    src_resolved = src.resolve()
    if not src_resolved.exists() or not src_resolved.is_file():
        raise FileNotFoundError(str(src))
    try:
        src_resolved.relative_to(dst_folder.resolve())
        return src_resolved
    except ValueError:
        pass
    target = _unique_target(dst_folder, src.name)
    if move:
        shutil.move(str(src_resolved), str(target))
    else:
        shutil.copy2(src_resolved, target)
    return target


def register_project_asset(project_id: int, path: str | Path, asset_type: str | None = None) -> dict:
    path = Path(path)
    category = _category_for_path(path, asset_type or "")
    folders = ensure_project_asset_tree(project_id)
    target = _copy_into(path, Path(folders[category]), move=False)
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO assets (type, name, path, size) VALUES (?,?,?,?)",
            (category, target.name, str(target), target.stat().st_size),
        )
        asset_id = cur.lastrowid
    return {"id": asset_id, "type": category, "name": target.name, "path": str(target)}


def organize_project_assets(project_id: int, copy: bool = True) -> dict:
    folders = ensure_project_asset_tree(project_id)
    moved = []
    sources: list[tuple[Path, str]] = []
    with db_cursor() as cur:
        for row in cur.execute("SELECT type, path FROM assets WHERE path IS NOT NULL").fetchall():
            p = Path(row["path"])
            if p.exists():
                sources.append((p, row["type"] or ""))
        for row in cur.execute("SELECT input_path, output_path FROM exports WHERE project_id=?", (project_id,)).fetchall():
            for key in ("input_path", "output_path"):
                if row[key] and Path(row[key]).exists():
                    sources.append((Path(row[key]), "export" if key == "output_path" else "video"))
        for row in cur.execute("SELECT input_path, output_path FROM queue_items WHERE project_id=?", (project_id,)).fetchall():
            for key in ("input_path", "output_path"):
                if row[key] and Path(row[key]).exists():
                    sources.append((Path(row[key]), ""))

    for root, typ in [
        (SUBTITLES_DIR, "subtitle"),
        (VOICES_DIR, "voice"),
        (EXPORTS_DIR / f"project_{project_id}", "export"),
        (CACHE_DIR, "temp"),
    ]:
        if root.exists():
            pattern = f"project_{project_id}*"
            for path in root.glob(pattern):
                if path.is_file():
                    sources.append((path, typ))

    seen = set()
    for src, typ in sources:
        try:
            src_key = str(src.resolve())
        except Exception:
            continue
        if src_key in seen:
            continue
        seen.add(src_key)
        category = _category_for_path(src, typ)
        folder = Path(folders[category])
        try:
            target = _copy_into(src, folder, move=not copy)
            moved.append({"source": src_key, "target": str(target), "type": category})
        except Exception as exc:
            moved.append({"source": src_key, "error": str(exc), "type": category})
    return {"project_id": project_id, "folders": folders, "items": moved}


def get_project_asset_structure(project_id: int) -> dict:
    folders = ensure_project_asset_tree(project_id)
    result = {"project_id": project_id, "folders": {}}
    for key, folder in folders.items():
        root = Path(folder)
        result["folders"][key] = {
            "path": str(root),
            "items": [
                {
                    "name": p.name,
                    "path": str(p),
                    "size": p.stat().st_size if p.is_file() else 0,
                    "type": "folder" if p.is_dir() else "file",
                    "ext": p.suffix.lower() if p.is_file() else "",
                }
                for p in sorted(root.iterdir())
            ],
        }
    return result
