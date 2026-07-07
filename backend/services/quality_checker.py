from __future__ import annotations

import json
import math
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from ..config import FFPROBE_PATH, FFMPEG_PATH


@dataclass
class QualityIssue:
    code: str
    severity: str
    message: str
    time_ms: int | None = None
    meta: dict[str, Any] | None = None


def _issue(code: str, severity: str, message: str, time_ms: int | None = None, meta: dict[str, Any] | None = None) -> QualityIssue:
    return QualityIssue(code=code, severity=severity, message=message, time_ms=time_ms, meta=meta or {})


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _run_json(cmd: list[str], timeout: int = 20) -> dict[str, Any]:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_creationflags(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "command failed")
    return json.loads(proc.stdout or "{}")


def _fps_to_float(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    try:
        return float(Fraction(value))
    except Exception:
        return None


def _db(value: float) -> float:
    if value <= 0:
        return -120.0
    return 20.0 * math.log10(value)


def probe_media(path: str | Path) -> dict[str, Any]:
    return _run_json([
        FFPROBE_PATH,
        "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(path),
    ])


def check_video_basic(video_path: str | Path, target: str = "short_9_16") -> tuple[dict[str, Any], list[QualityIssue]]:
    data = probe_media(video_path)
    issues: list[QualityIssue] = []
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        return data, [_issue("NO_VIDEO_STREAM", "error", "Khong tim thay video stream.")]

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = _fps_to_float(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    codec = video.get("codec_name") or ""
    bitrate = int(video.get("bit_rate") or fmt.get("bit_rate") or 0)

    if fps is None:
        issues.append(_issue("FPS_UNKNOWN", "warning", "Khong doc duoc FPS."))
    elif fps < 29:
        issues.append(_issue("FPS_LOW", "warning", f"FPS thap: {fps:.2f}. Nen render 30 hoac 60 FPS.", meta={"fps": fps}))
    elif not (29 <= fps <= 31 or 59 <= fps <= 61):
        issues.append(_issue("FPS_UNUSUAL", "info", f"FPS la: {fps:.2f}. Nen chuan hoa ve 30/60.", meta={"fps": fps}))

    if target == "short_9_16" and width and height:
        aspect = width / height
        if width >= height:
            issues.append(_issue("ASPECT_NOT_VERTICAL", "warning", f"Video khong phai doc: {width}x{height}.", meta={"width": width, "height": height}))
        if abs(aspect - 9 / 16) > 0.04:
            issues.append(_issue("ASPECT_RATIO_OFF", "warning", f"Ti le lech 9:16: {width}x{height}.", meta={"aspect": aspect}))

    if bitrate <= 0:
        issues.append(_issue("BITRATE_UNKNOWN", "info", "Khong doc duoc bitrate."))
    else:
        mbps = bitrate / 1_000_000
        if width >= 1080 and height >= 1920 and mbps < 6:
            issues.append(_issue("BITRATE_LOW", "warning", f"Bitrate thap cho 1080p: {mbps:.2f} Mbps.", meta={"bitrate": bitrate}))
        elif mbps < 2:
            issues.append(_issue("BITRATE_TOO_LOW", "warning", f"Bitrate qua thap: {mbps:.2f} Mbps.", meta={"bitrate": bitrate}))

    if codec not in {"h264", "hevc", "h265", "av1"}:
        issues.append(_issue("VIDEO_CODEC_UNUSUAL", "info", f"Codec video khong pho bien: {codec}.", meta={"codec": codec}))

    if not audio:
        issues.append(_issue("NO_AUDIO_STREAM", "warning", "Video khong co audio stream."))
    else:
        audio_codec = audio.get("codec_name") or ""
        sample_rate = str(audio.get("sample_rate") or "")
        if audio_codec not in {"aac", "mp3", "opus", "pcm_s16le", "flac"}:
            issues.append(_issue("AUDIO_CODEC_UNUSUAL", "info", f"Codec audio khong pho bien: {audio_codec}.", meta={"codec": audio_codec}))
        if sample_rate and sample_rate not in {"44100", "48000"}:
            issues.append(_issue("AUDIO_SAMPLE_RATE_UNUSUAL", "info", f"Sample rate la: {sample_rate} Hz.", meta={"sample_rate": sample_rate}))

    return data, issues


def load_audio_mono_f32(media_path: str | Path, sample_rate: int = 48000, max_seconds: int = 900) -> tuple[np.ndarray, int]:
    cmd = [
        FFMPEG_PATH,
        "-v", "error",
        "-t", str(max_seconds),
        "-i", str(media_path),
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "f32le",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_creationflags())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip())
    return np.frombuffer(proc.stdout, dtype=np.float32), sample_rate


def check_audio_quality(media_path: str | Path) -> list[QualityIssue]:
    try:
        audio, _sr = load_audio_mono_f32(media_path)
    except Exception as exc:
        return [_issue("AUDIO_READ_FAILED", "warning", f"Khong doc duoc audio: {exc}")]
    if audio.size == 0:
        return [_issue("AUDIO_EMPTY", "warning", "Audio rong hoac khong co du lieu.")]

    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(audio ** 2)))
    peak_db = _db(peak)
    rms_db = _db(rms)
    clipped = int(np.sum(np.abs(audio) >= 0.999))
    clipped_ratio = clipped / max(1, audio.size)
    issues: list[QualityIssue] = []
    if clipped and clipped_ratio > 0.00001:
        issues.append(_issue("AUDIO_CLIPPING", "error", f"Audio bi clipping: {clipped} samples cham nguong.", meta={"peak_db": peak_db, "rms_db": rms_db, "clipped_ratio": clipped_ratio}))
    elif peak_db > -0.3:
        issues.append(_issue("AUDIO_NEAR_CLIPPING", "warning", f"Audio gan clipping: peak {peak_db:.2f} dB.", meta={"peak_db": peak_db, "rms_db": rms_db}))
    if rms_db < -32:
        issues.append(_issue("AUDIO_TOO_QUIET", "warning", f"Audio hoi nho: RMS {rms_db:.2f} dB.", meta={"rms_db": rms_db}))
    if rms_db > -8:
        issues.append(_issue("AUDIO_TOO_LOUD", "warning", f"Audio qua lon: RMS {rms_db:.2f} dB.", meta={"rms_db": rms_db}))
    return issues


_SRT_RE = re.compile(
    r"(?:^|\n)\s*(\d+)\s*\n"
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3}).*?\n"
    r"(.*?)(?=\n\s*\d+\s*\n\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->|$)",
    re.DOTALL,
)


def _time_ms(value: str) -> int:
    h, m, rest = value.replace(",", ".").split(":")
    s, ms = rest.split(".")
    return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms[:3].ljust(3, "0"))


def _clean_sub_text(text: str) -> str:
    text = re.sub(r"\{.*?\}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\\N", "\n")
    return text.strip()


def parse_subtitle_events(path: str | Path) -> list[dict[str, Any]]:
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    events = []
    if "-->" in content:
        for idx, match in enumerate(_SRT_RE.finditer(content), start=1):
            text = _clean_sub_text(match.group(4))
            events.append({
                "index": idx,
                "start_ms": _time_ms(match.group(2)),
                "end_ms": _time_ms(match.group(3)),
                "text": text,
            })
    else:
        for idx, line in enumerate(content.splitlines(), start=1):
            if line.startswith("Dialogue:"):
                parts = line.split(",", 9)
                if len(parts) >= 10:
                    events.append({"index": idx, "start_ms": 0, "end_ms": 0, "text": _clean_sub_text(parts[9])})
    return events


def check_subtitles(subtitle_path: str | Path, max_chars_per_line: int = 42, max_lines: int = 2, max_cps: float = 17.0) -> tuple[list[dict[str, Any]], list[QualityIssue]]:
    try:
        events = parse_subtitle_events(subtitle_path)
    except Exception as exc:
        return [], [_issue("SUBTITLE_READ_FAILED", "error", f"Khong doc duoc subtitle: {exc}")]
    issues: list[QualityIssue] = []
    seen: dict[str, int] = {}
    last_end = -1
    for ev in events:
        idx = ev["index"]
        text = ev["text"]
        one_line = " ".join(text.split())
        start = int(ev["start_ms"])
        end = int(ev["end_ms"])
        duration = end - start
        if not one_line:
            issues.append(_issue("SUB_EMPTY", "warning", f"Subtitle #{idx} rong.", start, {"index": idx}))
            continue
        if duration <= 0:
            issues.append(_issue("SUB_BAD_TIMING", "error", f"Subtitle #{idx} sai timing.", start, {"index": idx}))
        if last_end > start:
            issues.append(_issue("SUB_OVERLAP", "warning", f"Subtitle #{idx} chong thoi gian voi cue truoc.", start, {"index": idx}))
        last_end = max(last_end, end)
        if duration < 800:
            issues.append(_issue("SUB_TOO_SHORT_DURATION", "warning", f"Subtitle #{idx} hien qua nhanh: {duration / 1000:.2f}s.", start, {"index": idx}))
        key = one_line.lower()
        if key in seen:
            issues.append(_issue("SUB_DUPLICATE_TEXT", "warning", f"Subtitle #{idx} trung voi subtitle #{seen[key]}.", start, {"index": idx, "duplicate_of": seen[key]}))
        seen.setdefault(key, idx)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) > max_lines:
            issues.append(_issue("SUB_TOO_MANY_LINES", "warning", f"Subtitle #{idx} co {len(lines)} dong.", start, {"index": idx, "lines": len(lines)}))
        for line_no, line in enumerate(lines, start=1):
            if len(line) > max_chars_per_line:
                issues.append(_issue("SUB_LINE_TOO_LONG", "warning", f"Subtitle #{idx} dong {line_no} qua dai: {len(line)} ky tu.", start, {"index": idx, "line_no": line_no, "length": len(line)}))
        cps = len(one_line) / max(0.001, duration / 1000)
        if cps > max_cps:
            issues.append(_issue("SUB_CPS_TOO_HIGH", "warning", f"Subtitle #{idx} doc qua nhanh: {cps:.1f} ky tu/giay.", start, {"index": idx, "cps": cps}))
    return events, issues


def check_voice_sync_rough(media_path: str | Path, subtitle_events: list[dict[str, Any]], silence_db_threshold: float = -45.0) -> list[QualityIssue]:
    if not subtitle_events:
        return []
    try:
        audio, sr = load_audio_mono_f32(media_path)
    except Exception as exc:
        return [_issue("VOICE_SYNC_AUDIO_READ_FAILED", "warning", f"Khong doc duoc audio de check voice: {exc}")]
    issues: list[QualityIssue] = []
    total_ms = int(audio.size / sr * 1000)
    for ev in subtitle_events:
        start = max(0, int(ev["start_ms"]))
        end = min(total_ms, int(ev["end_ms"]))
        if end <= start:
            continue
        chunk = audio[int(start / 1000 * sr):int(end / 1000 * sr)]
        if chunk.size == 0:
            continue
        rms_db = _db(float(np.sqrt(np.mean(chunk ** 2))))
        if rms_db < silence_db_threshold:
            issues.append(_issue("VOICE_POSSIBLY_MISSING_OR_SHIFTED", "warning", f"Subtitle #{ev['index']} co text nhung audio rat nho/im lang.", start, {"index": ev["index"], "rms_db": rms_db}))
    return issues


def check_font_coverage(subtitle_path: str | Path, font_path: str | Path) -> list[QualityIssue]:
    try:
        from fontTools.ttLib import TTFont
    except Exception:
        return [_issue("FONTTOOLS_MISSING", "info", "Chua cai fonttools nen bo qua check font.")]
    try:
        events = parse_subtitle_events(subtitle_path)
        font = TTFont(str(font_path))
    except Exception as exc:
        return [_issue("FONT_READ_FAILED", "warning", f"Khong doc duoc font/subtitle: {exc}")]
    cmap = set()
    for table in font["cmap"].tables:
        cmap.update(table.cmap.keys())
    chars = {ch for ev in events for ch in ev.get("text", "") if not ch.isspace()}
    missing = sorted(ch for ch in chars if ord(ch) not in cmap)
    if missing:
        return [_issue("FONT_MISSING_GLYPHS", "error", "Font thieu ky tu subtitle.", meta={"missing_count": len(missing), "missing_preview": "".join(missing[:50])})]
    return []


def check_tts_drift(source_subtitle_path: str | Path | None, aligned_subtitle_path: str | Path | None, max_drift_ms: int = 500) -> list[QualityIssue]:
    if not source_subtitle_path or not aligned_subtitle_path:
        return []
    src = Path(source_subtitle_path)
    aligned = Path(aligned_subtitle_path)
    if not src.exists() or not aligned.exists():
        return []
    try:
        src_events = parse_subtitle_events(src)
        out_events = parse_subtitle_events(aligned)
    except Exception:
        return []
    issues = []
    for s, a in zip(src_events, out_events):
        drift = int(a["end_ms"]) - int(s["end_ms"])
        if abs(drift) > max_drift_ms:
            issues.append(_issue("VOICE_DRIFT", "warning", f"Voice/subtitle #{s['index']} lech {drift}ms so voi subtitle goc.", int(s["start_ms"]), {"index": s["index"], "drift_ms": drift}))
    return issues


def run_quality_check(
    video_path: str | Path,
    subtitle_path: str | Path | None = None,
    font_path: str | Path | None = None,
    target: str = "short_9_16",
    source_subtitle_path: str | Path | None = None,
    aligned_subtitle_path: str | Path | None = None,
) -> dict[str, Any]:
    video = Path(video_path)
    if not video.exists():
        issue = _issue("VIDEO_NOT_FOUND", "error", f"Khong tim thay video: {video}")
        return {"ok": False, "status": "FAIL", "summary": {"errors": 1, "warnings": 0, "infos": 0, "total": 1}, "issues": [asdict(issue)]}

    issues: list[QualityIssue] = []
    try:
        probe, video_issues = check_video_basic(video, target)
    except Exception as exc:
        probe, video_issues = {}, [_issue("FFPROBE_FAILED", "error", f"ffprobe loi: {exc}")]
    issues.extend(video_issues)
    issues.extend(check_audio_quality(video))

    events: list[dict[str, Any]] = []
    if subtitle_path:
        sub = Path(subtitle_path)
        if sub.exists():
            events, sub_issues = check_subtitles(sub)
            issues.extend(sub_issues)
            issues.extend(check_voice_sync_rough(video, events))
            if font_path:
                font = Path(font_path)
                if font.exists():
                    issues.extend(check_font_coverage(sub, font))
                else:
                    issues.append(_issue("FONT_NOT_FOUND", "warning", f"Khong tim thay font: {font}"))
        else:
            issues.append(_issue("SUBTITLE_NOT_FOUND", "warning", f"Khong tim thay subtitle: {sub}"))
    issues.extend(check_tts_drift(source_subtitle_path, aligned_subtitle_path))

    counts = {
        "errors": len([x for x in issues if x.severity == "error"]),
        "warnings": len([x for x in issues if x.severity == "warning"]),
        "infos": len([x for x in issues if x.severity == "info"]),
    }
    counts["total"] = sum(counts.values())
    status = "FAIL" if counts["errors"] else "WARNING" if counts["warnings"] else "PASS"
    return {
        "ok": status != "FAIL",
        "status": status,
        "summary": counts,
        "issues": [asdict(x) for x in issues],
        "video_probe": probe,
    }
