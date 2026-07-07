from fastapi import APIRouter, HTTPException, Query
from ..services.preset_service import (
    list_presets,
    get_preset,
    save_preset as svc_save_preset,
    delete_preset as svc_delete_preset,
    export_preset,
    import_preset,
)

router = APIRouter()


@router.get("/")
def list_all_presets():
    return list_presets()


@router.get("/{name}")
def get_one_preset(name: str):
    preset = get_preset(name)
    if not preset:
        raise HTTPException(404, f"Không tìm thấy Preset '{name}'")
    return preset


@router.post("/")
def save_preset(name: str = Query(...), config: dict = None):
    if config is None:
        config = {}
    svc_save_preset(name, config)
    return {"message": f"Đã lưu Preset '{name}'"}


@router.delete("/{name}")
def delete_preset(name: str):
    svc_delete_preset(name)
    return {"message": f"Đã xóa Preset '{name}'"}


@router.get("/{name}/export")
def export_one_preset(name: str):
    json_str = export_preset(name)
    return {"preset": json_str}


@router.post("/import")
def import_preset_endpoint(json_str: str):
    try:
        name = import_preset(json_str)
        return {"message": f"Đã nhập Preset '{name}'"}
    except ValueError as e:
        raise HTTPException(400, str(e))
