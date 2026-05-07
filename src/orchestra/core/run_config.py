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
