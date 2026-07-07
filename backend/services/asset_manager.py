"""
Asset Manager — scans directory trees and indexes media files.
Watches for changes and caches asset metadata.
"""
import json
from pathlib import Path
from ..config import DATA_DIR, CACHE_DIR

MEDIA_EXTENSIONS = {
    "video": [".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v"],
    "audio": [".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma"],
    "image": [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".svg"],
    "subtitle": [".srt", ".ass", ".vtt", ".ssa"],
    "project": [".json"],
    "preset": [".json"],
}

ASSET_ROOTS = {
    "videos": DATA_DIR / "downloads",
    "music": DATA_DIR / "downloads",
    "voice": DATA_DIR / "voices",
    "subtitle": DATA_DIR / "subtitles",
    "templates": DATA_DIR / "templates",
    "exports": DATA_DIR / "exports",
}

CACHE_FILE = CACHE_DIR / "asset_cache.json"


def scan_assets(category: str = None, force: bool = False) -> dict:
    """Scan asset directories and return categorized results."""
    cache = _load_cache()

    if not force and cache:
        if category:
            return cache.get(category, {})
        return cache

    result = {}

    for cat, root in ASSET_ROOTS.items():
        if category and cat != category:
            continue
        root.mkdir(parents=True, exist_ok=True)
        items = _walk_dir(root, cat)
        result[cat] = items

    _save_cache(result)
    return result if not category else result.get(category, [])


def get_asset_subtree() -> dict:
    """Get full asset tree structure for sidebar."""
    scan_assets(force=False)
    return {
        "videos": {
            "path": str(ASSET_ROOTS["videos"]),
            "subfolders": _get_subfolders(ASSET_ROOTS["videos"]),
        },
        "audio": {
            "path": str(ASSET_ROOTS["music"]),
            "subfolders": _get_subfolders(ASSET_ROOTS["music"]),
        },
        "voice": {
            "path": str(ASSET_ROOTS["voice"]),
            "subfolders": _get_subfolders(ASSET_ROOTS["voice"]),
        },
        "subtitle": {
            "path": str(ASSET_ROOTS["subtitle"]),
            "subfolders": _get_subfolders(ASSET_ROOTS["subtitle"]),
        },
        "templates": {
            "path": str(ASSET_ROOTS["templates"]),
            "subfolders": _get_subfolders(ASSET_ROOTS["templates"]),
        },
    }


def get_by_type(file_type: str, subfolder: str = "") -> list:
    """Get assets filtered by type and optional subfolder."""
    ext_map = {"video": MEDIA_EXTENSIONS["video"], "audio": MEDIA_EXTENSIONS["audio"],
                "image": MEDIA_EXTENSIONS["image"], "subtitle": MEDIA_EXTENSIONS["subtitle"]}
    exts = ext_map.get(file_type, [])
    all_files = scan_assets()
    results = []

    for cat, items in all_files.items():
        for item in items:
            if subfolder and subfolder not in item["path"]:
                continue
            if not exts or Path(item["path"]).suffix.lower() in exts:
                results.append(item)

    return results


def get_recent(limit: int = 30) -> list:
    """Get recently added assets across all categories."""
    cache = _load_cache()
    all_items = []
    for cat, items in cache.items():
        for item in items:
            item["category"] = cat
            all_items.append(item)

    all_items.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    return all_items[:limit]


def search(query: str, category: str = None) -> list:
    """Search assets by filename."""
    query = query.lower()
    results = []
    cache = _load_cache()

    for cat, items in cache.items():
        if category and cat != category:
            continue
        for item in items:
            if query in item["name"].lower():
                item["category"] = cat
                results.append(item)

    return results


def _walk_dir(root: Path, category: str) -> list:
    items = []
    try:
        for f in sorted(root.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                stat = f.stat()
                items.append({
                    "name": f.name,
                    "path": str(f),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "ext": f.suffix.lower(),
                    "category": category,
                    "folder": str(f.parent.relative_to(root)) if f.parent != root else "",
                })
    except PermissionError:
        pass
    return items


def _get_subfolders(root: Path) -> list:
    folders = []
    try:
        for f in sorted(root.iterdir()):
            if f.is_dir():
                folders.append({
                    "name": f.name,
                    "path": str(f),
                    "count": len(list(f.glob("*"))),
                })
    except PermissionError:
        pass
    return folders


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cache(data: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
