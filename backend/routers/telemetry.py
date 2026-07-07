from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..services.telemetry_service import is_enabled, recent_events, record_event, set_enabled, system_snapshot

router = APIRouter()


class TelemetrySettingsRequest(BaseModel):
    enabled: bool


class TelemetryEventRequest(BaseModel):
    event_type: str
    payload: dict = {}


@router.get("/settings")
def get_telemetry_settings():
    return {"enabled": is_enabled()}


@router.put("/settings")
def update_telemetry_settings(req: TelemetrySettingsRequest):
    return set_enabled(req.enabled)


@router.post("/events")
def create_telemetry_event(req: TelemetryEventRequest):
    return record_event(req.event_type, req.payload)


@router.get("/events")
def list_telemetry_events(limit: int = 100):
    return recent_events(limit)


@router.get("/system")
def telemetry_system_snapshot():
    return system_snapshot()
