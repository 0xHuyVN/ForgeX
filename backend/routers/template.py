from fastapi import APIRouter, HTTPException
from ..services.queue_manager import add_queue_item
from ..services.template_service import (
    list_templates, get_template, save_template, delete_template,
    apply_template, export_project_as_template,
)
from ..services.dynamic_template_service import libopenshot_status, render_dynamic_template
from ..services.path_guard import http_safe_media_input, http_safe_output_path

router = APIRouter()


@router.get("/")
def list_all():
    return list_templates()


@router.get("/engine/status")
def engine_status():
    return {"libopenshot": libopenshot_status(), "renderer": "ffmpeg"}


@router.get("/{name}")
def get_one(name: str):
    t = get_template(name)
    if not t:
        raise HTTPException(404, f"Không tìm thấy template '{name}'")
    return t


@router.post("/")
def save(name: str = "", config: dict = None):
    if config is None:
        config = {}
    if name:
        config["name"] = name
    if "name" not in config:
        raise HTTPException(400, "Yêu cầu cung cấp tên template")
    result = save_template(config)
    return result


@router.delete("/{name}")
def delete(name: str):
    return delete_template(name)


@router.post("/{name}/apply")
def apply(name: str, project_id: int):
    try:
        result = apply_template(project_id, name)
        return result
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/{name}/render")
def render_template(name: str, data: dict):
    project_id = int(data.get("project_id", 0) or 0)
    input_path = data.get("input_path", "")
    if input_path:
        input_path = str(http_safe_media_input(input_path, field="template input"))
    output_path = data.get("output_path", "")
    if output_path:
        output_path = str(http_safe_output_path(output_path, field="template output", extensions={".mp4", ".mov", ".mkv"}))
    queue = data.get("queue", True)
    params = {
        "template_name": name,
        "input_path": input_path,
        "output_path": output_path,
        "overrides": data.get("overrides") or {},
        "publish": data.get("publish") or {},
    }
    if queue:
        item_id = add_queue_item(project_id, "dynamic_template", input_path, params, priority=1)
        return {"id": item_id, "message": f"Da dua render template '{name}' vao hang doi"}
    try:
        return render_dynamic_template(project_id, input_path, name, output_path, params["overrides"])
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/{name}/from-project/{project_id}")
def from_project(project_id: int, name: str):
    try:
        result = export_project_as_template(project_id, name)
        return result
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
