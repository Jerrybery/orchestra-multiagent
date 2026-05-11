"""Integration tests for /api/runs + /api/auto-pauses endpoints."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_submit_run_wait_true_returns_completed(orchestrator, fake_claude_env):
    """POST /api/runs with wait=true blocks until the run finishes."""
    from orchestra.web.api import app, set_orchestrator
    set_orchestrator(orchestrator)

    # Add a requirement first
    await orchestrator.task_queue.add_requirement("req-1", "test req")

    # Stub the runner so it returns quickly
    from unittest.mock import AsyncMock
    from orchestra.core.runners.base import RunResult
    runner = orchestrator.manager.runners["hl"]
    runner.run = AsyncMock(return_value=RunResult(
        status="succeeded", session_id="s1",
        result_snapshot={"features": [], "summary": "test"},
        error_message=None,
    ))

    client = TestClient(app)
    response = client.post("/api/runs", json={
        "role": "hl", "target_id": "req-1", "mode": "manual", "wait": True,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "succeeded"
    assert data["result_snapshot"]["summary"] == "test"


@pytest.mark.asyncio
async def test_list_runs(orchestrator):
    """GET /api/runs returns runs in DESC time order."""
    from orchestra.web.api import app, set_orchestrator
    set_orchestrator(orchestrator)
    await orchestrator.task_queue.add_agent_run(
        role="hl", target_kind="requirement", target_id="req-x",
        mode="manual", log_path="/tmp/x",
    )

    client = TestClient(app)
    response = client.get("/api/runs")
    assert response.status_code == 200
    runs = response.json()
    assert len(runs) >= 1
    assert runs[0]["role"] == "hl"


@pytest.mark.asyncio
async def test_list_auto_pauses_empty(orchestrator):
    from orchestra.web.api import app, set_orchestrator
    set_orchestrator(orchestrator)
    client = TestClient(app)
    response = client.get("/api/auto-pauses")
    assert response.status_code == 200
    assert response.json() == []
