"""Path confinement tests (REC-006)."""
from pathlib import Path

import pytest

from smo.utils import safeio


def test_sanitize_filename_removes_path_separators():
    assert safeio.sanitize_filename("../../etc/passwd") == "etc_passwd"
    assert safeio.sanitize_filename("..\\..\\windows\\system32") == "windows_system32"


def test_sanitize_filename_handles_empty_result():
    assert safeio.sanitize_filename("///") == "unnamed"


def test_normalize_path_accepts_inside_base(tmp_path):
    base = tmp_path.resolve()

    assert safeio.normalize_path(base, "nested/file.txt") == base / "nested" / "file.txt"


def test_normalize_path_rejects_traversal(tmp_path):
    base = (tmp_path / "base").resolve()
    base.mkdir()

    with pytest.raises(ValueError, match="outside base directory"):
        safeio.normalize_path(base, "../escape.txt")


def test_get_safe_path_enforces_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(safeio, "ensure_base_dir", lambda: None)
    base = (tmp_path / "media").resolve()

    path = safeio.get_safe_path(base, "image.png", allowed_extensions=[".png"])
    assert path.suffix == ".png"

    with pytest.raises(ValueError, match="extension"):
        safeio.get_safe_path(base, "script.exe", allowed_extensions=[".png"])


def test_ensure_safe_write_creates_parents(tmp_path, monkeypatch):
    monkeypatch.setattr(safeio, "ensure_base_dir", lambda: None)
    target = tmp_path / "a" / "b" / "file.txt"

    safeio.ensure_safe_write(target, b"hello")

    assert target.read_bytes() == b"hello"
    assert isinstance(target, Path)
