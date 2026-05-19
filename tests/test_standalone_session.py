import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio

from orchestra.core.orchestrator import OrchestraConfig
from orchestra.core.standalone import StandaloneSession
from orchestra.core.task_queue import TaskStatus


@pytest.fixture
def config(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "init", str(project)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(project), "config", "user.email", "t@t"], capture_output=True)
    subprocess.run(["git", "-C", str(project), "config", "user.name", "T"], capture_output=True)
    (project / "README.md").write_text("# test\n")
    subprocess.run(["git", "-C", str(project), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(project), "commit", "-m", "init"], capture_output=True, check=True)
    return OrchestraConfig(
        project_dir=project,
        orchestra_dir=tmp_path / ".orchestra",
        claude_cmd="echo",
    )


@pytest.mark.asyncio
async def test_standalone_session_init(config):
    session = StandaloneSession(config)
    await session.init()
    assert session.core is not None
    assert session.core.task_queue is not None
    await session.close()


def test_derive_task_id_from_filename():
    assert StandaloneSession._derive_task_id("feature-auth.md") == "auth"
    assert StandaloneSession._derive_task_id("spec-login-page.md") == "login-page"
    assert StandaloneSession._derive_task_id("feat-dashboard.md") == "dashboard"
    assert StandaloneSession._derive_task_id("my-component.md") == "my-component"
    assert StandaloneSession._derive_task_id("feature-.md") != ""  # fallback


def test_derive_task_id_from_branch():
    assert StandaloneSession._derive_task_id_from_branch("feat/auth-login") == "auth-login"
    assert StandaloneSession._derive_task_id_from_branch("bugfix/42_fix_crash") == "42_fix_crash"
    assert StandaloneSession._derive_task_id_from_branch("my-branch") == "my-branch"


@pytest.mark.asyncio
async def test_run_fr_creates_task_and_spec(config, tmp_path):
    """run_fr should create requirement + task, write spec, fast-forward status."""
    spec_file = tmp_path / "feature-auth.md"
    spec_file.write_text("# Auth Feature\n\nImplement login page.")

    session = StandaloneSession(config)
    await session.init()

    # Mock the manager.submit + wait to avoid spawning real agents
    mock_run_id = 10
    session.core.manager.submit = AsyncMock(return_value=mock_run_id)
    session.core.manager.wait_for_finish = AsyncMock()
    # Mock the agent_run result
    mock_agent_run = MagicMock(
        status="succeeded",
        result_snapshot={"branch": "feat/auth", "files_changed": ["a.py"],
                         "head_commit": "abc", "notes": "done"},
        error_message=None,
    )
    session.core.task_queue.get_agent_run = AsyncMock(return_value=mock_agent_run)

    result = await session.run_fr(spec_file)

    assert result["status"] == "succeeded"
    assert result["task_id"] == "auth"

    # Verify DB records
    task = await session.core.task_queue.get_task("auth")
    assert task is not None
    assert task.spec == "# Auth Feature\n\nImplement login page."

    # Verify spec file written
    spec_path = session.core.context.get_spec_path("auth")
    assert spec_path.exists()

    # Verify manager.submit was called with standalone mode
    session.core.manager.submit.assert_called_once()
    call_kwargs = session.core.manager.submit.call_args.kwargs
    assert call_kwargs.get("mode") == "standalone"

    await session.close()
