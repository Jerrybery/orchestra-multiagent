import json
import pytest
from pathlib import Path
from orchestra.core.run_config import RunConfig, load_run_config, save_run_config


def test_run_config_defaults():
    c = RunConfig(command="npm run dev")
    assert c.ready_signal is None
    assert c.base_url == "http://localhost:3000"
    assert c.startup_timeout == 60


def test_save_and_load_roundtrip(tmp_path: Path):
    cfg = RunConfig(
        command="npm run dev",
        ready_signal="Ready in",
        base_url="http://localhost:3000",
        startup_timeout=90,
        discovered_by="user_input",
    )
    target = tmp_path / "run_config.json"
    save_run_config(cfg, target)
    loaded = load_run_config(target)
    assert loaded.command == "npm run dev"
    assert loaded.startup_timeout == 90
    assert loaded.discovered_by == "user_input"


def test_load_returns_none_if_missing(tmp_path: Path):
    assert load_run_config(tmp_path / "nope.json") is None
