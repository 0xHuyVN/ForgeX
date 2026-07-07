import json
import os
import subprocess
import time
from pathlib import Path

from ..config import EXPORTS_DIR, FFMPEG_PATH, FFPROBE_PATH, SUBTITLES_DIR
from ..database import db_cursor
from .auto_reframe_service import build_dynamic_crop_filter
from .ffmpeg_utils import _filter_path, drawtext_filter, get_video_info, run_ffmpeg
from .template_service import get_template


def libopenshot_status() -> dict:
    try:
        import openshot  # noqa: F401
        return {"available": True, "module": "openshot"}
    except Exception as openshot_error:
        try:
            import libopenshot  # noqa: F401
            return {"available": True, "module": "libopenshot"}
        except Exception as libopenshot_error:
            try:
                from .openshot_runner import is_openshot_available
                if is_openshot_available():
                    return {"available": True, "module": "native-hijack", "info": "Running via native OpenShot 3.5.1"}
            except Exception:
                pass
            return {
                "available": False,
                "module": None,
                "error": f"openshot={openshot_error}; libopenshot={libopenshot_error}",
            }


def _project_subtitle_path(project_id: int) -> str:
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT content FROM subtitles WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    if not row:
        return ""
    path = SUBTITLES_DIR / f"project_{project_id}_template_burn.srt"
    path.write_text(row["content"], encoding="utf-8")
    return str(path)


def _resolution(template: dict) -> tuple[int, int]:
    export = template.get("export") or {}
    res = str(export.get("resolution") or template.get("resolution") or "1080x1920").lower()
    if "x" in res:
        w, h = res.split("x", 1)
        return int(w), int(h)
    return 1080, 1920


def _latest_project_video(project_id: int) -> str:
    with db_cursor() as cur:
        row = cur.execute(
            """
            SELECT c.source_path AS path
            FROM clips c JOIN tracks t ON t.id = c.track_id
            WHERE t.project_id=? AND t.type='video' AND c.source_path IS NOT NULL AND c.source_path!=''
            ORDER BY c.id DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    return row["path"] if row and row["path"] else ""


def _latest_project_music(project_id: int) -> str:
    with db_cursor() as cur:
        row = cur.execute(
            """
            SELECT c.source_path AS path
            FROM clips c JOIN tracks t ON t.id = c.track_id
            WHERE t.project_id=? AND t.type IN ('music','audio') AND c.source_path IS NOT NULL AND c.source_path!=''
            ORDER BY c.id DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    return row["path"] if row and row["path"] else ""


def _has_audio(path: str) -> bool:
    try:
        result = subprocess.run(
            [FFPROBE_PATH, "-v", "quiet", "-print_format", "json", "-show_streams", path],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        streams = json.loads(result.stdout).get("streams", [])
        return any(s.get("codec_type") == "audio" for s in streams)
    except Exception:
        return False


def render_dynamic_template(project_id: int, input_path: str, template_name: str, output_path: str = "", overrides: dict = None) -> dict:
    template = get_template(template_name)
    if not template:
        raise ValueError(f"Template not found: {template_name}")
    input_path = input_path or _latest_project_video(project_id)
    if not input_path or not os.path.exists(input_path):
        raise FileNotFoundError(f"Input video not found: {input_path or '(empty)'}")

    overrides = overrides or {}
    export = {**(template.get("export") or {}), **(overrides.get("export") or {})}
    width, height = _resolution({**template, "export": export})
    fps = int(export.get("fps") or template.get("fps") or 30)
    output_path = output_path or str(EXPORTS_DIR / f"template_{project_id}_{template_name.lower().replace(' ', '_')}_{int(time.time())}.mp4")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    render = template.get("render") or {}
    auto_reframe = overrides.get("auto_reframe", render.get("auto_reframe", width < height))
    if auto_reframe:
        base_vf, reframe_meta = build_dynamic_crop_filter(input_path, out_w=width, out_h=height)
    else:
        base_vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
        reframe_meta = {}

    filters = [f"[0:v]{base_vf}[v0]"]
    current = "[v0]"

    frame = template.get("frame") or render.get("frame") or {}
    border_size = int(frame.get("border_size", render.get("border_size", 0)) or 0)
    border_color = frame.get("border_color", render.get("border_color", "white@0.9"))
    if border_size > 0:
        filters.append(f"{current}drawbox=x=0:y=0:w=iw:h=ih:color={border_color}:t={border_size}[v_frame]")
        current = "[v_frame]"

    intro = template.get("intro") or render.get("intro") or {}
    intro_text = str(intro.get("text") or "").strip()
    if intro_text:
        dur = float(intro.get("duration", 2.0))
        filters.append(f"{current}{drawtext_filter(intro_text, fontsize=int(intro.get('font_size', 64)), x='(w-tw)/2', y='h*0.18')}:enable='between(t,0,{dur})'[v_intro]")
        current = "[v_intro]"

    outro = template.get("outro") or render.get("outro") or {}
    outro_text = str(outro.get("text") or "").strip()
    duration = float(get_video_info(input_path).get("duration", 0) or 0)
    if outro_text and duration > 0:
        start = max(0.0, duration - float(outro.get("duration", 2.0)))
        filters.append(f"{current}{drawtext_filter(outro_text, fontsize=int(outro.get('font_size', 58)), x='(w-tw)/2', y='h*0.78')}:enable='gte(t,{start:.3f})'[v_outro]")
        current = "[v_outro]"

    subtitle_path = overrides.get("subtitle_path") or (_project_subtitle_path(project_id) if (template.get("subtitle") or {}).get("burn", True) else "")
    if subtitle_path and os.path.exists(subtitle_path):
        safe_sub = _filter_path(subtitle_path)
        filters.append(f"{current}subtitles=filename='{safe_sub}'[v_sub]")
        current = "[v_sub]"

    cmd = ["-i", input_path]
    music_path = overrides.get("music_path") or render.get("music_path") or _latest_project_music(project_id)
    has_music = bool(music_path and os.path.exists(music_path))
    has_input_audio = _has_audio(input_path)
    if has_music:
        cmd.extend(["-i", music_path])

    if has_music and has_input_audio:
        filters.append("[0:a]volume=0.85[a0];[1:a]volume=0.18[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[aout]")
    cmd.extend(["-filter_complex", ";".join(filters), "-map", current])
    if has_music and has_input_audio:
        cmd.extend(["-map", "[aout]", "-shortest"])
    elif has_music:
        cmd.extend(["-map", "1:a:0", "-shortest"])
    else:
        cmd.extend(["-map", "0:a?"])

    cmd.extend(["-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", str(export.get("crf", 20)), "-c:a", "aac", "-b:a", export.get("audio_bitrate", "160k"), "-movflags", "+faststart", output_path])

    if not run_ffmpeg(cmd):
        raise RuntimeError("Dynamic template render failed")

    if project_id:
        with db_cursor() as cur:
            cur.execute(
                "INSERT INTO exports (project_id, output_path, format, status) VALUES (?,?,?,?)",
                (project_id, output_path, "template", "completed"),
            )
    return {"output": output_path, "template": template_name, "engine": "ffmpeg", "libopenshot": libopenshot_status(), "reframe": reframe_meta}
