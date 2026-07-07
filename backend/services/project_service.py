"""
Project Manager — full lifecycle for projects.
Create, Open, Save, Version History, Auto Backup, Export/Import.
"""
import json
import shutil
from pathlib import Path
from datetime import datetime
from ..database import db_cursor
from ..config import PROJECTS_DIR
from .preset_service import get_preset


def create_project(name: str, preset: str = "Movie Review", resolution: str = "1920x1080", fps: int = 30) -> dict:
    """Create a new project with preset config + timeline.json + settings.json."""
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO projects (name, preset, resolution, fps) VALUES (?,?,?,?)",
            (name, preset, resolution, fps),
        )
        pid = cur.lastrowid

    proj_dir = PROJECTS_DIR / f"project_{pid}"
    proj_dir.mkdir(parents=True, exist_ok=True)

    preset_config = get_preset(preset) or {}

    project_data = {
        "id": pid,
        "name": name,
        "preset": preset,
        "resolution": resolution,
        "fps": fps,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "preset_config": preset_config,
        "tracks": preset_config.get("tracks", [
            {"type": "video", "count": 1},
            {"type": "subtitle", "count": 1},
            {"type": "voice", "count": 1},
            {"type": "music", "count": 1},
        ]),
        "version": 1,
    }

    _write_json(proj_dir / "project.json", project_data)
    _write_json(proj_dir / "timeline.json", {"tracks": [], "markers": [], "transitions": []})
    _write_json(proj_dir / "settings.json", {
        "theme": "dark",
        "language": "vi",
        "auto_backup": True,
        "gpu_mode": "auto",
    })
    (proj_dir / "backups").mkdir(exist_ok=True)

    with db_cursor() as cur:
        cur.execute("UPDATE projects SET path=? WHERE id=?", (str(proj_dir), pid))

    return project_data


def open_project(project_id: int) -> dict:
    """Load a project by ID from DB + file."""
    with db_cursor() as cur:
        row = cur.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise FileNotFoundError(f"Project {project_id} not found")
        row = dict(row)

    proj_file = Path(row["path"]) / "project.json" if row["path"] else None
    if proj_file and proj_file.exists():
        try:
            data = json.loads(proj_file.read_text(encoding="utf-8"))
            row.update(data)
        except (json.JSONDecodeError, OSError):
            pass

    return row


def save_project(project_id: int, data: dict = None) -> dict:
    """Save project data to disk + DB. Creates a version backup."""
    proj_dir = PROJECTS_DIR / f"project_{project_id}"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "backups").mkdir(exist_ok=True)

    proj_file = proj_dir / "project.json"

    # Load existing or create new
    if proj_file.exists():
        current = json.loads(proj_file.read_text(encoding="utf-8"))
    else:
        current = {"id": project_id, "version": 0}

    # Backup previous version
    if proj_file.exists():
        version = current.get("version", 0)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = proj_dir / "backups" / f"v{version}_{ts}.json"
        shutil.copy2(str(proj_file), str(backup))

    # Merge new data
    if data:
        current.update(data)
    current["version"] = current.get("version", 0) + 1
    current["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    proj_file.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")

    # Update DB
    with db_cursor() as cur:
        cur.execute(
            "UPDATE projects SET name=?, preset=?, resolution=?, fps=?, path=?, updated_at=? WHERE id=?",
            (
                current.get("name", ""),
                current.get("preset", "Movie Review"),
                current.get("resolution", "1920x1080"),
                current.get("fps", 30),
                str(proj_dir),
                current["updated_at"],
                project_id,
            ),
        )

    return current


def list_projects() -> list:
    """List all projects with summary info."""
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT id, name, preset, resolution, fps, created_at, updated_at FROM projects ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_project(project_id: int):
    """Delete a project and its directory."""
    with db_cursor() as cur:
        row = cur.execute("SELECT path FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise FileNotFoundError(f"Project {project_id} not found")

        track_rows = cur.execute("SELECT id FROM tracks WHERE project_id=?", (project_id,)).fetchall()
        track_ids = [r["id"] for r in track_rows]
        if track_ids:
            placeholders = ",".join("?" for _ in track_ids)
            cur.execute(f"DELETE FROM clips WHERE track_id IN ({placeholders})", track_ids)

        cur.execute("DELETE FROM transitions WHERE project_id=?", (project_id,))
        cur.execute("DELETE FROM markers WHERE project_id=?", (project_id,))
        cur.execute("DELETE FROM scenes WHERE project_id=?", (project_id,))
        cur.execute("DELETE FROM tracks WHERE project_id=?", (project_id,))
        cur.execute("DELETE FROM subtitles WHERE project_id=?", (project_id,))
        cur.execute("DELETE FROM queue_items WHERE project_id=?", (project_id,))
        cur.execute("DELETE FROM exports WHERE project_id=?", (project_id,))
        cur.execute("DELETE FROM projects WHERE id=?", (project_id,))

    if row and row["path"]:
        p = Path(row["path"])
        if p.exists():
            shutil.rmtree(str(p), ignore_errors=True)


def get_version_history(project_id: int) -> list:
    """List all backup versions of a project."""
    proj_dir = PROJECTS_DIR / f"project_{project_id}"
    backup_dir = proj_dir / "backups"
    if not backup_dir.exists():
        return []

    versions = []
    for f in sorted(backup_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            versions.append({
                "file": f.name,
                "version": data.get("version", 0),
                "saved_at": f.stat().st_mtime,
                "size": f.stat().st_size,
            })
        except (json.JSONDecodeError, OSError):
            pass

    return versions


def restore_version(project_id: int, version_file: str) -> dict:
    """Restore a project from a specific backup version."""
    proj_dir = PROJECTS_DIR / f"project_{project_id}"
    backup = proj_dir / "backups" / version_file
    if not backup.exists():
        raise FileNotFoundError(f"Backup {version_file} not found")

    data = json.loads(backup.read_text(encoding="utf-8"))
    return save_project(project_id, data)


def auto_backup(project_id: int):
    """Auto-backup triggered periodically."""
    try:
        proj_dir = PROJECTS_DIR / f"project_{project_id}"
        proj_file = proj_dir / "project.json"
        if proj_file.exists():
            (proj_dir / "backups").mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = proj_dir / "backups" / f"auto_{ts}.json"
            shutil.copy2(str(proj_file), str(backup))
    except Exception:
        pass


def export_project(project_id: int) -> str:
    """Export project as JSON string."""
    data = open_project(project_id)
    return json.dumps(data, indent=2, ensure_ascii=False)


def import_project(json_str: str) -> dict:
    """Import a project from JSON string."""
    data = json.loads(json_str)
    name = data.get("name", "Imported Project")
    preset = data.get("preset", "Movie Review")
    resolution = data.get("resolution", "1920x1080")

    project = create_project(name, preset, resolution, data.get("fps", 30))
    save_project(project["id"], data)
    return project


def save_settings(project_id: int, settings: dict) -> dict:
    """Save settings.json for a project."""
    proj_dir = PROJECTS_DIR / f"project_{project_id}"
    proj_dir.mkdir(parents=True, exist_ok=True)
    f = proj_dir / "settings.json"
    current = {}
    if f.exists():
        try:
            current = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    current.update(settings)
    _write_json(f, current)
    return current


def load_settings(project_id: int) -> dict:
    """Load settings.json for a project."""
    f = PROJECTS_DIR / f"project_{project_id}" / "settings.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"theme": "dark", "language": "vi", "auto_backup": True, "gpu_mode": "auto"}


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
