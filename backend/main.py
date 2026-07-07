from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Force stdout/stderr to UTF-8 so Vietnamese (and other non-cp1252) characters in
# logs / print() never crash the worker on Windows consoles. Safe no-op when stdout
# is already UTF-8 (frozen builds redirect to a UTF-8 file in run_exe.py) or when
# stdout has been replaced by something that doesn't expose .reconfigure().
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

from .config import (
    HOST,
    PORT,
    BASE_DIR,
    MAX_QUEUE_WORKERS,
    DATA_DIR,
    DOWNLOADS_DIR,
    SUBTITLES_DIR,
    VOICES_DIR,
    EXPORTS_DIR,
)
from .database import init_db
from .services.path_allowlist import allow_path, is_allowed_path
from .services.path_guard import (
    add_extra_root,
    data_roots,
    safe_folder_to_open,
    PathGuardError,
)
from .services.csrf_guard import CsrfMiddleware
from .services.preset_service import init_presets
from .workers.ffmpeg_worker import get_worker
from .routers import (
    project_router,
    download_router,
    subtitle_router,
    voice_router,
    music_router,
    enhance_router,
    edit_router,
    ai_router,
    export_router,
    queue_router,
    asset_router,
    preset_router,
    timeline_router,
    pipeline_router,
    publish_router,
    template_router,
    batch_router,
    quality_router,
    analytics_router,
    packages_router,
    telemetry_router,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    from .config import DB_PATH
    print(f"[Server] Database path: {DB_PATH}")
    print(f"[Server] Database exists: {DB_PATH.exists()}")
    
    init_db()
    init_presets()
    
    from .services.queue_manager import clear_all
    try:
        clear_all()
        print("[Server] Cleared old queue items on startup")
    except Exception as e:
        print(f"[Server] Failed to clear queue: {e}")
    
    worker = get_worker()
    worker.max_workers = MAX_QUEUE_WORKERS
    worker.start()
    app.state.worker = worker
    print(f"[Server] Queue worker started with {MAX_QUEUE_WORKERS} workers")
    yield
    worker.stop()
    print("[Server] Queue worker stopped")


APP_VERSION = "2.1.1"


def _local_app_origins():
    ports = set(range(7860, 7865))
    ports.add(PORT)
    return [
        origin
        for port in sorted(ports)
        for origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")
    ]


LOCAL_APP_ORIGINS = _local_app_origins()


app = FastAPI(title="0xForge API", version=APP_VERSION, lifespan=lifespan)

# CSRF / same-origin gate for state-changing methods. Runs before CORS so
# we never leak credentials to a non-local origin.
app.add_middleware(CsrfMiddleware, allowed_origins=set(LOCAL_APP_ORIGINS))

# CORS is intentionally narrow: only the bundled SPA on controlled local app ports.
# - No "null" origin (it is XSS-friendly and lets sandboxed iframes ride
#   credentials).
# - No allow_origin_regex covering arbitrary localhost ports.
# - Methods/headers are explicit; we do not need "*" in a desktop app.
app.add_middleware(
    CORSMiddleware,
    allow_origins=LOCAL_APP_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
    expose_headers=["Content-Disposition"],
    max_age=600,
)

app.include_router(project_router, prefix="/api/projects", tags=["Project"])
app.include_router(download_router, prefix="/api/download", tags=["Download"])
app.include_router(subtitle_router, prefix="/api/subtitle", tags=["Subtitle"])
app.include_router(voice_router, prefix="/api/voice", tags=["Voice"])
app.include_router(music_router, prefix="/api/music", tags=["Music"])
app.include_router(enhance_router, prefix="/api/enhance", tags=["Enhance"])
app.include_router(edit_router, prefix="/api/edit", tags=["Edit"])
app.include_router(ai_router, prefix="/api/ai", tags=["AI"])
app.include_router(export_router, prefix="/api/export", tags=["Export"])
app.include_router(queue_router, prefix="/api/queue", tags=["Queue"])
app.include_router(asset_router, prefix="/api/assets", tags=["Assets"])
app.include_router(preset_router, prefix="/api/presets", tags=["Presets"])
app.include_router(timeline_router, prefix="/api/timeline", tags=["Timeline"])
app.include_router(pipeline_router, prefix="/api/pipeline", tags=["Pipeline"])
app.include_router(publish_router, prefix="/api/publish", tags=["Publish"])
app.include_router(template_router, prefix="/api/templates", tags=["Templates"])
app.include_router(batch_router, prefix="/api/batch", tags=["Batch"])
app.include_router(quality_router, prefix="/api/quality", tags=["Quality"])
app.include_router(analytics_router, prefix="/api/analytics", tags=["Analytics"])
app.include_router(packages_router, prefix="/api/packages", tags=["Packages"])
app.include_router(telemetry_router, prefix="/api/telemetry", tags=["Telemetry"])


@app.get("/api/health")
def health():
    return {"status": "ok", "version": APP_VERSION}

@app.get("/api/system/browse")
async def browse_system(type: str = "file", ext: str = ""):
    import tkinter as tk
    from tkinter import filedialog
    import asyncio
    from .config import SUBTITLES_DIR
    
    def _open_dialog():
        root = tk.Tk()
        root.attributes("-topmost", True)
        root.withdraw()
        if type == "folder":
            path = filedialog.askdirectory(parent=root, initialdir=str(SUBTITLES_DIR))
        elif ext == "srt":
            path = filedialog.askopenfilename(parent=root, initialdir=str(SUBTITLES_DIR), filetypes=[("Subtitle files", "*.srt *.ass")])
        elif ext == "video":
            path = filedialog.askopenfilename(parent=root, filetypes=[("Video files", "*.mp4 *.mkv *.avi *.mov")])
        else:
            path = filedialog.askopenfilename(parent=root, filetypes=[("All files", "*.*")])
        root.destroy()
        return path

    path = await asyncio.to_thread(_open_dialog)
    if path and ext in {"video", "srt"}:
        try:
            allow_path(path)
        except Exception:
            pass
    if path and type == "folder":
        try:
            add_extra_root(path)
            allow_path(path)
        except Exception:
            pass
    return {"path": path}


@app.get("/api/system/open-folder")
async def open_folder_system(path: str):
    """Open ``path`` in the OS file manager.

    Hardened: rejects anything that does not resolve inside an allowed root,
    and rejects any path that resolves to a non-directory / known executable
    extension (no RCE via ``os.startfile`` on a ``.lnk`` or ``.bat``).
    """
    import os
    import subprocess

    extra_roots = [DATA_DIR, DOWNLOADS_DIR, SUBTITLES_DIR, VOICES_DIR, EXPORTS_DIR]
    try:
        target = safe_folder_to_open(path, field="path", extra_roots=extra_roots)
    except PathGuardError as original_exc:
        try:
            candidate = Path(path).expanduser().resolve(strict=False)
            if candidate.suffix:
                target = safe_folder_to_open(candidate.parent, field="path", extra_roots=extra_roots)
            else:
                raise original_exc
        except Exception:
            from fastapi import HTTPException
            raise HTTPException(400, str(original_exc))

    if sys.platform.startswith("win"):
        os.startfile(str(target))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])

    return {"ok": True, "path": str(target)}



@app.get("/api/stats")
def stats():
    from .database import db_cursor
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM queue_items WHERE status='running'")
        running = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM queue_items WHERE status='waiting'")
        waiting = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM queue_items WHERE status='completed'")
        completed = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM queue_items WHERE status='failed'")
        failed = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(tokens),0) FROM api_usage WHERE date=date('now','localtime')")
        tokens = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(seconds),0) FROM api_usage WHERE date=date('now','localtime')")
        seconds = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(cost),0) FROM api_usage WHERE date=date('now','localtime')")
        cost = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM projects")
        projects_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM assets")
        assets_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM tracks")
        tracks_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM exports")
        exports_count = cur.fetchone()[0]
    return {
        "queue": {"running": running, "waiting": waiting, "completed": completed, "failed": failed},
        "api_usage": {"tokens": tokens, "tts_seconds": seconds, "cost": round(cost, 2)},
        "projects": projects_count,
        "assets": assets_count,
        "tracks": tracks_count,
        "exports": exports_count,
    }


@app.get("/api/system/gpu")
def detect_gpu():
    import subprocess, re
    result = {"nvidia": False, "amd": False, "intel": False, "primary": "cpu", "details": []}
    try:
        nvidia = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
        if nvidia.returncode == 0:
            result["nvidia"] = True
            result["primary"] = "nvidia"
            for line in nvidia.stdout.strip().split("\n"):
                if line.strip():
                    parts = [p.strip() for p in line.split(",")]
                    result["details"].append({"type": "nvidia", "name": parts[0] if len(parts) > 0 else "", "driver": parts[1] if len(parts) > 1 else "", "memory": parts[2] if len(parts) > 2 else ""})
    except Exception:
        pass
    if not result["nvidia"]:
        try:
            rocm = subprocess.run(["rocm-smi", "--showproductname"], capture_output=True, text=True, timeout=10)
            if rocm.returncode == 0:
                result["amd"] = True
                result["primary"] = "amd"
                for line in rocm.stdout.split("\n"):
                    if "GPU" in line or "Card" in line:
                        result["details"].append({"type": "amd", "name": line.strip()})
        except Exception:
            pass
    try:
        import torch
        if torch.cuda.is_available():
            result["nvidia"] = True
            result["primary"] = "nvidia"
            for i in range(torch.cuda.device_count()):
                result["details"].append({"type": "cuda", "name": torch.cuda.get_device_name(i), "memory": f"{torch.cuda.get_device_properties(i).total_memory / 1024**3:.0f}GB"})
    except ImportError:
        pass
    try:
        import platform
        if platform.system() == "Windows":
            import ctypes
            class D3DKMT_ADAPTERINFO(ctypes.Structure):
                _fields_ = [("hAdapter", ctypes.c_uint), ("luid", ctypes.c_int64), ("numSources", ctypes.c_uint32), ("numPresentTargets", ctypes.c_uint32), ("AdapterType", ctypes.c_uint32)]
            result["details"].append({"type": "info", "os": platform.system(), "arch": platform.machine()})
    except Exception:
        pass
    return result


@app.get("/api/system/info")
def system_info():
    import platform, psutil
    info = {
        "os": platform.system(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "cpu_count": psutil.cpu_count(),
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_total_gb": round(psutil.virtual_memory().total / 1024**3, 1),
        "memory_available_gb": round(psutil.virtual_memory().available / 1024**3, 1),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_total_gb": round(psutil.disk_usage("/").total / 1024**3, 1),
        "disk_free_gb": round(psutil.disk_usage("/").free / 1024**3, 1),
    }
    try:
        import subprocess
        ffmpeg_ver = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        info["ffmpeg"] = ffmpeg_ver.stdout.split("\n")[0] if ffmpeg_ver.returncode == 0 else "not found"
    except Exception:
        info["ffmpeg"] = "not found"
    return info


@app.get("/api/settings")
def get_settings():
    from .database import db_cursor
    from .services.secure_settings import reveal_setting
    with db_cursor() as cur:
        rows = cur.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: reveal_setting(r["key"], r["value"]) for r in rows}


@app.put("/api/settings")
def save_settings(data: dict):
    from .database import db_cursor
    from .services.secure_settings import protect_setting
    with db_cursor() as cur:
        for key, value in data.items():
            cur.execute(
                "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, protect_setting(key, str(value))),
            )
    return {"message": "Đã lưu cài đặt"}


@app.get("/api/video/serve")
def serve_video(path: str = ""):
    resolved = _resolve_allowed_media_path(path)
    if not resolved:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("Video not found", status_code=404)
    import mimetypes
    mimetypes.init()
    return FileResponse(str(resolved), media_type=mimetypes.guess_type(str(resolved))[0] or "video/mp4")


def _resolve_allowed_media_path(path: str):
    if not path:
        return None
    try:
        candidate = Path(path).expanduser().resolve()
    except Exception:
        return None
    if not candidate.exists() or not candidate.is_file():
        return None

    allowed_roots = [DATA_DIR, DOWNLOADS_DIR, SUBTITLES_DIR, VOICES_DIR, EXPORTS_DIR]
    for root in allowed_roots:
        try:
            candidate.relative_to(Path(root).resolve())
            return candidate
        except ValueError:
            pass

    if is_allowed_path(str(candidate)):
        return candidate

    from .database import db_cursor
    with db_cursor() as cur:
        checks = [
            ("SELECT 1 FROM assets WHERE path=? LIMIT 1", (str(candidate),)),
            ("SELECT 1 FROM downloads WHERE output_path=? LIMIT 1", (str(candidate),)),
            ("SELECT 1 FROM exports WHERE output_path=? LIMIT 1", (str(candidate),)),
            ("SELECT 1 FROM queue_items WHERE input_path=? OR output_path=? LIMIT 1", (str(candidate), str(candidate))),
            ("SELECT 1 FROM clips WHERE source_path=? LIMIT 1", (str(candidate),)),
        ]
        for query, args in checks:
            if cur.execute(query, args).fetchone():
                return candidate
    return None


app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static"), check_dir=False), name="static")


def _frontend_response(path: Path):
    """Serve a frontend asset with cache-busting headers so a re-run of the
    server always picks up the latest edits — no hard refresh required."""
    res = FileResponse(str(path))
    res.headers["Cache-Control"] = "no-store, must-revalidate"
    res.headers["Pragma"] = "no-cache"
    res.headers["Expires"] = "0"
    return res


@app.get("/")
def serve_index():
    return _frontend_response(BASE_DIR / "index.html")


@app.get("/style.css")
def serve_style():
    return _frontend_response(BASE_DIR / "style.css")


@app.get("/app.js")
def serve_js():
    return _frontend_response(BASE_DIR / "app.js")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=HOST, port=PORT, reload=True)
