from __future__ import annotations
import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional


@dataclass
class RunConfig:
    command: str
    ready_signal: Optional[str] = None
    base_url: str = "http://localhost:3000"
    startup_timeout: int = 60
    discovered_at: float = field(default_factory=time.time)
    discovered_by: str = "user_input"  # "heuristic" | "user_input"
    last_test_at: Optional[float] = None
    last_test_ok: Optional[bool] = None


def save_run_config(cfg: RunConfig, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(cfg), indent=2))


def load_run_config(target: Path) -> Optional[RunConfig]:
    if not target.exists():
        return None
    data = json.loads(target.read_text())
    return RunConfig(**data)


def detect_run_config(project_root: Path) -> Optional[RunConfig]:
    pkg = project_root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
        except json.JSONDecodeError:
            return None
        scripts = data.get("scripts", {})
        all_deps = {**data.get("dependencies", {}),
                    **data.get("devDependencies", {})}
        for key in ("dev", "start", "serve"):
            if key in scripts:
                ready = _guess_ready_signal(all_deps)
                base = _guess_base_url(all_deps)
                return RunConfig(
                    command=f"npm run {key}",
                    ready_signal=ready,
                    base_url=base,
                    discovered_by="heuristic",
                )
    pyproj = project_root / "pyproject.toml"
    if pyproj.exists():
        return _detect_python_run_config(pyproj)
    return None


def _guess_ready_signal(deps: dict) -> Optional[str]:
    if "next" in deps:
        return "Ready in"
    if "vite" in deps:
        return "Local:"
    if "react-scripts" in deps:
        return "Compiled successfully"
    return None


def _guess_base_url(deps: dict) -> str:
    if "vite" in deps:
        return "http://localhost:5173"
    return "http://localhost:3000"


def _detect_python_run_config(pyproj: Path) -> Optional[RunConfig]:
    """Best-effort. CLI-style projects: no ready_signal, fallback to ping."""
    try:
        import tomllib
    except ImportError:
        return None
    data = tomllib.loads(pyproj.read_text())
    scripts = data.get("project", {}).get("scripts", {})
    if not scripts:
        return None
    name = next(iter(scripts.keys()))
    return RunConfig(
        command=name,
        ready_signal=None,
        discovered_by="heuristic",
    )
