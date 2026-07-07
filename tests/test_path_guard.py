"""Smoke tests for the centralised path allow-list.

These tests verify the four behaviours the v2 review called out as
critical / major:

* uploads land inside the data tree even when the filename is hostile;
* folder-listing rejects paths outside the data tree;
* ``open_folder`` rejects non-directory and executable-extension targets;
* downloads-rooted imports cannot be tricked into reading arbitrary files
  via the legacy ``content-as-path`` bug.
"""
from pathlib import Path

import pytest

from backend.services import path_guard


def test_safe_filename_strips_traversal(sandbox_data):
    assert path_guard.safe_filename("..\\..\\Windows\\evil.exe") == "evil.exe"
    assert path_guard.safe_filename("foo/../bar") == "bar"
    assert path_guard.safe_filename("/etc/passwd") == "passwd"
    assert path_guard.safe_filename("\x00name.exe") == "name.exe"


def test_safe_filename_rejects_dot_dot(sandbox_data):
    with pytest.raises(path_guard.PathGuardError):
        path_guard.safe_filename(".")
    with pytest.raises(path_guard.PathGuardError):
        path_guard.safe_filename("..")


def test_safe_filename_sanitises_forbidden_chars(sandbox_data):
    cleaned = path_guard.safe_filename("bad:name?with*chars<>|.mp3")
    assert ":" not in cleaned
    assert "?" not in cleaned
    assert "*" not in cleaned
    assert "<" not in cleaned
    assert ">" not in cleaned
    assert "|" not in cleaned
    assert cleaned.endswith(".mp3")


def test_safe_inside_data_allows_inside_root(sandbox_data):
    inside = sandbox_data / "downloads" / "song.mp3"
    inside.write_bytes(b"fake")
    resolved = path_guard.safe_inside_data(str(inside), require_exists=True)
    assert resolved == inside.resolve()


def test_safe_inside_data_rejects_outside_root(sandbox_data, tmp_path):
    outside = tmp_path / "evil.txt"
    outside.write_text("nope")
    with pytest.raises(path_guard.PathGuardError):
        path_guard.safe_inside_data(str(outside), require_exists=True)


def test_safe_inside_data_rejects_traversal_escape(sandbox_data, tmp_path):
    """A filename like ``..\\..\\evil.txt`` must not slip past the guard."""
    base = sandbox_data / "downloads" / "good.txt"
    base.write_text("ok")
    sneaky = str(base) + "/../../../etc/passwd"
    with pytest.raises(path_guard.PathGuardError):
        path_guard.safe_inside_data(sneaky, require_exists=False)


def test_safe_join_rejects_dot_dot(sandbox_data):
    base = sandbox_data / "downloads"
    with pytest.raises(path_guard.PathGuardError):
        path_guard.safe_join(base, "..", field="upload")


def test_safe_join_writes_inside_base(sandbox_data):
    base = sandbox_data / "downloads"
    target = path_guard.safe_join(base, "user_music.mp3",
                                  field="upload target")
    assert str(target).startswith(str(base.resolve()))
    assert target.name == "user_music.mp3"


def test_safe_folder_to_open_rejects_file(sandbox_data):
    f = sandbox_data / "downloads" / "song.mp3"
    f.write_bytes(b"x")
    with pytest.raises(path_guard.PathGuardError):
        path_guard.safe_folder_to_open(str(f))


def test_safe_folder_to_open_rejects_executable_ext(sandbox_data):
    """A path whose basename ends in a known executable extension must not
    be passed to ``os.startfile`` (RCE risk)."""
    f = sandbox_data / "downloads" / "evil.exe"
    f.write_bytes(b"MZ")
    with pytest.raises(path_guard.PathGuardError):
        path_guard.safe_folder_to_open(str(f))


def test_safe_folder_to_open_rejects_shortcut(sandbox_data):
    f = sandbox_data / "downloads" / "evil.lnk"
    f.write_bytes(b"x")
    with pytest.raises(path_guard.PathGuardError):
        path_guard.safe_folder_to_open(str(f))


def test_safe_folder_to_open_accepts_data_dir(sandbox_data):
    target = path_guard.safe_folder_to_open(str(sandbox_data / "downloads"))
    assert target.is_dir()


def test_extra_roots_can_be_added_and_cleared(sandbox_data, tmp_path):
    user_picked = tmp_path / "user_picked"
    user_picked.mkdir()
    path_guard.add_extra_root(str(user_picked))
    try:
        resolved = path_guard.safe_inside_data(
            str(user_picked / "anything.mp4"),
            extra_roots=[str(user_picked)],
            require_exists=False,
        )
        assert resolved.parent == user_picked.resolve()
    finally:
        path_guard.clear_extra_roots()
    # Once cleared, the extra root is no longer allowed.
    with pytest.raises(path_guard.PathGuardError):
        path_guard.safe_inside_data(
            str(user_picked / "anything.mp4"), require_exists=False,
        )


def test_safe_media_input_accepts_media_extra_root(sandbox_data, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    video = media_dir / "clip.mp4"
    video.write_bytes(b"fake video")

    resolved = path_guard.safe_media_input(
        str(video),
        extra_roots=[str(media_dir)],
    )

    assert resolved == video.resolve()


def test_safe_media_input_rejects_non_media_extension(sandbox_data, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    payload = media_dir / "notes.txt"
    payload.write_text("not media")

    with pytest.raises(path_guard.PathGuardError):
        path_guard.safe_media_input(
            str(payload),
            extra_roots=[str(media_dir)],
        )


def test_safe_media_input_accepts_explicitly_allowed_browse_path(sandbox_data, tmp_path):
    from backend.services.path_allowlist import allow_path

    media_dir = tmp_path / "picked"
    media_dir.mkdir()
    video = media_dir / "clip.mkv"
    video.write_bytes(b"fake video")

    allow_path(str(video))

    assert path_guard.safe_media_input(str(video)) == video.resolve()
