"""Smoke tests for the queue / pipeline dispatcher.

The v2 code review raised pipeline dispatch as a maintainability concern:
* Unknown job kinds must terminate the job (not loop forever or 500).
* The set of known kinds should be enumerable for migration purposes.
* ``add_queue_item`` should reject obviously invalid input early.
"""
import pytest

from backend.services import pipeline_service


def _declared_kinds() -> set[str]:
    """Enumerate the kind strings declared in the big if/elif chain."""
    import re
    src = open(pipeline_service.__file__, "r", encoding="utf-8").read()
    return set(re.findall(r'ptype == ["\']([\w_]+)["\']', src))


def test_pipeline_service_declares_expected_kinds():
    """The dispatcher should at least handle every job kind the v2 review
    enumerated plus the basic media ops."""
    expected = {
        "download", "transcribe", "translate", "tts", "render",
        "tts_text", "process_music", "duck_music", "ffmpeg_command",
        "merge_videos", "split", "ocr_hardsub", "remove_hardsub",
        "scene_detect", "extract_subtitle_stream", "train_voice",
        "publish", "ai_recap", "ai_task", "pipeline", "export_audio",
        "auto_reframe", "dynamic_template", "clone_pipeline",
    }
    actual = _declared_kinds()
    missing = expected - actual
    assert not missing, f"pipeline dispatcher missing kinds: {missing}"


def test_pipeline_dispatch_unknown_kind_marks_failed():
    """An unknown kind must update the job to failed (not loop or raise)."""
    job = {
        "id": 1,
        "type": "totally_made_up_kind_xyz",
        "project_id": 0,
        "input_path": "",
        "params": "{}",
    }
    # The dispatcher marks unknown kinds failed and returns False.
    assert pipeline_service.run_pipeline(job) is False


def test_add_queue_item_persists_and_returns_id(monkeypatch, sandbox_data):
    """``add_queue_item`` should at minimum produce a usable row. We don't
    assert on the kind field because the v2 design is permissive; the
    dispatcher catches unknown kinds via ``run_pipeline`` instead."""
    from backend.services.queue_manager import add_queue_item
    from backend.database import init_db
    init_db()
    item_id = add_queue_item(0, "render", "", {"resolution": "1080p"})
    assert isinstance(item_id, int) and item_id > 0


def test_render_quality_gate_allows_warnings(monkeypatch, tmp_path):
    """Warnings should be visible in logs but should not block export by default."""
    from backend.services import quality_checker

    output = tmp_path / "rendered.mp4"
    output.write_bytes(b"fake")
    logs = []

    monkeypatch.setattr(pipeline_service, "_log", lambda _id, level, msg: logs.append((level, msg)))
    monkeypatch.setattr(
        quality_checker,
        "run_quality_check",
        lambda *args, **kwargs: {
            "ok": True,
            "status": "WARNING",
            "summary": {"errors": 0, "warnings": 1, "infos": 0},
            "issues": [{"severity": "warning", "code": "FPS_LOW", "message": "FPS thap"}],
        },
    )

    result = pipeline_service._run_render_quality_gate(1, 0, str(output), {})

    assert result["status"] == "WARNING"
    assert output.exists()
    assert any("FPS_LOW" in msg for _level, msg in logs)


def test_render_quality_gate_blocks_fail_and_removes_pending_output(monkeypatch, tmp_path):
    """A failed quality check should not leave a pending file ready for export."""
    from backend.services import quality_checker

    output = tmp_path / "rendered.mp4"
    output.write_bytes(b"fake")

    monkeypatch.setattr(pipeline_service, "_log", lambda *_args: None)
    monkeypatch.setattr(
        quality_checker,
        "run_quality_check",
        lambda *args, **kwargs: {
            "ok": False,
            "status": "FAIL",
            "summary": {"errors": 1, "warnings": 0, "infos": 0},
            "issues": [{"severity": "error", "code": "NO_VIDEO_STREAM", "message": "Khong tim thay video stream."}],
        },
    )

    with pytest.raises(RuntimeError, match="Render quality gate failed"):
        pipeline_service._run_render_quality_gate(1, 0, str(output), {})

    assert not output.exists()
