"""Tests for ContextManager RunConfig persistence and dev_server log paths."""

import pytest
from pathlib import Path

from orchestra.core.context_manager import ContextManager
from orchestra.core.run_config import RunConfig


@pytest.mark.asyncio
async def test_save_and_get_run_config(tmp_path: Path):
    cm = ContextManager(tmp_path / ".orchestra")
    cm.init()
    cfg = RunConfig(command="npm run dev", base_url="http://localhost:3000")
    await cm.save_run_config(cfg)
    loaded = await cm.get_run_config()
    assert loaded is not None
    assert loaded.command == "npm run dev"
    assert loaded.base_url == "http://localhost:3000"


@pytest.mark.asyncio
async def test_get_run_config_returns_none_initially(tmp_path: Path):
    cm = ContextManager(tmp_path / ".orchestra")
    cm.init()
    assert await cm.get_run_config() is None


@pytest.mark.asyncio
async def test_dev_server_log_path_per_task(tmp_path: Path):
    cm = ContextManager(tmp_path / ".orchestra")
    cm.init()
    p = cm.get_dev_server_log_path("task-001")
    assert p.name == "task-001.log"
    assert "dev_server_logs" in str(p)
    # Parent directory must be created on demand so callers can append directly.
    assert p.parent.exists()
