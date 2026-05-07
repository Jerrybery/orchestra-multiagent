"""Shared fixtures for Orchestra tests."""

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from orchestra.core.task_queue import TaskQueue
from orchestra.core.context_manager import ContextManager
from orchestra.core.worktree_manager import WorktreeManager
from orchestra.core.orchestrator import Orchestrator, OrchestraConfig


FIXTURE_DIR = Path(__file__).parent / "fixtures"
FAKE_CLAUDE = str(FIXTURE_DIR / "fake_claude.py")


@pytest.fixture
def tmp_dir(tmp_path):
    """A temporary directory that gets cleaned up."""
    return tmp_path


@pytest.fixture
def project_dir(tmp_dir):
    """A temp directory to act as the project repo."""
    d = tmp_dir / "project"
    d.mkdir()
    return d


@pytest.fixture
def orchestra_dir(tmp_dir):
    """A temp directory for .orchestra/."""
    d = tmp_dir / ".orchestra"
    d.mkdir()
    return d


@pytest_asyncio.fixture
async def task_queue(orchestra_dir):
    """An initialized TaskQueue."""
    tq = TaskQueue(orchestra_dir / "tasks.db")
    await tq.init()
    yield tq
    await tq.close()


@pytest.fixture
def context_mgr(orchestra_dir):
    """An initialized ContextManager."""
    cm = ContextManager(orchestra_dir)
    cm.init()
    return cm


@pytest_asyncio.fixture
async def git_repo(project_dir):
    """A temp git repo with an initial commit."""
    subprocess.run(["git", "init", str(project_dir)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(project_dir), "config", "user.email", "test@test.com"], capture_output=True)
    subprocess.run(["git", "-C", str(project_dir), "config", "user.name", "Test"], capture_output=True)
    readme = project_dir / "README.md"
    readme.write_text("# Test Project\n")
    subprocess.run(["git", "-C", str(project_dir), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(project_dir), "commit", "-m", "Initial commit"], capture_output=True, check=True)
    return project_dir


@pytest_asyncio.fixture
async def worktree_mgr(git_repo, orchestra_dir):
    """A WorktreeManager attached to a real git repo."""
    wt_dir = orchestra_dir / "worktrees"
    wt_dir.mkdir(parents=True, exist_ok=True)
    return WorktreeManager(git_repo, wt_dir)


@pytest.fixture
def orchestra_config(git_repo, orchestra_dir):
    return OrchestraConfig(
        project_dir=git_repo,
        orchestra_dir=orchestra_dir,
        max_fr=2,
        max_fi=1,
        max_hl=1,
        claude_cmd="echo",  # mock: just echo the prompt
    )


@pytest_asyncio.fixture
async def orchestrator(orchestra_config):
    orch = Orchestrator(orchestra_config)
    await orch.init()
    yield orch
    await orch.close()


@pytest.fixture
def fake_claude_script(tmp_path):
    """Write a stream-json script and yield its path."""
    def _write(events: list[dict]) -> Path:
        p = tmp_path / "claude_script.jsonl"
        with p.open("w") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
        return p
    return _write


@pytest.fixture
def fake_claude_env(monkeypatch, fake_claude_script):
    """Set FAKE_CLAUDE_SCRIPT and return a function to install a script."""
    def _install(events, exit_code=0, resume_id=None):
        path = fake_claude_script(events)
        monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(path))
        monkeypatch.setenv("FAKE_CLAUDE_EXIT", str(exit_code))
        if resume_id:
            monkeypatch.setenv("FAKE_CLAUDE_RESUME_ID", resume_id)
    return _install
