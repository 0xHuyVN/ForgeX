import os
import sys
import json
import subprocess
import shutil
from pathlib import Path


def _find_python():
    """Find system Python (not the PyInstaller EXE)."""
    configured = os.environ.get("PYTHON_EXECUTABLE", "").strip()
    if configured and os.path.exists(configured):
        return configured
    if getattr(sys, 'frozen', False):
        candidates = [
            os.path.join(os.path.dirname(sys.executable), "python.exe"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        python = shutil.which("python") or shutil.which("py")
        if python:
            return python
        raise RuntimeError("System Python not found. Set PYTHON_EXECUTABLE to enable STT subprocess mode.")
    return sys.executable


def _stt_script():
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).resolve().parent
        candidate = exe_dir / "backend" / "services" / "transcribe_worker.py"
        if candidate.exists():
            return candidate
    return Path(__file__).parent / "transcribe_worker.py"


def transcribe_subprocess(
    audio_path: str,
    language: str = "vi",
    model: str = "base",
    use_whisperx: bool = False,
    device: str = "auto",
    compute_type: str = "auto",
    timeout_seconds: int = 600,
) -> dict:
    """Run faster-whisper/whisperx in a subprocess to avoid native DLL conflicts in PyInstaller bundle."""
    python = _find_python()
    script = _stt_script()
    if not script.exists():
        return {"srt_path": "", "text": "", "segments": 0, "error": "worker script not found"}

    cmd = [
        python,
        str(script),
        audio_path,
        "--language",
        language,
        "--model",
        model,
        "--device",
        device,
        "--compute-type",
        compute_type,
    ]
    if use_whisperx:
        cmd.append("--whisperx")

    try:
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout_seconds,
            encoding="utf-8", errors="replace", env=env,
        )
        if result.returncode != 0:
            parsed = _parse_worker_json(result.stdout)
            if parsed.get("error"):
                return parsed
            err = result.stderr.strip()
            out = result.stdout.strip()
            return {"srt_path": "", "text": "", "segments": 0, "error": err or out}
        return _parse_worker_json(result.stdout)
    except subprocess.TimeoutExpired:
        return {"srt_path": "", "text": "", "segments": 0, "error": "transcription timed out"}
    except Exception as e:
        return {"srt_path": "", "text": "", "segments": 0, "error": str(e)}


def _parse_worker_json(stdout: str) -> dict:
    """Worker dependencies may print logs before the JSON payload."""
    text = (stdout or "").strip()
    if not text:
        return {"srt_path": "", "text": "", "segments": 0, "error": "empty transcription output"}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                return json.loads(line)
        return {"srt_path": "", "text": "", "segments": 0, "error": text[-1000:]}
