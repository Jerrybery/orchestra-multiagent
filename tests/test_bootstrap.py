import subprocess
from pathlib import Path
import pytest
import pytest_asyncio

from orchestra.core.orchestrator import OrchestraConfig, Orchestrator
from orchestra.core.bootstrap import CoreServices, build_core


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


def test_build_core_returns_core_services(config):
    core = build_core(config)
    assert isinstance(core, CoreServices)
    assert core.task_queue is not None
    assert core.context is not None
    assert core.worktree is not None
    assert core.spawner is not None
    assert core.manager is not None
    assert "hl" in core.runners
    assert "fr" in core.runners
    assert "fi" in core.runners
    assert "pl" in core.runners


def test_build_core_prompt_loader_works(config):
    core = build_core(config)
    core.context.init()
    prompt = core.prompt_loader("head_leader")
    assert len(prompt) > 0


@pytest_asyncio.fixture
async def orchestrator_via_bootstrap(config):
    orch = Orchestrator(config)
    await orch.init()
    yield orch
    await orch.close()


@pytest.mark.asyncio
async def test_orchestrator_still_works_after_refactor(orchestrator_via_bootstrap):
    orch = orchestrator_via_bootstrap
    req = await orch.task_queue.add_requirement("r1", "test content")
    assert req.id == "r1"
    fetched = await orch.task_queue.get_requirement("r1")
    assert fetched.content == "test content"
    assert orch.task_queue is not None
    assert orch.context is not None
    assert orch.worktree is not None
    assert orch.spawner is not None
    assert orch.manager is not None
    assert orch.auto_driver is not None
