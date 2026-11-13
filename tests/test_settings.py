"""Configuration and master key loading tests (REC-001)."""
import base64

import pytest

from smo.config.settings import load_master_key, settings


def test_load_master_key_from_env(monkeypatch):
    key = b"k" * 32
    monkeypatch.setenv("SMO_MASTER_KEY", base64.urlsafe_b64encode(key).decode())

    assert load_master_key() == key


def test_load_master_key_rejects_wrong_length(monkeypatch):
    monkeypatch.setenv("SMO_MASTER_KEY", base64.urlsafe_b64encode(b"short").decode())

    with pytest.raises(RuntimeError, match="32 bytes"):
        load_master_key()


def test_load_master_key_requires_configuration(monkeypatch):
    monkeypatch.delenv("SMO_MASTER_KEY", raising=False)
    monkeypatch.setattr(settings, "smo_master_key_path", None)
    monkeypatch.setattr(settings, "smo_master_key", None)

    with pytest.raises(RuntimeError, match="not set"):
        load_master_key()


def test_load_master_key_from_file(monkeypatch, tmp_path):
    key = b"f" * 32
    key_file = tmp_path / "master.key"
    key_file.write_bytes(key)
    monkeypatch.delenv("SMO_MASTER_KEY", raising=False)
    monkeypatch.setattr(settings, "smo_master_key", None)
    monkeypatch.setattr(settings, "smo_master_key_path", str(key_file))

    assert settings.uses_key_file is True
    assert load_master_key() == key


def test_uses_key_file_false_when_env_key_present(monkeypatch):
    monkeypatch.setattr(settings, "smo_master_key", "abc")
    monkeypatch.setattr(settings, "smo_master_key_path", "unused")

    assert settings.uses_key_file is False
