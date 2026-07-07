from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..services.package_manager import check_packages, install_package

router = APIRouter()


class InstallPackageRequest(BaseModel):
    package_id: str


@router.get("/check")
def packages_check():
    return check_packages()


@router.post("/install")
def packages_install(req: InstallPackageRequest):
    return install_package(req.package_id)
