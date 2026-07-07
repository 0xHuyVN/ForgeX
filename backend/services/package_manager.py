from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass

from ..config import FFMPEG_PATH, FFPROBE_PATH, YTDLP_PATH


@dataclass
class PackageStatus:
    id: str
    label: str
    kind: str
    installed: bool
    version: str = ""
    path: str = ""
    install_command: list[str] | None = None
    error: str = ""


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _run_version(cmd: list[str], timeout: int = 8) -> tuple[bool, str, str]:
    try:
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
        output = (proc.stdout or proc.stderr or "").splitlines()
        return proc.returncode == 0, output[0] if output else "", proc.stderr.strip()
    except Exception as exc:
        return False, "", str(exc)


def _binary_status(pkg_id: str, label: str, cmd: str, version_args: list[str], install_command: list[str]) -> PackageStatus:
    resolved = shutil.which(cmd) or (cmd if os.path.exists(cmd) else "")
    ok, version, err = _run_version([cmd, *version_args]) if resolved or cmd else (False, "", "not found")
    return PackageStatus(pkg_id, label, "binary", ok, version, resolved, install_command, "" if ok else err)


def _python_status(pkg_id: str, label: str, module: str, pip_name: str | None = None) -> PackageStatus:
    installed = importlib.util.find_spec(module) is not None
    version = ""
    if installed:
        try:
            mod = __import__(module)
            version = str(getattr(mod, "__version__", ""))
        except Exception:
            pass
    return PackageStatus(
        pkg_id,
        label,
        "python",
        installed,
        version,
        "",
        [sys.executable, "-m", "pip", "install", pip_name or module],
        "" if installed else "module not importable",
    )


def check_packages() -> dict:
    packages = [
        _binary_status("ffmpeg", "FFmpeg", FFMPEG_PATH, ["-version"], ["winget", "install", "Gyan.FFmpeg"]),
        _binary_status("ffprobe", "FFprobe", FFPROBE_PATH, ["-version"], ["winget", "install", "Gyan.FFmpeg"]),
        _binary_status("yt-dlp", "yt-dlp CLI", YTDLP_PATH, ["--version"], [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]),
        _binary_status("python", "Python", sys.executable, ["--version"], []),
        _python_status("yt_dlp", "yt-dlp Python", "yt_dlp", "yt-dlp"),
        _python_status("faster_whisper", "faster-whisper", "faster_whisper", "faster-whisper"),
        _python_status("whisperx", "WhisperX", "whisperx", "whisperx"),
        _python_status("edge_tts", "Edge TTS", "edge_tts", "edge-tts"),
        _python_status("numpy", "NumPy", "numpy", "numpy"),
    ]
    data = [asdict(pkg) for pkg in packages]
    missing = [pkg for pkg in data if not pkg["installed"]]
    return {"ok": not missing, "packages": data, "missing": missing}


def install_package(package_id: str) -> dict:
    status = check_packages()
    package = next((pkg for pkg in status["packages"] if pkg["id"] == package_id), None)
    if not package:
        return {"ok": False, "error": f"Unknown package: {package_id}"}
    if package["installed"]:
        return {"ok": True, "status": "already_installed", "package": package}
    command = package.get("install_command") or []
    if not command:
        return {"ok": False, "error": f"No automatic installer configured for {package_id}", "package": package}
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
            creationflags=_creationflags(),
        )
        return {
            "ok": proc.returncode == 0,
            "status": "done" if proc.returncode == 0 else "failed",
            "returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-4000:],
            "stderr_tail": (proc.stderr or "")[-4000:],
            "command": command,
        }
    except Exception as exc:
        return {"ok": False, "status": "failed", "error": str(exc), "command": command}
