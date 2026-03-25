"""Manages the shared context directory (.orchestra/context/)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# Default content for initial context files
_DEFAULT_ARCHITECTURE = """\
# Architecture Decisions

> This file is maintained by Head Leader. Feature Realizers should read before starting work.

## Project Type
(To be filled by Head Leader)

## Module Layout
(To be filled by Head Leader)

## Key Decisions
(To be filled by Head Leader)
"""

_DEFAULT_CONVENTIONS = """\
# Technical Conventions

> This file is maintained by Head Leader. All agents must follow these conventions.

## Language & Frameworks
(To be filled by Head Leader)

## Coding Style
(To be filled by Head Leader)

## Naming Conventions
(To be filled by Head Leader)
"""

_DEFAULT_GLOSSARY = """\
# Glossary

> Shared terminology. Keep entries consistent across all feature specs and code.
"""


class ContextManager:
    """Manages the shared context store that all agents read from."""

    def __init__(self, orchestra_dir: Path):
        self.orchestra_dir = orchestra_dir
        self.context_dir = orchestra_dir / "context"
        self.specs_dir = self.context_dir / "feature_specs"
        self.contracts_dir = self.context_dir / "api_contracts"
        self.reports_dir = orchestra_dir / "reports"
        self.worktrees_dir = orchestra_dir / "worktrees"
        self.logs_dir = orchestra_dir / "logs"

    def init(self) -> None:
        """Create directory structure and default files."""
        for d in (self.context_dir, self.specs_dir, self.contracts_dir,
                  self.reports_dir, self.worktrees_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

        defaults = {
            self.context_dir / "architecture.md": _DEFAULT_ARCHITECTURE,
            self.context_dir / "conventions.md": _DEFAULT_CONVENTIONS,
            self.context_dir / "glossary.md": _DEFAULT_GLOSSARY,
        }
        for path, content in defaults.items():
            if not path.exists():
                path.write_text(content)

    def get_spec_path(self, task_id: str) -> Path:
        return self.specs_dir / f"{task_id}.md"

    def write_spec(self, task_id: str, content: str) -> Path:
        path = self.get_spec_path(task_id)
        path.write_text(content)
        return path

    def read_spec(self, task_id: str) -> Optional[str]:
        path = self.get_spec_path(task_id)
        return path.read_text() if path.exists() else None

    def get_report_path(self, task_id: str) -> Path:
        return self.reports_dir / f"{task_id}-report.md"

    def read_report(self, task_id: str) -> Optional[str]:
        path = self.get_report_path(task_id)
        return path.read_text() if path.exists() else None

    def get_worktree_path(self, task_id: str) -> Path:
        return self.worktrees_dir / task_id

    def get_log_path(self, agent_id: str) -> Path:
        return self.logs_dir / f"{agent_id}.log"

    def get_agent_env(self, task_id: str, role: str) -> dict[str, str]:
        """Build the path environment dict injected into agent prompts."""
        env = {
            "context_dir": str(self.context_dir),
            "architecture": str(self.context_dir / "architecture.md"),
            "conventions": str(self.context_dir / "conventions.md"),
            "glossary": str(self.context_dir / "glossary.md"),
            "api_contracts_dir": str(self.contracts_dir),
            "feature_specs_dir": str(self.specs_dir),
        }

        if role in ("feature_realizer", "feature_interpreter"):
            env["spec_file"] = str(self.get_spec_path(task_id))
            env["workspace"] = str(self.get_worktree_path(task_id))

        if role == "feature_interpreter":
            env["report_file"] = str(self.get_report_path(task_id))

        return env
