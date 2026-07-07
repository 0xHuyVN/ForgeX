from fastapi import APIRouter, HTTPException
from ..models.schemas import ProjectCreate, ProjectUpdate
from ..services.project_service import (
    create_project as svc_create_project,
    open_project as svc_open_project,
    save_project as svc_save_project,
    list_projects as svc_list_projects,
    delete_project as svc_delete_project,
    get_version_history,
    restore_version,
    save_settings,
    load_settings,
)

router = APIRouter()


@router.get("/")
def list_projects():
    return svc_list_projects()


@router.post("/")
def create_project(data: ProjectCreate):
    preset = data.project_preset or data.preset
    result = svc_create_project(data.name, preset, data.resolution, data.fps)
    return result


@router.get("/{project_id}")
def get_project(project_id: int):
    project = svc_open_project(project_id)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án")
    return project


@router.put("/{project_id}")
def update_project(project_id: int, data: ProjectUpdate):
    project = svc_open_project(project_id)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án")
    updates = data.model_dump(exclude_none=True)
    if updates:
        project.update(updates)
        svc_save_project(project_id, project)
    return {"message": "Đã cập nhật dự án"}


@router.delete("/{project_id}")
def delete_project(project_id: int):
    svc_delete_project(project_id)
    return {"message": "Đã xóa dự án"}


@router.post("/{project_id}/save")
def save_project(project_id: int):
    project = svc_open_project(project_id)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án")
    result = svc_save_project(project_id, project)
    return {"message": "Đã lưu dự án", "version": result.get("version")}


@router.get("/{project_id}/versions")
def versions(project_id: int):
    return get_version_history(project_id)


@router.post("/{project_id}/restore")
def restore(project_id: int, version_file: str):
    result = restore_version(project_id, version_file)
    return {"message": "Đã khôi phục phiên bản", "version": result.get("version")}


@router.get("/{project_id}/settings")
def get_settings(project_id: int):
    return load_settings(project_id)


@router.put("/{project_id}/settings")
def update_settings(project_id: int, settings: dict):
    result = save_settings(project_id, settings)
    return result
