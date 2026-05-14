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
    """An initialized TaskQueue backed by ORM."""
    from orchestra.core.db.engine import create_db_engine, init_db
    engine, sf = create_db_engine(orchestra_dir=orchestra_dir)
    await init_db(engine)
    tq = TaskQueue(sf)
    await tq.init()
    yield tq
    await tq.close()
    await engine.dispose()


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


@pytest_asyncio.fixture
async def fr_task_implemented(orchestrator, tmp_path):
    """A task in IMPLEMENTED state with a real git worktree.

    Used by FI tests: the worktree has an initial commit on `main` so that
    `_git_rev_parse_head` and `_git_status_porcelain` return real values.
    The orchestrator's `context.get_worktree_path` is monkeypatched to point
    at this temp worktree so FI runs against it.
    """
    from orchestra.core.task_queue import TaskStatus

    await orchestrator.task_queue.add_requirement("r1", "x")
    await orchestrator.task_queue.add_proposal(
        "p1", "r1", features=[{"id": "ti", "title": "X"}]
    )
    await orchestrator.task_queue.add_task(
        "ti", title="X", priority=0, depends_on=[], requirement_id="r1"
    )
    await orchestrator.task_queue.update_proposal_status("p1", "approved")
    await orchestrator.task_queue.transition("ti", TaskStatus.PLANNING)
    await orchestrator.task_queue.transition("ti", TaskStatus.PLANNED)
    await orchestrator.task_queue.transition("ti", TaskStatus.ASSIGNED)
    await orchestrator.task_queue.transition("ti", TaskStatus.IN_PROGRESS)
    await orchestrator.task_queue.transition("ti", TaskStatus.IMPLEMENTED)

    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=wt)
    (wt / "x.py").write_text("# initial")
    subprocess.check_call(["git", "add", "."], cwd=wt)
    subprocess.check_call(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-m", "init"],
        cwd=wt,
    )
    orchestrator.context.get_worktree_path = lambda tid: wt
    return await orchestrator.task_queue.get_task("ti")


@pytest_asyncio.fixture
async def run_config_set(orchestrator):
    """Persist a RunConfig that produces a fake dev server which:
    - prints the ready signal immediately, then sleeps so it stays up,
    - allows DevServer.start() to return quickly via ready_signal matching.
    """
    from orchestra.core.run_config import RunConfig
    cfg = RunConfig(
        command='python -c "import time; print(\'Ready in 0.1s\', flush=True); time.sleep(30)"',
        ready_signal="Ready in",
        base_url="http://localhost:9999",
        startup_timeout=5,
    )
    await orchestrator.context.save_run_config(cfg)
