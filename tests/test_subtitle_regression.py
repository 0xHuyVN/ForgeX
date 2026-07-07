"""Regression tests for the subtitle import endpoints.

These specifically guard against the two bugs called out in the v2 review:

1. ``import_subtitle`` used to treat the *contents* of an upload as a path
   and would silently substitute the contents of any matching file on disk.
2. ``extract_stream`` had ~30 lines of dead code after a ``return``.

For (1) we use FastAPI's ``TestClient`` against an isolated DB and assert
that uploading an SRT whose decoded text happens to be a valid filesystem
path still stores the SRT bytes verbatim.

For (2) we assert that the endpoint does not execute the dead code path and
returns immediately with the enqueued job.
"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def subtitle_app(monkeypatch, sandbox_data):
    """Build a minimal FastAPI app exposing the subtitle router only.

    The ``sandbox_data`` fixture has already pointed ``database`` at a
    shared in-memory SQLite (so TestClient's worker thread sees the same
    schema as the main thread), rebound ``SUBTITLES_DIR`` etc. on every
    backend module, and initialised the schema. We just need to disable
    foreign keys for the insert-with-project_id=0 tests, and clear the
    per-thread connection cache so the TestClient worker thread picks up
    the shared URI on its first ``get_conn()``.
    """
    from backend.routers import subtitle as subtitle_router
    from backend import database as db_module

    # Tests don't pre-create a project row; turn off the FK so the
    # ``INSERT INTO subtitles (project_id=0)`` does not blow up.
    db_module.get_conn().execute("PRAGMA foreign_keys=OFF")

    # Drop any cached per-thread connection so the TestClient handler-thread
    # opens a fresh one against the shared URI on next get_conn().
    try:
        if hasattr(db_module._local, "conn") and db_module._local.conn is not None:
            db_module._local.conn.close()
    except Exception:
        pass
    db_module._local.conn = None

    app = FastAPI()
    app.include_router(subtitle_router.router, prefix="/api/subtitle")
    return app


def _post_upload(client: TestClient, body: bytes, filename: str):
    return client.post(
        "/api/subtitle/import",
        params={"project_id": 0},
        files={"file": (filename, body, "application/x-subrip")},
    )


def test_import_subtitle_stores_upload_verbatim(subtitle_app, sandbox_data, tmp_path):
    """An upload whose decoded text *looks like* a path must still be stored
    as the upload contents — not the file at that path."""
    # Lay a tempting target on disk: any path the server might try to follow.
    secret = tmp_path / "secret.txt"
    secret.write_text("THIS IS A SECRET", encoding="utf-8")

    # The upload body is the literal string of a real path on the system.
    body = str(secret).encode("utf-8")

    with TestClient(subtitle_app) as client:
        res = _post_upload(client, body, filename="harmless.srt")
        assert res.status_code == 200, res.text

    # The saved file under SUBTITLES_DIR must contain the upload body,
    # NOT the contents of the secret file.
    saved = next((sandbox_data / "subtitles").iterdir())
    text = saved.read_text(encoding="utf-8")
    assert text == str(secret)         # the path string
    assert "THIS IS A SECRET" not in text  # never substituted


def test_import_subtitle_sanitises_traversal_filename(subtitle_app, sandbox_data):
    """A filename containing ``..`` must be sanitised to its basename before
    the file is written — the upload lands inside the sandbox, not outside."""
    body = b"1\n00:00:00,000 --> 00:00:01,000\nhello\n"
    with TestClient(subtitle_app) as client:
        res = _post_upload(client, body, filename="..\\..\\evil.srt")
    assert res.status_code == 200
    saved = list((sandbox_data / "subtitles").iterdir())
    assert len(saved) == 1
    # The traversal was stripped — the file lives as sub_0_evil.srt under
    # SUBTITLES_DIR, NOT anywhere outside the sandbox.
    assert saved[0].name == "sub_0_evil.srt"
    assert saved[0].resolve().is_relative_to(sandbox_data.resolve())


def test_import_subtitle_accepts_normal_srt(subtitle_app, sandbox_data):
    body = b"1\r\n00:00:01,000 --> 00:00:04,000\r\nXin chao cac ban\r\n\r\n"
    with TestClient(subtitle_app) as client:
        res = _post_upload(client, body, filename="ok.srt")
    assert res.status_code == 200
    saved = next((sandbox_data / "subtitles").iterdir())
    assert "Xin" in saved.read_text(encoding="utf-8")


def test_extract_stream_enqueues_only(subtitle_app, monkeypatch):
    """Regression: the previous code had ~30 lines of dead ffmpeg work after
    a ``return``. The endpoint should now ONLY enqueue and return."""
    captured = {}

    def fake_add_queue_item(project_id, kind, input_path, params, priority=None):
        captured["kind"] = kind
        captured["project_id"] = project_id
        captured["input"] = input_path
        return 999

    from backend.services import queue_manager
    monkeypatch.setattr(queue_manager, "add_queue_item", fake_add_queue_item)
    # Re-bind on the router module (it imported the symbol at top of file).
    from backend.routers import subtitle as sr
    monkeypatch.setattr(sr, "add_queue_item", fake_add_queue_item)

    # Make a video file inside the allow-list so the path check passes.
    from backend.config import DOWNLOADS_DIR
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    fake_video = DOWNLOADS_DIR / "fake.mp4"
    fake_video.write_bytes(b"\x00")

    with TestClient(subtitle_app) as client:
        res = client.post("/api/subtitle/extract-stream",
                          json={"path": str(fake_video), "index": 1})
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["id"] == 999
    assert captured["kind"] == "extract_subtitle_stream"
    # The endpoint MUST NOT include any "content" key (the dead path used to
    # read the file and embed it in the response).
    assert "content" not in payload