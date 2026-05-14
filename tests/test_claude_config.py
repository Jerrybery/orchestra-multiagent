# tests/test_claude_config.py
"""Tests for ClaudeConfig data model and manager."""

import pytest
import yaml
from pathlib import Path
from orchestra.core.claude_config import ClaudeProfile, ClaudeConfig, ClaudeConfigManager


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "orchestra.yaml"


@pytest.fixture
def manager(config_path):
    return ClaudeConfigManager(config_path)


class TestClaudeProfile:
    def test_defaults(self):
        p = ClaudeProfile(name="test")
        assert p.provider == "anthropic"
        assert p.model == "sonnet"
        assert p.max_turns == 50
        assert p.env == {}
        assert p.mcp_servers == {}
        assert p.role_models == {}

    def test_to_dict_roundtrip(self):
        p = ClaudeProfile(name="x", model="opus", env={"K": "V"})
        d = p.to_dict()
        p2 = ClaudeProfile.from_dict("x", d)
        assert p2.model == "opus"
        assert p2.env == {"K": "V"}


class TestClaudeConfigManager:
    def test_load_empty_creates_default_profile(self, manager):
        config = manager.config
        assert "default" in config.profiles
        assert config.active_profile == "default"

    def test_load_legacy_format_migrates(self, config_path):
        config_path.write_text(yaml.dump({
            "claude": {"command": "my-claude", "model": "opus", "max_turns": 80}
        }))
        mgr = ClaudeConfigManager(config_path)
        assert mgr.config.command == "my-claude"
        p = mgr.active_profile()
        assert p.model == "opus"
        assert p.max_turns == 80

    def test_load_new_format(self, config_path):
        config_path.write_text(yaml.dump({
            "claude": {
                "command": "claude",
                "active_profile": "prod",
                "profiles": {
                    "prod": {
                        "provider": "bedrock",
                        "model": "opus",
                        "max_turns": 80,
                        "env": {"AWS_REGION": "us-east-1"},
                        "role_models": {"head_leader": "opus"},
                    }
                }
            }
        }))
        mgr = ClaudeConfigManager(config_path)
        assert mgr.config.active_profile == "prod"
        p = mgr.active_profile()
        assert p.provider == "bedrock"
        assert p.role_models == {"head_leader": "opus"}

    def test_create_profile(self, manager):
        manager.create_profile("fast", ClaudeProfile(
            name="fast", model="haiku", max_turns=10,
        ))
        assert "fast" in manager.config.profiles

    def test_create_duplicate_raises(self, manager):
        with pytest.raises(ValueError, match="already exists"):
            manager.create_profile("default", ClaudeProfile(name="default"))

    def test_update_profile(self, manager):
        manager.update_profile("default", {"model": "opus"})
        assert manager.config.profiles["default"].model == "opus"

    def test_update_nonexistent_raises(self, manager):
        with pytest.raises(KeyError):
            manager.update_profile("nope", {"model": "opus"})

    def test_delete_profile(self, manager):
        manager.create_profile("temp", ClaudeProfile(name="temp"))
        manager.delete_profile("temp")
        assert "temp" not in manager.config.profiles

    def test_delete_active_raises(self, manager):
        with pytest.raises(ValueError, match="Cannot delete active"):
            manager.delete_profile("default")

    def test_switch_profile(self, manager):
        manager.create_profile("alt", ClaudeProfile(name="alt", model="opus"))
        manager.switch_profile("alt")
        assert manager.config.active_profile == "alt"
        assert manager.active_profile().model == "opus"

    def test_switch_nonexistent_raises(self, manager):
        with pytest.raises(KeyError):
            manager.switch_profile("nope")

    def test_save_roundtrip(self, config_path):
        mgr = ClaudeConfigManager(config_path)
        mgr.create_profile("new", ClaudeProfile(
            name="new", provider="bedrock", model="opus",
        ))
        mgr.save()
        mgr2 = ClaudeConfigManager(config_path)
        assert "new" in mgr2.config.profiles
        assert mgr2.config.profiles["new"].provider == "bedrock"

    def test_save_preserves_non_claude_sections(self, config_path):
        config_path.write_text(yaml.dump({
            "concurrency": {"feature_realizer": 4},
            "claude": {"model": "sonnet"},
        }))
        mgr = ClaudeConfigManager(config_path)
        mgr.save()
        raw = yaml.safe_load(config_path.read_text())
        assert raw["concurrency"]["feature_realizer"] == 4
