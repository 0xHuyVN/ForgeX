import re
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Query, HTTPException
from ..database import db_cursor
from ..config import DATA_DIR
from ..services.asset_manager import scan_assets, get_recent, search, get_asset_subtree
from ..services.asset_organizer import (
    ensure_project_asset_tree,
    get_project_asset_structure,
    organize_project_assets,
    register_project_asset,
)

router = APIRouter()


def _safe_component(value: str, field: str) -> str:
    value = (value or "").strip()
    if not value or value in {".", ".."}:
        raise HTTPException(400, f"Invalid {field}")
    if Path(value).name != value or "\\" in value or "/" in value:
        raise HTTPException(400, f"Invalid {field}")
    if not re.fullmatch(r"[\w .@()-]+", value, flags=re.ASCII):
        raise HTTPException(400, f"Invalid {field}")
    return value


def _safe_download_path(*parts: str) -> Path:
    base = (DATA_DIR / "downloads").resolve()
    target = base
    for idx, part in enumerate(parts):
        target = target / _safe_component(part, f"path component {idx + 1}")
    resolved = target.resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise HTTPException(400, "Invalid asset path")
    return resolved

ASSET_CATEGORIES = {
    "videos": ["raw", "edited", "exported"],
    "audio": ["music", "voice", "effects"],
    "subtitle": ["source", "translate"],
    "branding": ["logos", "watermarks", "qr"],
    "templates": [],
}


@router.get("/browse")
def browse_assets(category: str = None, subcategory: str = None):
    base = DATA_DIR / "downloads"
    base.mkdir(exist_ok=True)
    if category and subcategory:
        target = _safe_download_path(category, subcategory)
    elif category:
        target = _safe_download_path(category)
    else:
        target = base

    target.mkdir(parents=True, exist_ok=True)
    items = []
    for f in sorted(target.iterdir()):
        items.append({
            "name": f.name,
            "path": str(f),
            "type": "folder" if f.is_dir() else "file",
            "size": f.stat().st_size if f.is_file() else 0,
            "ext": f.suffix if f.is_file() else "",
        })
    return {
        "category": category or "root",
        "subcategory": subcategory or "",
        "items": items,
    }


@router.get("/categories")
def list_categories():
    return ASSET_CATEGORIES


@router.get("/counts")
def asset_counts():
    """Return asset counts for sidebar display."""
    assets = scan_assets()
    counts = {}
    for cat, items in assets.items():
        counts[cat] = len(items)
    return counts


@router.get("/tree")
def asset_tree():
    """Return nested folder tree for asset sidebar."""
    return get_asset_subtree()


@router.get("/project/{project_id}/structure")
def project_asset_structure(project_id: int):
    return get_project_asset_structure(project_id)


@router.post("/project/{project_id}/ensure")
def ensure_project_assets(project_id: int):
    return {"project_id": project_id, "folders": ensure_project_asset_tree(project_id)}


@router.post("/project/{project_id}/organize")
def organize_project_asset_tree(project_id: int, copy: bool = True):
    return organize_project_assets(project_id, copy=copy)


@router.post("/project/{project_id}/register")
def register_project_asset_endpoint(project_id: int, data: dict):
    path = data.get("path", "")
    if not path:
        raise HTTPException(400, "Missing path")
    return register_project_asset(project_id, path, data.get("type"))


@router.get("/recent")
def recent_assets(limit: int = Query(20)):
    return get_recent(limit)


@router.get("/search")
def search_assets(q: str = Query(""), category: str = None):
    return search(q, category)


@router.post("/upload")
async def upload_asset(category: str, subcategory: str, file: UploadFile = File(...)):
    target = _safe_download_path(category, subcategory)
    target.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    safe_name = _safe_component(Path(file.filename or "upload.bin").name, "filename")
    out = (target / safe_name).resolve()
    try:
        out.relative_to((DATA_DIR / "downloads").resolve())
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    out.write_bytes(content)
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO assets (type, name, path, size) VALUES (?,?,?,?)",
            (category, safe_name, str(out), len(content)),
        )
    return {"path": str(out), "id": cur.lastrowid}
