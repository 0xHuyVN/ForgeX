import subprocess
import json
import os
import re
import shutil
import tempfile
import time
from fractions import Fraction
from pathlib import Path
from ..config import FFMPEG_PATH, FFPROBE_PATH, EXPORTS_DIR, CACHE_DIR, VOICES_DIR, SUBTITLES_DIR


_has_nvenc_cache = None


def _startupinfo():
    if not hasattr(subprocess, "STARTUPINFO"):
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return info


def _creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _as_bool(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default


def _parse_fps(value: str) -> float:
    try:
        return float(Fraction(value))
    except Exception:
        try:
            return float(value)
        except Exception:
            return 0.0


def _normalize_bitrate(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"auto", "tu dong", "tự động", "automatic"}:
        return None
    return text


def _volume01(value, default=1.0):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _positive_int(value, default=0) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _crop_position_expr(position) -> tuple[str, str]:
    text = str(position or "center").strip().lower().replace("_", "-")
    if not text or text == "........":
        text = "center"
    if "," in text:
        parts = [p.strip() for p in text.split(",", 1)]
        try:
            px = max(0.0, min(1.0, float(parts[0])))
            py = max(0.0, min(1.0, float(parts[1])))
            return (f"(iw-ow)*{px:.4f}", f"(ih-oh)*{py:.4f}")
        except (TypeError, ValueError):
            pass
    x = "0" if "left" in text else "iw-ow" if "right" in text else "(iw-ow)/2"
    y = "0" if "top" in text else "ih-oh" if "bottom" in text else "(ih-oh)/2"
    return x, y


def _drawtext_escape(text: str) -> str:
    return str(text or "").replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("\n", " ")


def _drawtext_fontfile() -> str:
    candidates = [
        os.environ.get("DRAWTEXT_FONT"),
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return _filter_path(candidate)
    return ""


def drawtext_filter(text: str, *, fontcolor: str = "white@0.8", fontsize: int = 48, x: str = "0", y: str = "0") -> str:
    parts = [f"text='{_drawtext_escape(text)}'"]
    fontfile = _drawtext_fontfile()
    if fontfile:
        parts.append(f"fontfile='{fontfile}'")
    parts.extend([f"fontcolor={fontcolor}", f"fontsize={fontsize}", f"x={x}", f"y={y}"])
    return "drawtext=" + ":".join(parts)


def _codec_name(params: dict) -> str:
    codec = str(params.get("codec", "h264")).strip().lower()
    return {"h265": "h265", "hevc": "h265", "h264": "h264", "av1": "av1"}.get(codec, "h264")


def _selected_video_codec(params: dict) -> str:
    codec = _codec_name(params)
    gpu = str(params.get("gpu", "auto")).strip().lower()
    use_nvenc = gpu in {"auto", "gpu", "nvenc", "nvidia"} and has_nvenc()
    if gpu in {"cpu", "none", "off"}:
        use_nvenc = False
    if use_nvenc and codec == "h264":
        return "h264_nvenc"
    if use_nvenc and codec == "h265":
        return "hevc_nvenc"
    return {"h264": "libx264", "h265": "libx265", "av1": "libaom-av1"}.get(codec, "libx264")


def _append_video_encoding_args(cmd: list, params: dict):
    chosen_codec = _selected_video_codec(params)
    cmd.extend(["-c:v", chosen_codec])

    bitrate = _normalize_bitrate(params.get("bitrate"))
    quality = str(params.get("quality", "")).lower()
    preset = str(params.get("preset") or ("veryfast" if quality == "draft" else "medium")).lower()
    crf = str(params.get("crf") or ("24" if quality == "draft" else "18"))

    if "nvenc" in chosen_codec:
        preset_map = {
            "ultrafast": "p1",
            "superfast": "p1",
            "veryfast": "p2",
            "faster": "p2",
            "fast": "p3",
            "medium": "p4",
            "slow": "p5",
            "slower": "p6",
            "veryslow": "p7",
            "p1": "p1",
            "p2": "p2",
            "p3": "p3",
            "p4": "p4",
            "p5": "p5",
            "p6": "p6",
            "p7": "p7",
        }
        nvenc_preset = preset_map.get(preset, "p2" if quality == "draft" else "p4")
        if bitrate:
            cmd.extend(["-preset", nvenc_preset, "-rc", "vbr", "-b:v", bitrate])
        else:
            cmd.extend(["-preset", nvenc_preset, "-rc", "vbr", "-cq", crf])
        return

    if chosen_codec in {"libx264", "libx265"}:
        valid_presets = {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow", "placebo"}
        if preset not in valid_presets:
            preset = "veryfast" if quality == "draft" else "medium"
        if bitrate:
            cmd.extend(["-preset", preset, "-b:v", bitrate])
        else:
            cmd.extend(["-preset", preset, "-crf", crf])
        return

    if bitrate:
        cmd.extend(["-b:v", bitrate])
    else:
        cmd.extend(["-crf", crf, "-b:v", "0", "-cpu-used", "6" if quality == "draft" else "4"])

def has_nvenc() -> bool:
    global _has_nvenc_cache
    if _has_nvenc_cache is not None:
        return _has_nvenc_cache
    try:
        res = subprocess.run(
            [FFMPEG_PATH, "-encoders"],
            capture_output=True,
            text=True,
            timeout=5,
            startupinfo=_startupinfo(),
            creationflags=_creationflags()
        )
        _has_nvenc_cache = "h264_nvenc" in res.stdout
    except Exception:
        _has_nvenc_cache = False
    return _has_nvenc_cache



def _run_ffmpeg_legacy(cmd: list, timeout: int = 3600) -> bool:
    full_cmd = [FFMPEG_PATH, "-y"] + cmd
    print(f"[FFmpeg] {' '.join(str(a) for a in full_cmd)}")
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        result = subprocess.run(full_cmd, check=True, capture_output=True, text=True, timeout=timeout, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "")
        tail = err[-2000:] if len(err) > 2000 else err
        print(f"[FFmpeg] ERROR (rc={e.returncode}): {tail}")
        return False
    except FileNotFoundError:
        print("[FFmpeg] NOT FOUND — install FFmpeg or set FFMPEG_PATH env")
        return False

 
def run_ffmpeg(cmd: list, timeout: int = 3600, progress_cb=None, duration: float = None) -> bool:
    from .job_logger import job_log
    use_progress = bool(progress_cb and duration and duration > 0)
    progress_args = ["-nostats", "-progress", "pipe:1"] if use_progress else []
    full_cmd = [FFMPEG_PATH, "-y"] + progress_args + cmd
    job_log("info", f"[FFmpeg] Executing: {' '.join(str(a) for a in full_cmd)}")
    if use_progress:
        return _run_ffmpeg_with_progress(full_cmd, timeout, float(duration), progress_cb)

    try:
        res = subprocess.run(
            full_cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            startupinfo=_startupinfo(),
            creationflags=_creationflags(),
        )
        # Log standard output if any
        if res.stdout and res.stdout.strip():
            job_log("info", f"[FFmpeg] Output: {res.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        err = e.stderr or ""
        tail = err[-2000:] if len(err) > 2000 else err
        job_log("error", f"[FFmpeg] ERROR (rc={e.returncode}): {tail}")
        return False
    except FileNotFoundError:
        job_log("error", "[FFmpeg] NOT FOUND - install FFmpeg or set FFMPEG_PATH env")
        return False


def _run_ffmpeg_with_progress(full_cmd: list, timeout: int, duration: float, progress_cb) -> bool:
    from .job_logger import job_log
    tail = []
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=_startupinfo(),
            creationflags=_creationflags(),
        )
    except FileNotFoundError:
        job_log("error", "[FFmpeg] NOT FOUND - install FFmpeg or set FFMPEG_PATH env")
        return False

    try:
        for raw_line in process.stdout:
            line = raw_line.strip()
            if line:
                tail.append(line)
                tail = tail[-40:]
            if line.startswith("out_time_ms="):
                try:
                    out_seconds = int(line.split("=", 1)[1]) / 1_000_000
                    progress_cb(max(0, min(100, int((out_seconds / duration) * 100))))
                except Exception:
                    pass
            elif line.startswith("progress=end"):
                try:
                    progress_cb(100)
                except Exception:
                    pass
            if timeout and time.monotonic() - started > timeout:
                process.kill()
                job_log("error", f"[FFmpeg] ERROR: timeout after {timeout}s")
                return False
        rc = process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        job_log("error", f"[FFmpeg] ERROR: timeout after {timeout}s")
        return False

    if rc != 0:
        err_msg = ' | '.join(tail[-20:])
        job_log("error", f"[FFmpeg] ERROR (rc={rc}): {err_msg}")
        return False
    return True


def get_video_info(path: str) -> dict:
    cmd = [FFPROBE_PATH, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=_creationflags())
        data = json.loads(result.stdout)
        video_stream = next((s for s in data.get("streams", []) if s["codec_type"] == "video"), {})
        audio_stream = next((s for s in data.get("streams", []) if s["codec_type"] == "audio"), {})
        fmt = data.get("format", {})
        return {
            "width": int(video_stream.get("width", 0)),
            "height": int(video_stream.get("height", 0)),
            "fps": _parse_fps(video_stream.get("r_frame_rate", "0/1")),
            "duration": float(fmt.get("duration", 0)),
            "size": int(fmt.get("size", 0)),
            "video_codec": video_stream.get("codec_name", ""),
            "audio_codec": audio_stream.get("codec_name", ""),
        }
    except Exception:
        return {}


def _get_media_duration(path: str) -> float:
    if not path or not os.path.exists(path):
        return 0.0
    cmd = [FFPROBE_PATH, "-v", "quiet", "-print_format", "json", "-show_format", path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=_creationflags())
        data = json.loads(result.stdout or "{}")
        return float(data.get("format", {}).get("duration", 0) or 0)
    except Exception:
        return 0.0


def _parse_srt_times(path: str) -> list:
    if not path or not os.path.exists(path):
        return []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    def parse_time(value: str) -> float:
        match = re.search(r"(\d{1,2}):(\d{2}):(\d{2})(?:[,.](\d{1,3}))?", value)
        if not match:
            return 0.0
        h, m, s, ms = match.groups()
        return int(h) * 3600 + int(m) * 60 + int(s) + int((ms or "0").ljust(3, "0")) / 1000

    times = []
    for line in text.splitlines():
        if "-->" not in line:
            continue
        start, end = line.split("-->", 1)
        start_s = parse_time(start)
        end_s = parse_time(end)
        if end_s > start_s:
            times.append((start_s, end_s))
    return times


def _default_subtitle_region(position: str = "bottom") -> dict:
    pos = (position or "bottom").strip().lower()
    if pos in {"top", "top-center"}:
        return {"x": 0.1, "y": 0.06, "width": 0.8, "height": 0.16, "alignment": "top-center"}
    if pos in {"center", "middle"}:
        return {"x": 0.1, "y": 0.42, "width": 0.8, "height": 0.16, "alignment": "center"}
    return {"x": 0.1, "y": 0.78, "width": 0.8, "height": 0.16, "alignment": "bottom-center"}


def _append_segment_stretch_filters(
    filter_nodes: list,
    input_stream: str,
    source_subtitle_path: str,
    target_subtitle_path: str,
    video_duration: float,
    target_duration: float,
) -> str:
    def log_skip(reason: str):
        try:
            from .job_logger import job_log
            job_log("warning", f"[Render] Segment stretch skipped: {reason}")
        except Exception:
            pass

    source = _parse_srt_times(source_subtitle_path)
    target = _parse_srt_times(target_subtitle_path)
    if not source or not target:
        log_skip(f"source={len(source)} target={len(target)}")
        return input_stream

    count = min(len(source), len(target))
    if count < 2:
        log_skip(f"not enough cues: source={len(source)} target={len(target)}")
        return input_stream

    if len(source) != len(target):
        log_skip(f"cue count mismatch: source={len(source)} target={len(target)} using={count}")

    def clamp_time(value: float, upper: float) -> float:
        return max(0.0, min(float(value or 0.0), upper))

    source_points = [0.0] + [clamp_time(source[idx][0], video_duration) for idx in range(count)] + [video_duration]
    target_points = [0.0] + [clamp_time(target[idx][0], target_duration) for idx in range(count)] + [target_duration]

    # Keep boundaries strictly increasing. Failed/placeholder cues can produce
    # very tight timings; FFmpeg trim/concat needs positive durations.
    for points, upper in ((source_points, video_duration), (target_points, target_duration)):
        for idx in range(1, len(points)):
            if points[idx] <= points[idx - 1]:
                points[idx] = min(upper, points[idx - 1] + 0.02)

    labels = []
    for idx in range(len(source_points) - 1):
        src_start = source_points[idx]
        src_end = source_points[idx + 1]
        tgt_start = target_points[idx]
        tgt_end = target_points[idx + 1]

        if src_start >= video_duration or tgt_start >= target_duration:
            continue

        src_end = max(src_start + 0.02, min(src_end, video_duration))
        tgt_end = max(tgt_start + 0.02, min(tgt_end, target_duration))

        src_dur = max(0.02, src_end - src_start)
        tgt_dur = max(0.02, tgt_end - tgt_start)
        factor = tgt_dur / src_dur
        label = f"[vseg{idx}]"
        filter_nodes.append(
            f"{input_stream}trim=start={src_start:.3f}:end={src_end:.3f},setpts={factor:.6f}*(PTS-STARTPTS){label}"
        )
        labels.append(label)

    out = "[v_tts_segments]"
    if not labels:
        log_skip("no usable segment boundaries")
        return input_stream
    filter_nodes.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0{out}")
    return out


def render_video(input_path: str, output_path: str, params: dict = None):
    p = params or {}
    cmd = ["-i", input_path]

    vf = []
    if p.get("width") and p.get("height"):
        vf.append(f"scale={p['width']}:{p['height']}")
    if p.get("fps"):
        cmd.extend(["-r", str(p["fps"])])
    if vf:
        cmd.extend(["-vf", ",".join(vf)])

    _append_video_encoding_args(cmd, p)

    cmd.extend(["-c:a", "aac", "-b:a", p.get("audio_bitrate", "192k"), "-y", output_path])
    return run_ffmpeg(cmd)


def single_pass_render(video_path: str, output_path: str, params: dict = None, progress_cb=None) -> bool:
    """
    Renders video in a single pass: blurs hardsub region, burns soft subtitle,
    replaces/muxes audio, scales, sets fps, and encodes.
    All filter operations are combined into a single filter graph to avoid multiple encodings.
    """
    p = params or {}
    project_id = p.get("project_id", 0)

    # 1. Inputs
    cmd = ["-i", video_path]
    
    # Check if we have TTS audio to replace
    tts_path = p.get("tts_path")
    if not tts_path and project_id:
        potential_tts = str(VOICES_DIR / f"project_{project_id}_tts.wav")
        if os.path.exists(potential_tts):
            tts_path = potential_tts

    if tts_path and os.path.exists(tts_path):
        cmd.extend(["-i", tts_path])
        has_tts = True
    else:
        has_tts = False

    # 2. Build Video Filter Complex
    # Get original resolution for cropping calculations
    info = get_video_info(video_path)
    vw = info.get("width", 1920) or 1920
    vh = info.get("height", 1080) or 1080
    duration = float(info.get("duration", 0) or 0)
    tts_duration = _get_media_duration(tts_path) if has_tts else 0.0
    extend_video_to_tts = _as_bool(p.get("extend_video_to_tts"), False) and has_tts and tts_duration > duration > 0

    filter_nodes = []
    current_v_stream = "[0:v]"

    if extend_video_to_tts:
        segment_stretched = _append_segment_stretch_filters(
            filter_nodes,
            current_v_stream,
            p.get("source_subtitle_path"),
            p.get("subtitle_path"),
            duration,
            tts_duration,
        )
        if segment_stretched != current_v_stream:
            try:
                from .job_logger import job_log
                job_log("info", "[Render] Extending video per subtitle cue timing")
            except Exception:
                pass
            current_v_stream = segment_stretched
        else:
            stretch = max(1.0, tts_duration / duration)
            stretch_out = "[v_tts_stretch]"
            try:
                from .job_logger import job_log
                job_log("info", f"[Render] Extending video uniformly by {stretch:.3f}x")
            except Exception:
                pass
            filter_nodes.append(f"{current_v_stream}setpts={stretch:.6f}*PTS{stretch_out}")
            current_v_stream = stretch_out

    # Subtitle region blur (hardsub removal)
    remove_hardsub = _as_bool(p.get("remove_hardsub"), False)
    region = p.get("subtitle_region") or p.get("region")
    if remove_hardsub and not region:
        region = _default_subtitle_region(p.get("subtitle_position", "bottom"))
    if remove_hardsub and region and float(region.get("width", 0)) > 0 and float(region.get("height", 0)) > 0:
        def to_px(val, dim):
            return int(float(val) * dim) if float(val) <= 1 else int(float(val))

        rx = max(0, to_px(region["x"], vw))
        ry = max(0, to_px(region["y"], vh))
        rw = min(vw - rx, max(1, to_px(region.get("width", 0.7), vw)))
        rh = min(vh - ry, max(1, to_px(region.get("height", 0.15), vh)))
        pad_ratio = float(p.get("hardsub_blur_padding", 0.02) or 0.02)
        pad_x = max(16, int(vw * pad_ratio))
        pad_y = max(12, int(vh * pad_ratio * 0.75))
        rx2 = max(0, rx - pad_x)
        ry2 = max(0, ry - pad_y)
        rw = min(vw - rx2, rw + (rx - rx2) + pad_x)
        rh = min(vh - ry2, rh + (ry - ry2) + pad_y)
        rx, ry = rx2, ry2
        blur_strength = max(2, int(p.get("hardsub_blur_strength", 12) or 12))

        blur_out = "[blurred_sub]"
        overlay_out = "[v_blur_applied]"
        
        filter_nodes.append(f"{current_v_stream}crop=w={rw}:h={rh}:x={rx}:y={ry},boxblur=lr={blur_strength}:lp=2{blur_out}")
        filter_nodes.append(f"{current_v_stream}{blur_out}overlay=x={rx}:y={ry}{overlay_out}")
        current_v_stream = overlay_out

    # Soft subtitle burning
    burn = _as_bool(p.get("burn_subtitle"), True)
    sub_path = p.get("subtitle_path")
    if not sub_path and project_id:
        potential_sub = str(SUBTITLES_DIR / f"project_{project_id}_burn.srt")
        if os.path.exists(potential_sub):
            sub_path = potential_sub

    if burn and sub_path and os.path.exists(sub_path):
        sub_style = {
            "font": p.get("subtitle_font", "Arial"),
            "size": int(p.get("subtitle_size", 42)),
            "color": p.get("subtitle_color", "#FFFFFF"),
            "shadow": p.get("subtitle_shadow", "soft"),
            "stroke": int(p.get("subtitle_stroke", 2)),
            "position": p.get("subtitle_position", "bottom"),
        }
        sub_ext = Path(sub_path).suffix.lower()
        
        tmp_dir = Path(CACHE_DIR) / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        
        if sub_ext == ".srt" and region:
            ass_content = _srt_to_region_ass(
                Path(sub_path).read_text(encoding="utf-8"),
                vw, vh, region, sub_style
            )
            ass_tmp = str(tmp_dir / f"sub_region_{os.getpid()}_{os.urandom(4).hex()}.ass")
            Path(ass_tmp).write_text(ass_content, encoding="utf-8")
            actual_sub_path = ass_tmp
            sub_ext = ".ass"
        else:
            actual_sub_path = sub_path

        safe_src = _temp_copy(actual_sub_path)
        safe_path = _filter_path(safe_src)
        
        sub_out = "[v_sub_burned]"
        if sub_ext == ".ass":
            filter_nodes.append(f"{current_v_stream}ass=filename='{safe_path}'{sub_out}")
        else:
            filter_nodes.append(f"{current_v_stream}subtitles=filename='{safe_path}'{sub_out}")
        current_v_stream = sub_out

    # Scaling / Resolution conversion
    width = _positive_int(p.get("width"))
    height = _positive_int(p.get("height"))
    if width and height:
        scale_out = "[v_scaled]"
        keep_aspect = _as_bool(p.get("keep_aspect"), True)
        crop_enabled = _as_bool(p.get("crop_enabled"), False)
        if keep_aspect and crop_enabled:
            crop_x, crop_y = _crop_position_expr(p.get("crop_position"))
            scale_filter = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}:{crop_x}:{crop_y},setsar=1"
            )
        elif keep_aspect:
            scale_filter = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
            )
        else:
            scale_filter = f"scale={width}:{height},setsar=1"
        filter_nodes.append(f"{current_v_stream}{scale_filter}{scale_out}")
        current_v_stream = scale_out

    # Enhancement filters (LUT, color grading, effects)
    enhance = p.get("enhance") or {}

    # LUT (Color grading)
    lut = enhance.get("lut")
    if lut and lut.lower() != "none":
        from ..config import BASE_DIR
        lut_dir = BASE_DIR / "data" / "luts"
        lut_file = lut_dir / f"{lut}.cube"
        if lut_file.exists():
            lut_out = "[v_lut]"
            filter_nodes.append(f"{current_v_stream}lut3d='{_filter_path(str(lut_file))}'{lut_out}")
            current_v_stream = lut_out

    # Brightness, Contrast, Saturation
    brightness = enhance.get("brightness", 50)
    contrast = enhance.get("contrast", 55)
    saturation = enhance.get("saturation", 60)

    # Map from 0-100 scale to eq filter values
    # Brightness: -1 to 1 (50 = 0)
    # Contrast: -2 to 2 (55 = 0.2)
    # Saturation: 0 to 3 (60 = 1.2)
    if brightness != 50 or contrast != 55 or saturation != 60:
        eq_brightness = (float(brightness) - 50) / 50  # -1 to 1
        eq_contrast = (float(contrast) - 50) / 25  # -2 to 2
        eq_saturation = float(saturation) / 50  # 0 to 3

        eq_out = "[v_eq]"
        filter_nodes.append(
            f"{current_v_stream}eq=brightness={eq_brightness:.2f}:contrast={eq_contrast:.2f}:saturation={eq_saturation:.2f}{eq_out}"
        )
        current_v_stream = eq_out

    # Vignette effect
    vignette = enhance.get("vignette", 0)
    if vignette and float(vignette) > 0:
        # Vignette: 0-100 scale, map to 0-1 for vignette filter
        v_amount = float(vignette) / 100
        vig_out = "[v_vig]"
        filter_nodes.append(
            f"{current_v_stream}vignette=angle=PI*{v_amount:.4f}:x0=w/2:y0=h/2{vig_out}"
        )
        current_v_stream = vig_out

    # Watermark/Logo overlay
    watermark = enhance.get("watermark", False)
    watermark_text = str(enhance.get("watermark_text") or "").strip()
    if watermark and watermark_text:
        wm_out = "[v_wm]"
        filter_nodes.append(
            f"{current_v_stream}{drawtext_filter(watermark_text, fontcolor='white@0.5', fontsize=48, x='w-tw-20', y='h-th-20')}{wm_out}"
        )
        current_v_stream = wm_out

    video_map_stream = current_v_stream if filter_nodes else "0:v:0"

    # 3. Audio filter graph
    audio_out_stream = None
    if has_tts:
        tts_volume = _volume01(p.get("tts_volume"), 1.0)
        original_volume = _volume01(p.get("original_audio_volume"), 0.0)
        has_original_audio = bool(info.get("audio_codec"))
        if has_original_audio and original_volume > 0:
            audio_out_stream = "[a_mix]"
            audio_duration_mode = "longest" if extend_video_to_tts else "first"
            filter_nodes.append(
                f"[0:a]volume={original_volume}[a_orig];"
                f"[1:a]volume={tts_volume}[a_tts];"
                f"[a_orig][a_tts]amix=inputs=2:duration={audio_duration_mode}:dropout_transition=0{audio_out_stream}"
            )
        elif tts_volume < 1:
            audio_out_stream = "[a_tts]"
            filter_nodes.append(f"[1:a]volume={tts_volume}{audio_out_stream}")

    # 4. Assemble filters and map streams
    if filter_nodes:
        cmd.extend(["-filter_complex", ";".join(filter_nodes)])
        cmd.extend(["-map", video_map_stream])
    else:
        cmd.extend(["-map", "0:v:0"])

    if audio_out_stream:
        cmd.extend(["-map", audio_out_stream])
        if not extend_video_to_tts and (not duration or not tts_duration or tts_duration >= duration):
            cmd.append("-shortest")
    elif has_tts:
        cmd.extend(["-map", "1:a:0"])
        if not extend_video_to_tts and (not duration or not tts_duration or tts_duration >= duration):
            cmd.append("-shortest")
    else:
        cmd.extend(["-map", "0:a?"])

    # 5. FPS
    requested_fps = p.get("fps")
    original_fps = float(info.get("fps", 0) or 0)
    fps_changes = False
    if requested_fps:
        try:
            fps_changes = original_fps > 0 and abs(float(requested_fps) - original_fps) > 0.01
        except (TypeError, ValueError):
            fps_changes = True
        if fps_changes:
            cmd.extend(["-r", str(requested_fps)])

    copy_allowed = _as_bool(p.get("copy_if_possible"), True)
    if copy_allowed and not filter_nodes and not has_tts and not fps_changes and not p.get("force_encode"):
        cmd.extend(["-c", "copy"])
        if Path(output_path).suffix.lower() in {".mp4", ".mov", ".m4v"}:
            cmd.extend(["-movflags", "+faststart"])
        cmd.extend(["-y", output_path])
        return run_ffmpeg(cmd, progress_cb=progress_cb, duration=duration)

    # 6. Video Codec and encoding settings
    _append_video_encoding_args(cmd, p)

    # 7. Audio Codec
    cmd.extend(["-c:a", "aac", "-b:a", p.get("audio_bitrate", "192k")])

    # 8. Output
    cmd.extend(["-y", output_path])

    return run_ffmpeg(cmd, progress_cb=progress_cb, duration=duration)


def export_audio(input_path: str, output_path: str, fmt: str = "mp3"):
    codec_map = {"mp3": "libmp3lame", "wav": "pcm_s16le", "flac": "flac", "ogg": "libvorbis"}
    cmd = ["-i", input_path, "-vn", "-c:a", codec_map.get(fmt, "libmp3lame"), "-y", output_path]
    return run_ffmpeg(cmd)


def export_subtitle_file(content: str, fmt: str, project_id: int, style: dict = None) -> str:
    out = str(EXPORTS_DIR / f"sub_project_{project_id}.{fmt}")
    if fmt == "srt":
        Path(out).write_text(content, encoding="utf-8")
    elif fmt == "ass":
        font = style.get("font", "Arial") if style else "Arial"
        size = style.get("size", 42) if style else 42
        color = style.get("color", "#FFFFFF").replace("#", "") if style else "FFFFFF"
        # ASS color format is &HAABBGGRR. We get #RRGGBB. So reverse it: &H00 + BB + GG + RR
        if len(color) == 6:
            color = f"&H00{color[4:6]}{color[2:4]}{color[0:2]}"
        else:
            color = "&H00FFFFFF"
            
        shadow = style.get("shadow", "Soft") if style else "Soft"
        shadow_val = "2" if shadow.lower() == "soft" else "4" if shadow.lower() == "hard" else "0"
        
        ass_header = f"[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow\nStyle: Default,{font},{size},{color},&H00000000,0,0,1,2,{shadow_val}\n\n[Events]\nFormat: Layer, Start, End, Style, Text\n"
        
        blocks = content.strip().split("\n\n")
        lines = []
        for block in blocks:
            parts = block.split("\n")
            if len(parts) >= 3:
                time_line = parts[1]
                if "-->" in time_line:
                    t_parts = time_line.split("-->")
                    # SRT: 00:00:00,000 -> ASS: 0:00:00.00
                    def srt_to_ass_time(t_str):
                        t_str = t_str.strip().replace(",", ".")
                        if t_str.startswith("0") and len(t_str) >= 12: t_str = t_str[1:]
                        if len(t_str) > 10: t_str = t_str[:10]
                        return t_str
                    
                    start = srt_to_ass_time(t_parts[0])
                    end = srt_to_ass_time(t_parts[1])
                    text = "\\N".join(parts[2:])
                    lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
                    
        Path(out).write_text(ass_header + "\n".join(lines), encoding="utf-8")
    return out


def split_video(input_path: str, output_path: str, start: float, end: float):
    cmd = ["-i", input_path, "-ss", str(start), "-to", str(end), "-c", "copy", "-y", output_path]
    return run_ffmpeg(cmd)


def merge_videos(file_paths: list, output_path: str):
    list_path = Path(output_path).parent / "_merge_list.txt"
    list_path.write_text("\n".join(f"file '{p}'" for p in file_paths), encoding="utf-8")
    cmd = ["-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", "-y", output_path]
    result = run_ffmpeg(cmd)
    list_path.unlink(missing_ok=True)
    return result


def blur_subtitle_region(video_path: str, output_path: str, region: dict) -> bool:
    """Blur a rectangular region in the video using FFmpeg.

    Blurs subtitle area (crop -> boxblur -> overlay) to remove hardcoded subs.
    Region: {x, y, width, height} as fractions (0-1) of video dimensions.
    """
    info = get_video_info(video_path)
    vw, vh = info.get("width", 1920), info.get("height", 1080)
    if not vw or not vh:
        return False

    def to_px(val, dim):
        return int(float(val) * dim) if float(val) <= 1 else int(float(val))

    rx = max(0, to_px(region["x"], vw))
    ry = max(0, to_px(region["y"], vh))
    rw = min(vw - rx, max(1, to_px(region.get("width", 0.7), vw)))
    rh = min(vh - ry, max(1, to_px(region.get("height", 0.15), vh)))

    filter_complex = (
        f"[0:v]crop=w={rw}:h={rh}:x={rx}:y={ry},"
        f"boxblur=lr=2:lp=1[blurred];"
        f"[0:v][blurred]overlay=x={rx}:y={ry}[out]"
    )
    if has_nvenc():
        codec_args = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "18"]
    else:
        codec_args = ["-c:v", "libx264", "-crf", "18", "-preset", "medium"]

    cmd = [
        "-i", video_path,
        "-filter_complex", filter_complex,
        "-map", "[out]", "-map", "0:a",
    ] + codec_args + [
        "-c:a", "copy",
        "-y", output_path,
    ]
    return run_ffmpeg(cmd)


def _srt_to_region_ass(srt_content: str, video_w: int, video_h: int,
                       region: dict, style: dict) -> str:
    """Convert SRT content to ASS with text constrained to a region box.

    Auto-scales font, aligns text in box, wraps lines.
    """
    font = style.get("font", "Arial")
    size = int(style.get("size", 42))
    color = style.get("color", "#FFFFFF")
    shadow = style.get("shadow", "Soft")

    def to_px(val, dim):
        return int(float(val) * dim) if float(val) <= 1 else int(float(val))

    rx = to_px(region["x"], video_w)
    ry = to_px(region["y"], video_h)
    rw = to_px(region.get("width", 0.7), video_w)
    rh = to_px(region.get("height", 0.15), video_h)
    align_str = region.get("alignment", "bottom-center")

    # Map alignment string to ASS alignment number and calculate pos anchor
    # ASS Alignments: 1=bottom-left, 2=bottom-center, 3=bottom-right
    # 4=middle-left, 5=middle-center, 6=middle-right
    # 7=top-left, 8=top-center, 9=top-right
    align_map = {
        "bottom-center": (2, rx + rw // 2, ry + rh),
        "top-center": (8, rx + rw // 2, ry),
        "center": (5, rx + rw // 2, ry + rh // 2),
        "bottom-left": (1, rx, ry + rh),
        "bottom-right": (3, rx + rw, ry + rh),
        "top-left": (7, rx, ry),
        "top-right": (9, rx + rw, ry),
        "custom": (2, rx + rw // 2, ry + rh)
    }

    ass_align, cx, cy = align_map.get(align_str, (2, rx + rw // 2, ry + rh))
    base_fs = min(size, max(12, int(rh * 0.7)))

    color = color.lstrip("#")
    if len(color) == 6:
        ass_color = f"&H00{color[4:6]}{color[2:4]}{color[0:2]}"
    else:
        ass_color = "&H00FFFFFF"

    shadow_map = {"soft": "2", "hard": "4", "off": "0"}
    shadow_val = shadow_map.get(shadow.lower(), "2")

    def srt_to_ass_time(t_str):
        t = t_str.strip().replace(",", ".")
        if t.startswith("0") and len(t) >= 12:
            t = t[1:]
        if len(t) > 10:
            t = t[:10]
        return t

    ass = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video_w}\n"
        f"PlayResY: {video_h}\n"
        "WrapStyle: 1\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, BackColour, "
        "Bold, Italic, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV\n"
        f"Style: Default,{font},{base_fs},{ass_color},&H00000000,"
        f"0,0,1,2,{shadow_val},{ass_align},0,0,0\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, "
        "MarginL, MarginR, MarginV, Effect, Text\n"
    )

    blocks = re.split(r'\n\s*\n', srt_content.strip())
    parsed_events = []
    global_max_chars = 1
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        if not lines[0].isdigit():
            continue
        if '-->' not in lines[1]:
            continue

        parts = lines[1].split('-->')
        start = srt_to_ass_time(parts[0])
        end = srt_to_ass_time(parts[1])
        text = '\\N'.join(l.strip() for l in lines[2:] if l.strip())
        if not text:
            continue

        max_chars = max(len(l) for l in text.split('\\N'))
        global_max_chars = max(global_max_chars, max_chars)
        parsed_events.append((start, end, text))

    global_fs = max(12, min(base_fs, int(rw / (max(1, global_max_chars) * 0.55))))
    for start, end, text in parsed_events:
        override = f"{{\\fs{global_fs}\\pos({cx},{cy})}}" if global_fs != base_fs else f"{{\\pos({cx},{cy})}}"
        ass += f"Dialogue: 0,{start},{end},Default,,0,0,0,,{override}{text}\n"

    return ass


def burn_subtitle(video_path: str, subtitle_path: str, output_path: str = None,
                  region: dict = None, style: dict = None, remove_hardsub: bool = False) -> str:
    """Burn subtitles into video.

    If remove_hardsub and region are provided, blurs the region first.
    If region is provided, positions new subtitles inside the region box.
    """
    if not output_path:
        output_path = video_path.replace(".mp4", "_subbed.mp4")

    current_input = video_path
    tmp_blurred = None
    tmp_dir = Path(CACHE_DIR) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if remove_hardsub and not region:
        region = _default_subtitle_region((style or {}).get("position", "bottom"))

    if remove_hardsub and region and region.get("width", 0) > 0 and region.get("height", 0) > 0:
        tmp_blurred = str(tmp_dir / f"blur_{os.getpid()}_{os.urandom(4).hex()}.mp4")
        if blur_subtitle_region(video_path, tmp_blurred, region):
            current_input = tmp_blurred

    safe_src = _temp_copy(subtitle_path)
    sub_ext = Path(subtitle_path).suffix.lower()
    safe_path = _filter_path(safe_src)

    if has_nvenc():
        codec_args = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "18"]
    else:
        codec_args = ["-c:v", "libx264", "-crf", "18", "-preset", "medium"]

    if sub_ext == ".srt" and region:
        info = get_video_info(video_path)
        vw = info.get("width", 1920) or 1920
        vh = info.get("height", 1080) or 1080
        ass_content = _srt_to_region_ass(
            Path(subtitle_path).read_text(encoding="utf-8"),
            vw, vh, region, style or {}
        )
        ass_tmp = str(tmp_dir / f"sub_region_{os.getpid()}_{os.urandom(4).hex()}.ass")
        Path(ass_tmp).write_text(ass_content, encoding="utf-8")
        safe_ass = _temp_copy(ass_tmp)
        safe_path = _filter_path(safe_ass)
        cmd = [
            "-i", current_input,
            "-vf", f"ass=filename='{safe_path}'",
        ] + codec_args + [
            "-c:a", "copy",
            "-y", output_path,
        ]
    elif sub_ext == ".srt":
        cmd = [
            "-i", current_input,
            "-vf", f"subtitles=filename='{safe_path}'",
        ] + codec_args + [
            "-c:a", "copy",
            "-y", output_path,
        ]
    elif sub_ext == ".ass":
        cmd = [
            "-i", current_input,
            "-vf", f"ass=filename='{safe_path}'",
        ] + codec_args + [
            "-c:a", "copy",
            "-y", output_path,
        ]
    else:
        cmd = ["-i", current_input, "-c", "copy", "-y", output_path]

    result = run_ffmpeg(cmd)
    if tmp_blurred and os.path.exists(tmp_blurred):
        try:
            os.remove(tmp_blurred)
        except Exception:
            pass

    if not result:
        raise RuntimeError(f"FFmpeg burn_subtitle failed for {video_path}")
    if not os.path.exists(output_path):
        raise RuntimeError(f"burn_subtitle output not created: {output_path}")
    return output_path


def _temp_copy(path: str) -> str:
    """Copy file to %TEMP% to avoid special chars in path (e.g. &)."""
    import shutil
    import tempfile
    ext = Path(path).suffix
    tmp = os.path.join(tempfile.gettempdir(), f"sub_burn_{os.getpid()}_{os.urandom(4).hex()}{ext}")
    shutil.copy2(path, tmp)
    return tmp

def _filter_path(path: str) -> str:
    """Return POSIX path with colon escaped for use inside filename='...'."""
    return path.replace("\\", "/").replace(":", "\\:")




def replace_audio(video_path: str, audio_path: str, output_path: str = None) -> str:
    """Replace video audio track with a new audio file."""
    if not output_path:
        output_path = video_path.replace(".mp4", "_newaudio.mp4")
    duration = _get_media_duration(video_path)
    cmd = ["-i", video_path, "-i", audio_path, "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0"]
    if duration > 0:
        cmd.extend(["-t", f"{duration:.3f}"])
    else:
        cmd.append("-shortest")
    cmd.extend(["-y", output_path])
    if not run_ffmpeg(cmd):
        raise RuntimeError(f"FFmpeg replace_audio failed for {video_path}")
    if not os.path.exists(output_path):
        raise RuntimeError(f"replace_audio output not created: {output_path}")
    return output_path


def mix_audio(video_path: str, audio_path: str, volume: float = 0.3, output_path: str = None) -> str:
    """Mix background audio with existing video audio."""
    if not output_path:
        output_path = video_path.replace(".mp4", "_mixed.mp4")
    cmd = [
        "-i", video_path, "-i", audio_path,
        "-filter_complex",
        f"[1:a]volume={volume}[bga];[0:a][bga]amix=inputs=2:duration=first[outa]",
        "-map", "0:v:0", "-map", "[outa]",
        "-c:v", "copy",
        "-y", output_path,
    ]
    run_ffmpeg(cmd)
    return output_path


def extract_audio(video_path: str, output_path: str = None, sample_rate: int = 16000) -> str:
    """Extract audio from video, useful for STT."""
    if not output_path:
        output_path = video_path.replace(".mp4", ".wav")
    cmd = ["-i", video_path, "-vn", "-ac", "1", "-ar", str(sample_rate), "-y", output_path]
    run_ffmpeg(cmd)
    return output_path
