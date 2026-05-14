# tests/test_claude_config_api.py
"""Tests for /api/claude-config/* endpoints."""

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from orchestra.web.api import app, set_orchestrator
from orchestra.core.orchestrator import Orchestrator, OrchestraConfig
from orchestra.core.claude_config import ClaudeConfigManager, ClaudeProfile
from orchestra.core.vault import Vault


@pytest.fixture
def setup_env(tmp_path):
    """Set up a minimal orchestrator with ClaudeConfigManager and Vault."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    orchestra_dir = project_dir / ".orchestra"
    orchestra_dir.mkdir()

    config_path = project_dir / "orchestra.yaml"
    config_path.write_text(yaml.dump({
        "claude": {
            "command": "echo",
            "active_profile": "dev",
            "profiles": {
                "dev": {"model": "sonnet", "max_turns": 30},
                "prod": {"provider": "bedrock", "model": "opus", "max_turns": 80},
            },
        }
    }))

    config_mgr = ClaudeConfigManager(config_path)
    vault = Vault(orchestra_dir / "vault.enc", tmp_path / "vault.key")

    config = OrchestraConfig(
        project_dir=project_dir,
        orchestra_dir=orchestra_dir,
        claude_cmd="echo",
        claude_config_mgr=config_mgr,
        vault=vault,
    )

    # Mock Orchestrator to avoid git repo requirement
    with patch.object(Orchestrator, '__init__', lambda self, cfg: None):
        orch = Orchestrator.__new__(Orchestrator)
        orch.config = config
        set_orchestrator(orch)

    yield {"config_mgr": config_mgr, "vault": vault, "orch": orch}

    set_orchestrator(None)


@pytest.fixture
def client(setup_env):
    return TestClient(app)


class TestClaudeConfigAPI:
    def test_get_config(self, client):
        res = client.get("/api/claude-config")
        assert res.status_code == 200
        data = res.json()
        assert data["active_profile"] == "dev"
        assert "dev" in data["profiles"]

    def test_list_profiles(self, client):
        res = client.get("/api/claude-config/profiles")
        assert res.status_code == 200
        names = [p["name"] for p in res.json()]
        assert "dev" in names
        assert "prod" in names

    def test_create_profile(self, client):
        res = client.post("/api/claude-config/profiles", json={
            "name": "fast",
            "model": "haiku",
            "max_turns": 10,
        })
        assert res.status_code == 200
        assert res.json()["name"] == "fast"

    def test_create_duplicate_returns_409(self, client):
        res = client.post("/api/claude-config/profiles", json={
            "name": "dev", "model": "sonnet",
        })
        assert res.status_code == 409

    def test_update_profile(self, client):
        res = client.put("/api/claude-config/profiles/dev", json={
            "model": "opus",
        })
        assert res.status_code == 200
        assert res.json()["model"] == "opus"

    def test_delete_profile(self, client):
        res = client.delete("/api/claude-config/profiles/prod")
        assert res.status_code == 204

    def test_delete_active_returns_400(self, client):
        res = client.delete("/api/claude-config/profiles/dev")
        assert res.status_code == 400

    def test_switch_profile(self, client):
        res = client.put("/api/claude-config/active-profile", json={"name": "prod"})
        assert res.status_code == 200
        assert res.json()["name"] == "prod"

    def test_vault_store_and_list(self, client):
        client.post("/api/claude-config/vault", json={
            "name": "my-key", "value": "secret123",
        })
        res = client.get("/api/claude-config/vault")
        assert "my-key" in res.json()

    def test_vault_delete(self, client):
        client.post("/api/claude-config/vault", json={
            "name": "tmp", "value": "x",
        })
        res = client.delete("/api/claude-config/vault/tmp")
        assert res.status_code == 204
