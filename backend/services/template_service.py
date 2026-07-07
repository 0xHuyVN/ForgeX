import json
from ..database import db_cursor
from ..config import TEMPLATES_DIR, PROJECTS_DIR


def list_templates():
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    templates = []
    for f in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            templates.append({
                "name": data.get("name", f.stem),
                "file": f.name,
                "description": data.get("description", ""),
                "preset": data.get("preset", ""),
                "resolution": data.get("resolution", "1920x1080"),
                "fps": data.get("fps", 30),
                "size": f.stat().st_size,
            })
        except (json.JSONDecodeError, OSError):
            pass
    return templates


def get_template(name: str):
    f = TEMPLATES_DIR / f"{name.lower().replace(' ', '_')}.json"
    if not f.exists():
        for ff in TEMPLATES_DIR.glob("*.json"):
            try:
                data = json.loads(ff.read_text(encoding="utf-8"))
                if data.get("name", "") == name:
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_template(data: dict):
    name = data.get("name", "Untitled Template")
    key = name.lower().replace(" ", "_")
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    f = TEMPLATES_DIR / f"{key}.json"
    data.setdefault("tracks", [
        {"type": "video", "count": 1},
        {"type": "subtitle", "count": 1},
        {"type": "voice", "count": 1},
        {"type": "music", "count": 1},
    ])
    data.setdefault("voice", {"provider": "edge", "voice": "vi-VN-NamMinhNeural", "speed": 1.0})
    data.setdefault("subtitle", {"font": "Arial", "size": 42, "color": "#FFFFFF", "burn": True})
    data.setdefault("export", {"resolution": "1920x1080", "fps": 30, "codec": "h264", "bitrate": "8M"})
    data.setdefault("enhance", {"lut": "Cinematic", "brightness": 50, "contrast": 55, "saturation": 60})
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"message": f"Đã lưu Template '{name}'", "file": str(f)}


def delete_template(name: str):
    key = name.lower().replace(" ", "_")
    f = TEMPLATES_DIR / f"{key}.json"
    if f.exists():
        f.unlink()
        return {"message": f"Đã xóa Template '{name}'"}
    for ff in TEMPLATES_DIR.glob("*.json"):
        try:
            data = json.loads(ff.read_text(encoding="utf-8"))
            if data.get("name", "") == name:
                ff.unlink()
                return {"message": f"Đã xóa Template '{name}'"}
        except (json.JSONDecodeError, OSError):
            pass
    return {"message": f"Không tìm thấy Template '{name}'"}


def apply_template(project_id: int, template_name: str):
    template = get_template(template_name)
    if not template:
        raise ValueError(f"Template '{template_name}' not found")
    proj_dir = PROJECTS_DIR / f"project_{project_id}"
    proj_dir.mkdir(parents=True, exist_ok=True)
    proj_file = proj_dir / "project.json"
    current = {}
    if proj_file.exists():
        try:
            current = json.loads(proj_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    current.update({
        "preset": template.get("preset", current.get("preset", "Movie Review")),
        "resolution": template.get("resolution", current.get("resolution", "1920x1080")),
        "fps": template.get("fps", current.get("fps", 30)),
        "tracks": template.get("tracks", current.get("tracks", [])),
        "voice": template.get("voice"),
        "subtitle": template.get("subtitle"),
        "export": template.get("export"),
        "enhance": template.get("enhance"),
        "template": template_name,
    })
    proj_file.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    with db_cursor() as cur:
        cur.execute("UPDATE projects SET preset=?, resolution=?, fps=? WHERE id=?",
                    (current.get("preset", ""), current.get("resolution", "1920x1080"),
                     current.get("fps", 30), project_id))
    return {"message": f"Đã áp dụng Template '{template_name}' cho dự án {project_id}"}


def export_project_as_template(project_id: int, template_name: str):
    proj_dir = PROJECTS_DIR / f"project_{project_id}"
    proj_file = proj_dir / "project.json"
    if not proj_file.exists():
        raise FileNotFoundError(f"Project {project_id} file not found")
    data = json.loads(proj_file.read_text(encoding="utf-8"))
    data["name"] = template_name
    save_template(data)
    return {"message": f"Đã xuất dự án {project_id} thành template '{template_name}'"}
