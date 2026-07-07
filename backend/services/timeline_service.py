"""
Timeline Engine — manages multi-track timeline with clips, markers, transitions.
Stores tracks/clips/markers/transitions in database + timeline.json in project folder.
"""
import json
import time
import uuid
from ..database import db_cursor
from ..config import PROJECTS_DIR
from pathlib import Path


def _timeline_file(project_id: int) -> Path:
    return PROJECTS_DIR / f"project_{project_id}" / "timeline.json"


def _undo_dir(project_id: int) -> Path:
    return PROJECTS_DIR / f"project_{project_id}" / "undo"


def _timeline_settings_key(project_id: int) -> str:
    return f"timeline.view.{project_id}"


def _decode_json(value, default):
    if not value:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def save_timeline_to_file(project_id: int):
    """Write current timeline to project's timeline.json."""
    data = timeline_to_json(project_id)
    f = _timeline_file(project_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_timeline_from_file(project_id: int) -> dict:
    """Load timeline from project's timeline.json."""
    f = _timeline_file(project_id)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"tracks": [], "markers": [], "transitions": []}


def auto_save(func):
    """Decorator that saves timeline to file after any CRUD operation."""
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        if hasattr(result, "get") and "project_id" in result:
            save_timeline_to_file(result["project_id"])
        elif args:
            for arg in args:
                if isinstance(arg, int):
                    save_timeline_to_file(arg)
                    break
        return result
    return wrapper


# ─── Track CRUD ───

def create_track(project_id: int, track_type: str = "video", name: str = None, index: int = None) -> dict:
    with db_cursor() as cur:
        if index is None:
            cur.execute("SELECT COALESCE(MAX(track_index), -1) + 1 FROM tracks WHERE project_id=?", (project_id,))
            index = cur.fetchone()[0]
        if not name:
            name = f"{track_type.capitalize()} {index + 1}"
        cur.execute(
            "INSERT INTO tracks (project_id, type, name, track_index) VALUES (?,?,?,?)",
            (project_id, track_type, name, index),
        )
        result = {"id": cur.lastrowid, "project_id": project_id, "type": track_type, "name": name, "track_index": index}
    save_timeline_to_file(project_id)
    return result


def get_tracks(project_id: int) -> list:
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM tracks WHERE project_id=? ORDER BY track_index", (project_id,)
        ).fetchall()
        tracks = [dict(r) for r in rows]
        for t in tracks:
            t["clips"] = get_clips(t["id"])
        return tracks


def update_track(track_id: int, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [track_id]
    with db_cursor() as cur:
        row = cur.execute("SELECT project_id FROM tracks WHERE id=?", (track_id,)).fetchone()
        cur.execute(f"UPDATE tracks SET {sets} WHERE id=?", vals)
    if row:
        save_timeline_to_file(row["project_id"])


def delete_track(track_id: int):
    with db_cursor() as cur:
        cur.execute("SELECT project_id FROM tracks WHERE id=?", (track_id,))
        row = cur.fetchone()
        cur.execute("DELETE FROM tracks WHERE id=?", (track_id,))
        if row:
            save_timeline_to_file(row["project_id"])


# ─── Clip CRUD ───

def _project_id_for_track(track_id: int) -> int:
    with db_cursor() as cur:
        row = cur.execute("SELECT project_id FROM tracks WHERE id=?", (track_id,)).fetchone()
        return row["project_id"] if row else None


def create_clip(track_id: int, source_path: str = "", name: str = None, start_frame: int = 0,
                end_frame: int = 0, position_frame: int = 0, config: dict = None) -> dict:
    pid = _project_id_for_track(track_id)
    if pid is None:
        raise ValueError(f"Track {track_id} not found")

    with db_cursor() as cur:
        if not name and source_path:
            name = Path(source_path).stem
        cur.execute(
            """INSERT INTO clips (track_id, source_path, name, start_frame, end_frame, position_frame, config)
               VALUES (?,?,?,?,?,?,?)""",
            (track_id, source_path, name, start_frame, end_frame, position_frame,
             json.dumps(config or {})),
        )
        result = {"id": cur.lastrowid, "track_id": track_id, "name": name}
    save_timeline_to_file(pid)
    return result


def get_clips(track_id: int) -> list:
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM clips WHERE track_id=? ORDER BY position_frame", (track_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def update_clip(clip_id: int, **kwargs):
    if "config" in kwargs and isinstance(kwargs["config"], dict):
        kwargs["config"] = json.dumps(kwargs["config"])
    if "effects" in kwargs and isinstance(kwargs["effects"], list):
        kwargs["effects"] = json.dumps(kwargs["effects"])
    if not kwargs:
        return
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [clip_id]
    with db_cursor() as cur:
        row = cur.execute("SELECT track_id FROM clips WHERE id=?", (clip_id,)).fetchone()
        cur.execute(f"UPDATE clips SET {sets} WHERE id=?", vals)
    if row:
        pid = _project_id_for_track(row["track_id"])
        if pid:
            save_timeline_to_file(pid)


def delete_clip(clip_id: int):
    with db_cursor() as cur:
        cur.execute("SELECT track_id FROM clips WHERE id=?", (clip_id,))
        row = cur.fetchone()
        track_id = row["track_id"] if row else None
        cur.execute("DELETE FROM clips WHERE id=?", (clip_id,))
    if track_id:
        pid = _project_id_for_track(track_id)
        if pid:
            save_timeline_to_file(pid)


def move_clip(clip_id: int, new_track_id: int = None, new_position: int = None):
    updates = {}
    if new_track_id is not None:
        updates["track_id"] = new_track_id
    if new_position is not None:
        updates["position_frame"] = new_position
    if updates:
        update_clip(clip_id, **updates)


# Smart timeline operations

def get_timeline_view(project_id: int) -> dict:
    default = {
        "zoom": 1.0,
        "snap": True,
        "snap_threshold_frames": 6,
        "ripple": False,
    }
    with db_cursor() as cur:
        row = cur.execute("SELECT value FROM settings WHERE key=?", (_timeline_settings_key(project_id),)).fetchone()
    data = _decode_json(row["value"], {}) if row else {}
    default.update({k: data[k] for k in data if k in default})
    return default


def set_timeline_view(project_id: int, **kwargs) -> dict:
    view = get_timeline_view(project_id)
    if "zoom" in kwargs and kwargs["zoom"] is not None:
        view["zoom"] = max(0.05, min(64.0, float(kwargs["zoom"])))
    if "snap" in kwargs and kwargs["snap"] is not None:
        view["snap"] = bool(kwargs["snap"])
    if "snap_threshold_frames" in kwargs and kwargs["snap_threshold_frames"] is not None:
        view["snap_threshold_frames"] = max(0, min(120, int(kwargs["snap_threshold_frames"])))
    if "ripple" in kwargs and kwargs["ripple"] is not None:
        view["ripple"] = bool(kwargs["ripple"])
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now','localtime')",
            (_timeline_settings_key(project_id), json.dumps(view)),
        )
    return view


def create_bookmark(project_id: int, frame: int, label: str = "", color: str = "#2dd4bf") -> dict:
    return create_marker(project_id, frame, label, color, marker_type="bookmark")


def set_track_state(track_id: int, locked: int = None, hidden: int = None, muted: int = None) -> dict:
    updates = {}
    if locked is not None:
        updates["locked"] = 1 if bool(locked) else 0
    if hidden is not None:
        updates["hidden"] = 1 if bool(hidden) else 0
    if muted is not None:
        updates["muted"] = 1 if bool(muted) else 0
    update_track(track_id, **updates)
    with db_cursor() as cur:
        row = cur.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
    return dict(row) if row else {}


def _clip_row(clip_id: int):
    with db_cursor() as cur:
        return cur.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()


def _ensure_track_unlocked(track_id: int):
    with db_cursor() as cur:
        row = cur.execute("SELECT locked FROM tracks WHERE id=?", (track_id,)).fetchone()
    if row and int(row["locked"] or 0):
        raise ValueError("Track is locked")


def group_clips(clip_ids: list[int], group_id: str = None) -> dict:
    group_id = group_id or uuid.uuid4().hex
    updated = []
    for clip_id in clip_ids:
        row = _clip_row(clip_id)
        if not row:
            continue
        _ensure_track_unlocked(row["track_id"])
        config = _decode_json(row["config"], {})
        config["group_id"] = group_id
        update_clip(clip_id, config=config)
        updated.append(clip_id)
    return {"group_id": group_id, "clip_ids": updated}


def ungroup_clips(clip_ids: list[int]) -> dict:
    updated = []
    for clip_id in clip_ids:
        row = _clip_row(clip_id)
        if not row:
            continue
        _ensure_track_unlocked(row["track_id"])
        config = _decode_json(row["config"], {})
        config.pop("group_id", None)
        update_clip(clip_id, config=config)
        updated.append(clip_id)
    return {"clip_ids": updated}


def snapshot_undo(project_id: int, label: str = "") -> dict:
    data = timeline_to_json(project_id)
    undo_dir = _undo_dir(project_id)
    undo_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time() * 1000)
    safe_label = "".join(ch for ch in (label or "timeline") if ch.isalnum() or ch in "._- ")[:40].strip() or "timeline"
    path = undo_dir / f"{stamp}_{safe_label}.json"
    path.write_text(json.dumps({"label": label, "timeline": data}, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"path": str(path), "label": label, "created_at_ms": stamp}


def list_undo_history(project_id: int, limit: int = 50) -> list[dict]:
    undo_dir = _undo_dir(project_id)
    if not undo_dir.exists():
        return []
    items = []
    for path in sorted(undo_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        label = path.stem.split("_", 1)[1] if "_" in path.stem else path.stem
        items.append({"path": str(path), "label": label, "mtime": path.stat().st_mtime})
    return items


def undo_last(project_id: int) -> dict:
    history = list_undo_history(project_id, limit=1)
    if not history:
        raise ValueError("No undo snapshot")
    path = Path(history[0]["path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    timeline_from_json(project_id, payload.get("timeline", {}))
    path.unlink(missing_ok=True)
    return {"restored": True, "snapshot": history[0]}


def snap_frame(project_id: int, frame: int, threshold_frames: int = None) -> dict:
    view = get_timeline_view(project_id)
    threshold = int(threshold_frames if threshold_frames is not None else view.get("snap_threshold_frames", 6))
    candidates = []
    with db_cursor() as cur:
        for row in cur.execute("SELECT id, frame, label, type FROM markers WHERE project_id=?", (project_id,)).fetchall():
            candidates.append({"kind": row["type"] or "marker", "id": row["id"], "frame": row["frame"], "label": row["label"]})
        rows = cur.execute(
            "SELECT c.id, c.position_frame, c.start_frame, c.end_frame, c.name "
            "FROM clips c JOIN tracks t ON t.id=c.track_id WHERE t.project_id=?",
            (project_id,),
        ).fetchall()
        for row in rows:
            start = int(row["position_frame"] or 0)
            end = start + max(0, int(row["end_frame"] or 0) - int(row["start_frame"] or 0))
            candidates.append({"kind": "clip_start", "id": row["id"], "frame": start, "label": row["name"]})
            candidates.append({"kind": "clip_end", "id": row["id"], "frame": end, "label": row["name"]})
    best = min(candidates, key=lambda c: abs(int(c["frame"]) - int(frame)), default=None)
    if best and abs(int(best["frame"]) - int(frame)) <= threshold:
        return {"snapped": True, "frame": int(best["frame"]), "target": best}
    return {"snapped": False, "frame": int(frame), "target": None}


def ripple_move_clip(clip_id: int, delta_frames: int, snap: bool = True, threshold_frames: int = None) -> dict:
    row = _clip_row(clip_id)
    if not row:
        raise ValueError("Clip not found")
    _ensure_track_unlocked(row["track_id"])
    project_id = _project_id_for_track(row["track_id"])
    old_pos = int(row["position_frame"] or 0)
    new_pos = old_pos + int(delta_frames)
    if snap and project_id:
        new_pos = snap_frame(project_id, new_pos, threshold_frames).get("frame", new_pos)
    actual_delta = new_pos - old_pos
    updated = []
    with db_cursor() as cur:
        cur.execute("UPDATE clips SET position_frame=? WHERE id=?", (new_pos, clip_id))
        updated.append({"id": clip_id, "position_frame": new_pos})
        following = cur.execute(
            "SELECT id, position_frame FROM clips "
            "WHERE track_id=? AND id<>? AND position_frame>? ORDER BY position_frame",
            (row["track_id"], clip_id, old_pos),
        ).fetchall()
        for item in following:
            pos = int(item["position_frame"] or 0) + actual_delta
            cur.execute("UPDATE clips SET position_frame=? WHERE id=?", (pos, item["id"]))
            updated.append({"id": item["id"], "position_frame": pos})
    if project_id:
        save_timeline_to_file(project_id)
    return {"clip_id": clip_id, "delta_frames": actual_delta, "updated": updated}


# ─── Marker CRUD ───

def create_marker(project_id: int, frame: int, label: str = "", color: str = "#f8b400", marker_type: str = "note") -> dict:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO markers (project_id, frame, label, color, type) VALUES (?,?,?,?,?)",
            (project_id, frame, label, color, marker_type),
        )
        result = {"id": cur.lastrowid, "frame": frame, "label": label, "project_id": project_id}
    save_timeline_to_file(project_id)
    return result


def get_markers(project_id: int) -> list:
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM markers WHERE project_id=? ORDER BY frame", (project_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_marker(marker_id: int):
    with db_cursor() as cur:
        cur.execute("SELECT project_id FROM markers WHERE id=?", (marker_id,))
        row = cur.fetchone()
        cur.execute("DELETE FROM markers WHERE id=?", (marker_id,))
        if row:
            save_timeline_to_file(row["project_id"])


# ─── Transition CRUD ───

def create_transition(project_id: int, clip_a_id: int, clip_b_id: int,
                      trans_type: str = "crossfade", duration_frames: int = 15) -> dict:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO transitions (project_id, clip_a_id, clip_b_id, type, duration_frames) VALUES (?,?,?,?,?)",
            (project_id, clip_a_id, clip_b_id, trans_type, duration_frames),
        )
        result = {"id": cur.lastrowid, "type": trans_type, "project_id": project_id}
    save_timeline_to_file(project_id)
    return result


def get_transitions(project_id: int) -> list:
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM transitions WHERE project_id=? ORDER BY id", (project_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ─── Timeline serialization ───

def timeline_to_json(project_id: int) -> dict:
    """Export a project's entire timeline to a JSON dict."""
    return {
        "tracks": get_tracks(project_id),
        "markers": get_markers(project_id),
        "transitions": get_transitions(project_id),
    }


def timeline_from_json(project_id: int, data: dict):
    """Import a JSON dict into the project's timeline, replacing existing."""
    with db_cursor() as cur:
        cur.execute("DELETE FROM tracks WHERE project_id=?", (project_id,))
        cur.execute("DELETE FROM markers WHERE project_id=?", (project_id,))
        cur.execute("DELETE FROM transitions WHERE project_id=?", (project_id,))

    for track_data in data.get("tracks", []):
        clips = track_data.pop("clips", [])
        t = create_track(
            project_id,
            track_type=track_data.get("type", "video"),
            name=track_data.get("name"),
            index=track_data.get("track_index"),
        )
        for clip_data in clips:
            create_clip(
                t["id"],
                source_path=clip_data.get("source_path", ""),
                name=clip_data.get("name"),
                start_frame=clip_data.get("start_frame", 0),
                end_frame=clip_data.get("end_frame", 0),
                position_frame=clip_data.get("position_frame", 0),
            )

    for marker_data in data.get("markers", []):
        create_marker(project_id, marker_data.get("frame", 0), marker_data.get("label", ""))

    for trans_data in data.get("transitions", []):
        create_transition(project_id, trans_data.get("clip_a_id", 0), trans_data.get("clip_b_id", 0),
                          trans_data.get("type", "crossfade"), trans_data.get("duration_frames", 15))

    save_timeline_to_file(project_id)
    try:
        from .event_bus import event_bus
        event_bus.publish("timeline_updated", {"project_id": project_id})
    except Exception:
        pass


# ─── Timeline to FFmpeg filter graph ───
def timeline_to_ffmpeg(project_id: int) -> list:
    """Generate FFmpeg concat/trim commands from timeline clips."""
    tracks = get_tracks(project_id)
    commands = []
    for track in tracks:
        for clip in track.get("clips", []):
            src = clip.get("source_path", "")
            if not src:
                continue
            start = clip.get("start_frame", 0)
            end = clip.get("end_frame", 0)
            cmd = {"input": src}
            if end > start:
                cmd["trim"] = {"start": start, "end": end}
            commands.append(cmd)
    return commands


def sync_timeline_subtitle(project_id: int, srt_content: str):
    """Parse SRT content and populate subtitle clips in the timeline."""
    import re
    
    # Simple SRT parser
    pattern = re.compile(
        r"(\d+)\s*\n(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\n(.*?)(?=\n\d+\s*\n|\Z)",
        re.DOTALL
    )
    
    def time_to_frames(t_str):
        parts = re.split(r"[:,.]", t_str.replace(",", "."))
        if len(parts) >= 4:
            h, m, s, ms = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
            seconds = h * 3600 + m * 60 + s + ms / 1000.0
            return int(seconds * 30) # 30 fps
        return 0

    blocks = []
    for match in pattern.finditer(srt_content):
        start_f = time_to_frames(match.group(2))
        end_f = time_to_frames(match.group(3))
        text = match.group(4).strip().replace("\n", " ")
        blocks.append({
            "name": text[:30] + ("..." if len(text) > 30 else ""),
            "start_frame": start_f,
            "end_frame": end_f,
            "position_frame": start_f
        })
        
    with db_cursor() as cur:
        # Find or create subtitle track
        track = cur.execute(
            "SELECT id FROM tracks WHERE project_id=? AND type='subtitle' LIMIT 1",
            (project_id,)
        ).fetchone()
        if track:
            track_id = track["id"]
            # Clear old subtitle clips
            cur.execute("DELETE FROM clips WHERE track_id=?", (track_id,))
        else:
            t = create_track(project_id, "subtitle", "Subtitle", index=1)
            track_id = t["id"]
            
        # Insert clips
        for b in blocks:
            cur.execute(
                """INSERT INTO clips (track_id, source_path, name, start_frame, end_frame, position_frame)
                   VALUES (?,?,?,?,?,?)""",
                (track_id, "", b["name"], b["start_frame"], b["end_frame"], b["position_frame"])
            )
            
    save_timeline_to_file(project_id)


def sync_timeline_audio_track(project_id: int, file_path: str, track_type: str, track_label: str, index: int):
    """Generic helper to sync a music or voice audio file to a track on the timeline."""
    import subprocess
    import os
    import json
    from ..config import FFPROBE_PATH
    
    if not file_path or not os.path.exists(file_path):
        return
        
    cmd = [
        FFPROBE_PATH, "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        file_path
    ]
    duration_secs = 30.0
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)
        info = json.loads(res.stdout)
        duration_secs = float(info.get("format", {}).get("duration", 30.0))
    except Exception as e:
        print(f"Error probing audio duration: {e}")
        
    duration_frames = int(duration_secs * 30)
    
    with db_cursor() as cur:
        track = cur.execute(
            "SELECT id FROM tracks WHERE project_id=? AND type=? LIMIT 1",
            (project_id, track_type)
        ).fetchone()
        if track:
            track_id = track["id"]
            cur.execute("DELETE FROM clips WHERE track_id=?", (track_id,))
        else:
            t = create_track(project_id, track_type, track_label, index=index)
            track_id = t["id"]
            
        filename = os.path.basename(file_path)
        cur.execute(
            """INSERT INTO clips (track_id, source_path, name, start_frame, end_frame, position_frame)
               VALUES (?,?,?,?,?,?)""",
            (track_id, file_path, filename, 0, duration_frames, 0)
        )
        
    save_timeline_to_file(project_id)


def sync_timeline_music(project_id: int, music_path: str):
    sync_timeline_audio_track(project_id, music_path, "music", "Audio 1", 3)


def sync_timeline_voice(project_id: int, voice_path: str):
    sync_timeline_audio_track(project_id, voice_path, "voice", "Voice", 2)

