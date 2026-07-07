"""
Timeline API — /api/timeline/*
Manage multi-track timelines: tracks, clips, markers, transitions.
"""
from fastapi import APIRouter, HTTPException
from ..services.timeline_service import (
    create_track, get_tracks, update_track, delete_track,
    create_clip, get_clips, update_clip, delete_clip, move_clip,
    create_marker, get_markers, delete_marker,
    create_transition, get_transitions,
    timeline_to_json, timeline_from_json, timeline_to_ffmpeg,
    get_timeline_view, set_timeline_view, create_bookmark, set_track_state,
    group_clips, ungroup_clips, snapshot_undo, list_undo_history, undo_last,
    snap_frame, ripple_move_clip,
)
from ..services.path_guard import http_safe_media_input
from ..database import db_cursor

router = APIRouter()


# ─── Timeline export/import ───

@router.get("/{project_id}")
def get_timeline(project_id: int):
    with db_cursor() as cur:
        row = cur.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Không tìm thấy dự án")
    return timeline_to_json(project_id)


@router.put("/{project_id}")
def set_timeline(project_id: int, data: dict):
    timeline_from_json(project_id, data)
    return {"message": "Đã cập nhật dòng thời gian"}


@router.get("/{project_id}/ffmpeg")
def get_ffmpeg_commands(project_id: int):
    return {"commands": timeline_to_ffmpeg(project_id)}


@router.get("/{project_id}/view")
def read_timeline_view(project_id: int):
    return get_timeline_view(project_id)


@router.put("/{project_id}/view")
def write_timeline_view(project_id: int, data: dict):
    return set_timeline_view(project_id, **data)


@router.post("/{project_id}/undo/snapshot")
def create_undo_snapshot(project_id: int, data: dict = None):
    return snapshot_undo(project_id, (data or {}).get("label", "manual"))


@router.get("/{project_id}/undo")
def read_undo_history(project_id: int, limit: int = 50):
    return {"items": list_undo_history(project_id, limit)}


@router.post("/{project_id}/undo")
def restore_last_undo(project_id: int):
    try:
        return undo_last(project_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/{project_id}/snap")
def snap_frame_endpoint(project_id: int, frame: int, threshold_frames: int = None):
    return snap_frame(project_id, frame, threshold_frames)


# ─── Tracks ───

@router.post("/{project_id}/tracks")
def add_track(project_id: int, track_type: str = "video", name: str = None):
    t = create_track(project_id, track_type, name)
    return t


@router.get("/{project_id}/tracks")
def list_tracks(project_id: int):
    return get_tracks(project_id)


@router.put("/tracks/{track_id}")
def edit_track(track_id: int, name: str = None, muted: int = None, locked: int = None, hidden: int = None):
    kwargs = {}
    if name is not None: kwargs["name"] = name
    if muted is not None: kwargs["muted"] = muted
    if locked is not None: kwargs["locked"] = locked
    if hidden is not None: kwargs["hidden"] = hidden
    if kwargs:
        update_track(track_id, **kwargs)
    return {"message": "Đã cập nhật track"}


@router.delete("/tracks/{track_id}")
def remove_track(track_id: int):
    delete_track(track_id)
    return {"message": "Đã xóa track"}


# ─── Clips ───

@router.put("/tracks/{track_id}/state")
def edit_track_state(track_id: int, data: dict):
    return set_track_state(
        track_id,
        locked=data.get("locked"),
        hidden=data.get("hidden"),
        muted=data.get("muted"),
    )


@router.post("/tracks/{track_id}/clips")
def add_clip(track_id: int, source_path: str = "", name: str = None,
             start_frame: int = 0, end_frame: int = 0, position_frame: int = 0):
    try:
        c = create_clip(track_id, source_path, name, start_frame, end_frame, position_frame)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return c


@router.get("/tracks/{track_id}/clips")
def list_clips(track_id: int):
    return get_clips(track_id)


@router.put("/clips/{clip_id}")
def edit_clip(clip_id: int, source_path: str = None, name: str = None,
              start_frame: int = None, end_frame: int = None,
              position_frame: int = None, speed: float = None,
              volume: float = None, opacity: float = None):
    kwargs = {}
    if source_path is not None: kwargs["source_path"] = source_path
    if name is not None: kwargs["name"] = name
    if start_frame is not None: kwargs["start_frame"] = start_frame
    if end_frame is not None: kwargs["end_frame"] = end_frame
    if position_frame is not None: kwargs["position_frame"] = position_frame
    if speed is not None: kwargs["speed"] = speed
    if volume is not None: kwargs["volume"] = volume
    if opacity is not None: kwargs["opacity"] = opacity
    if kwargs:
        update_clip(clip_id, **kwargs)
    return {"message": "Đã cập nhật clip"}


@router.delete("/clips/{clip_id}")
def remove_clip(clip_id: int):
    delete_clip(clip_id)
    return {"message": "Đã xóa clip"}


@router.put("/clips/{clip_id}/move")
def move_clip_endpoint(clip_id: int, track_id: int = None, position: int = None):
    move_clip(clip_id, track_id, position)
    return {"message": "Đã di chuyển clip"}


# ─── Markers ───

@router.put("/clips/{clip_id}/ripple")
def ripple_move_clip_endpoint(clip_id: int, data: dict):
    try:
        return ripple_move_clip(
            clip_id,
            int(data.get("delta_frames", 0)),
            bool(data.get("snap", True)),
            data.get("threshold_frames"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/clips/group")
def group_clips_endpoint(data: dict):
    clip_ids = [int(x) for x in data.get("clip_ids", [])]
    return group_clips(clip_ids, data.get("group_id"))


@router.post("/clips/ungroup")
def ungroup_clips_endpoint(data: dict):
    clip_ids = [int(x) for x in data.get("clip_ids", [])]
    return ungroup_clips(clip_ids)


@router.post("/{project_id}/markers")
def add_marker(project_id: int, frame: int, label: str = "", color: str = "#f8b400"):
    m = create_marker(project_id, frame, label, color)
    return m


@router.get("/{project_id}/markers")
def list_markers(project_id: int):
    return get_markers(project_id)


@router.post("/{project_id}/bookmarks")
def add_bookmark(project_id: int, data: dict):
    return create_bookmark(
        project_id,
        int(data.get("frame", 0)),
        data.get("label", ""),
        data.get("color", "#2dd4bf"),
    )


@router.delete("/markers/{marker_id}")
def remove_marker(marker_id: int):
    delete_marker(marker_id)
    return {"message": "Đã xóa đánh dấu"}


# ─── Transitions ───

@router.post("/{project_id}/transitions")
def add_transition(project_id: int, clip_a_id: int, clip_b_id: int,
                   trans_type: str = "crossfade", duration_frames: int = 15):
    t = create_transition(project_id, clip_a_id, clip_b_id, trans_type, duration_frames)
    return t


@router.get("/{project_id}/transitions")
def list_transitions(project_id: int):
    return get_transitions(project_id)


# ─── Video & Music Timeline Sync ───

@router.post("/{project_id}/video")
def set_timeline_video(project_id: int, data: dict):
    import subprocess
    import json
    import os
    from ..config import FFPROBE_PATH
    from ..services.timeline_service import create_track
    from ..database import db_cursor
    
    video_path = data.get("path", "")
    if not video_path or not os.path.exists(video_path):
        raise HTTPException(400, "Đường dẫn video không hợp lệ")
    video_path = str(http_safe_media_input(video_path, field="video path"))

    # Validate that the path is actually a readable media file with a video stream.
    cmd = [
        FFPROBE_PATH, "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        video_path
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        if res.returncode != 0:
            raise ValueError(res.stderr.strip() or "ffprobe failed")
        info = json.loads(res.stdout)
        streams = info.get("streams", [])
        if not any(s.get("codec_type") == "video" for s in streams):
            raise ValueError("no video stream")
        duration_secs = float(info.get("format", {}).get("duration") or 0)
        if duration_secs <= 0:
            raise ValueError("missing video duration")
    except Exception as e:
        raise HTTPException(400, f"Video khong hop le hoac khong doc duoc: {e}")
        
    duration_frames = int(duration_secs * 30) # 30 fps
    
    with db_cursor() as cur:
        # Find or create video track
        track = cur.execute(
            "SELECT id FROM tracks WHERE project_id=? AND type='video' LIMIT 1",
            (project_id,)
        ).fetchone()
        if track:
            track_id = track["id"]
            # Clear old video clips
            cur.execute("DELETE FROM clips WHERE track_id=?", (track_id,))
        else:
            t = create_track(project_id, "video", "Video 1", index=0)
            track_id = t["id"]
            
        # Insert video clip
        filename = os.path.basename(video_path)
        cur.execute(
            """INSERT INTO clips (track_id, source_path, name, start_frame, end_frame, position_frame)
               VALUES (?,?,?,?,?,?)""",
            (track_id, video_path, filename, 0, duration_frames, 0)
        )
        
    return {"message": "Đã đồng bộ video vào timeline", "duration_frames": duration_frames}


@router.post("/{project_id}/music")
def set_timeline_music(project_id: int, data: dict):
    import subprocess
    import json
    import os
    from ..config import FFPROBE_PATH
    from ..services.timeline_service import create_track
    from ..database import db_cursor
    
    music_path = data.get("path", "")
    if not music_path or not os.path.exists(music_path):
        raise HTTPException(400, "Đường dẫn nhạc không hợp lệ")
    music_path = str(http_safe_media_input(music_path, field="music path", extensions={".mp3", ".wav", ".m4a", ".flac", ".ogg"}))

    # Validate that the path is actually a readable media file with an audio stream.
    cmd = [
        FFPROBE_PATH, "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        music_path
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        if res.returncode != 0:
            raise ValueError(res.stderr.strip() or "ffprobe failed")
        info = json.loads(res.stdout)
        streams = info.get("streams", [])
        if not any(s.get("codec_type") == "audio" for s in streams):
            raise ValueError("no audio stream")
        duration_secs = float(info.get("format", {}).get("duration") or 0)
        if duration_secs <= 0:
            raise ValueError("missing audio duration")
    except Exception as e:
        raise HTTPException(400, f"Nhac khong hop le hoac khong doc duoc: {e}")
        
    duration_frames = int(duration_secs * 30) # 30 fps
    
    with db_cursor() as cur:
        # Find or create music track
        track = cur.execute(
            "SELECT id FROM tracks WHERE project_id=? AND type='music' LIMIT 1",
            (project_id,)
        ).fetchone()
        if track:
            track_id = track["id"]
            cur.execute("DELETE FROM clips WHERE track_id=?", (track_id,))
        else:
            t = create_track(project_id, "music", "Audio 1", index=2)
            track_id = t["id"]
            
        filename = os.path.basename(music_path)
        cur.execute(
            """INSERT INTO clips (track_id, source_path, name, start_frame, end_frame, position_frame)
               VALUES (?,?,?,?,?,?)""",
            (track_id, music_path, filename, 0, duration_frames, 0)
        )
        
    return {"message": "Đã đồng bộ nhạc vào timeline", "duration_frames": duration_frames}

