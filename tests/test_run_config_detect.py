import json
from pathlib import Path
from orchestra.core.run_config import detect_run_config


def test_detect_nextjs(tmp_path: Path):
    pkg = {"scripts": {"dev": "next dev"}, "dependencies": {"next": "14.0.0"}}
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    cfg = detect_run_config(tmp_path)
    assert cfg.command == "npm run dev"
    assert cfg.ready_signal == "Ready in"
    assert cfg.base_url == "http://localhost:3000"
    assert cfg.discovered_by == "heuristic"


def test_detect_vite(tmp_path: Path):
    pkg = {"scripts": {"dev": "vite"}, "devDependencies": {"vite": "5.0.0"}}
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    cfg = detect_run_config(tmp_path)
    assert cfg.ready_signal == "Local:"
    assert cfg.base_url == "http://localhost:5173"


def test_detect_falls_back_when_no_signals(tmp_path: Path):
    pkg = {"scripts": {"start": "node server.js"}}
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    cfg = detect_run_config(tmp_path)
    assert cfg.command == "npm run start"
    assert cfg.ready_signal is None  # Fallback to ping


def test_detect_returns_none_when_nothing(tmp_path: Path):
    assert detect_run_config(tmp_path) is None
