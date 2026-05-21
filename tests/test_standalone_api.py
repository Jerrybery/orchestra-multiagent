"""Tests for standalone API endpoints."""
import subprocess
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from orchestra.web.api import app, set_orchestrator
from orchestra.core.orchestrator import Orchestrator, OrchestraConfig
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


@pytest_asyncio.fixture
async def client(config):
    orch = Orchestrator(config)
    await orch.init()
    set_orchestrator(orch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    set_orchestrator(None)
    await orch.close()


@pytest.mark.asyncio
async def test_standalone_fr_creates_task_and_starts_run(client, config):
    resp = await client.post("/api/standalone/fr", json={
        "spec": "# Login Page\n\nBuild a login page.",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert data["task_id"] == "login-page"
    assert "run_id" in data


@pytest.mark.asyncio
async def test_standalone_fr_with_custom_task_id(client):
    resp = await client.post("/api/standalone/fr", json={
        "spec": "# Auth\n\nAuth feature.",
        "task_id": "my-custom-id",
    })
    assert resp.status_code == 200
    assert resp.json()["task_id"] == "my-custom-id"


@pytest.mark.asyncio
async def test_standalone_fi_requires_branch_or_pr(client):
    resp = await client.post("/api/standalone/fi", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_standalone_fi_with_branch(client, config):
    # Create a feature branch first
    project = config.project_dir
    subprocess.run(["git", "-C", str(project), "checkout", "-b", "feat/test-review"], capture_output=True, check=True)
    (project / "feature.py").write_text("# new feature\n")
    subprocess.run(["git", "-C", str(project), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(project), "commit", "-m", "add feature"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(project), "checkout", "-"], capture_output=True, check=True)

    resp = await client.post("/api/standalone/fi", json={
        "branch": "feat/test-review",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert data["task_id"] == "test-review"
    assert data["branch"] == "feat/test-review"
