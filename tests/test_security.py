"""Immutable security layer tests (REC-002)."""
from dataclasses import FrozenInstanceError

import pytest

from smo.config.security import MutableConfig, mutable_config, security_layer


def test_secure_defaults():
    assert security_layer.dry_run is True
    assert security_layer.human_gate is True
    assert security_layer.budget_per_day_usd > 0
    assert security_layer.mcp_scopes_read == frozenset({"read"})
    assert security_layer.mcp_scopes_content == frozenset({"content"})


def test_security_layer_is_frozen():
    with pytest.raises(FrozenInstanceError):
        security_layer.dry_run = False  # type: ignore[misc]


@pytest.mark.parametrize(
    "key",
    ["dry_run", "human_gate", "budget_per_day_usd", "mcp_scopes_read", "mcp_scopes_admin"],
)
def test_immutable_controls_cannot_be_modified(key):
    with pytest.raises(ValueError, match="Cannot modify immutable control"):
        mutable_config.set(key, False)


def test_mutable_config_accepts_whitelisted_keys():
    config = MutableConfig()

    config.set("brand_voice", {"tone": "friendly"})
    config.set("mcp_enabled", False)

    assert config.brand_voice == {"tone": "friendly"}
    assert config.mcp_enabled is False


def test_mutable_config_rejects_bad_value_type():
    config = MutableConfig()

    with pytest.raises(ValueError, match="Unexpected value type"):
        config.set("brand_voice", "not-a-dict")
