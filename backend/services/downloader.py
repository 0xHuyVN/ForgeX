"""Video downloader — uses the yt-dlp Python API directly.

This avoids the need to ship the ``yt-dlp.exe`` console script in the
PyInstaller bundle and is also more robust (catches errors as Python
exceptions instead of parsing stdout for progress).

For headless / frozen builds, ``yt_dlp`` is imported as a regular Python
package (PyInstaller's ``yt_dlp`` hook bundles the module + all its
dependencies automatically). For the dev ``run.bat`` workflow, it relies
on ``pip install yt-dlp`` having been run against the active venv.
"""
import json
import sys
from pathlib import Path

from ..config import DOWNLOADS_DIR
from ..database import db_cursor
from .path_guard import safe_output_dir


def _publish_download(dl_id: int):
    try:
        from .event_bus import event_bus
        with db_cursor() as cur:
            row = cur.execute("SELECT * FROM downloads WHERE id=?", (dl_id,)).fetchone()
            if row:
                event_bus.publish("download_updated", dict(row))
    except Exception:
        pass


def _format_selector(quality: str) -> str:
    if quality == "best":
        return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    if quality == "1080p":
        return "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]"
    if quality == "720p":
        return "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]"
    if quality == "audio":
        return "bestaudio/best"
    return "best"


def _build_opts(out_dir: Path, quality: str, cookie_file: str | None,
                proxy: str | None) -> dict:
    opts = {
        "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
        "format": _format_selector(quality),
        "restrictfilenames": True,
        "noprogress": False,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }
    if quality == "audio":
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
        }]
    if cookie_file:
        opts["cookiefile"] = cookie_file
    if proxy:
        opts["proxy"] = proxy
    return opts


class _DbProgressHook:
    """yt-dlp progress hook that mirrors the current download row."""

    def __init__(self, dl_id: int):
        self.dl_id = dl_id

    def __call__(self, d: dict):
        if d.get("status") == "downloading":
            pct = d.get("_percent_str") or ""
            try:
                value = float(pct.strip().rstrip("%")) if isinstance(pct, str) else float(pct)
            except (ValueError, TypeError):
                return
            try:
                with db_cursor() as cur:
                    cur.execute("UPDATE downloads SET progress=? WHERE id=?", (value, self.dl_id))
                _publish_download(self.dl_id)
            except Exception:
                pass
        elif d.get("status") == "finished":
            try:
                with db_cursor() as cur:
                    cur.execute("UPDATE downloads SET progress=100 WHERE id=?", (self.dl_id,))
                _publish_download(self.dl_id)
            except Exception:
                pass


def download_video(dl_id: int, url: str, quality: str = "best",
                   cookie_file: str = None, proxy: str = None,
                   output_dir: str = None):
    out_dir = safe_output_dir(output_dir, field="download output_dir") if output_dir and output_dir.strip() else DOWNLOADS_DIR / f"dl_{dl_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        with db_cursor() as cur:
            cur.execute("UPDATE downloads SET status='running', error=NULL WHERE id=?", (dl_id,))
        _publish_download(dl_id)

        from yt_dlp import YoutubeDL
        opts = _build_opts(out_dir, quality, cookie_file, proxy)
        opts["progress_hooks"] = [_DbProgressHook(dl_id)]

        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        # ``extract_info`` returns the downloaded entry's info dict when
        # ``download=True`` and a single video was requested. For playlists
        # it returns a dict with ``entries``; we treat the output dir as
        # the result.
        if isinstance(info, dict) and info.get("_type") == "playlist":
            files = sorted(out_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            out_path = str(files[0]) if files else ""
        else:
            requested = info.get("requested_downloads") or []
            if requested:
                out_path = requested[0].get("filepath") or str(out_dir / ydl.prepare_filename(info))
            else:
                out_path = str(out_dir / ydl.prepare_filename(info))

        with db_cursor() as cur:
            cur.execute(
                "UPDATE downloads SET status='completed', output_path=?, progress=100 WHERE id=?",
                (out_path, dl_id),
            )
        _publish_download(dl_id)

    except Exception as e:
        with db_cursor() as cur:
            cur.execute(
                "UPDATE downloads SET status='failed', error=? WHERE id=?",
                (str(e), dl_id),
            )
        _publish_download(dl_id)
