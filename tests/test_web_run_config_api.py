"""Tests for /api/run_config endpoints (Task 2.5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

import orchestra.web.api as web_api
from orchestra.web.api import app, set_orchestrator
from orchestra.core.run_config import RunConfig
from orchestra.core.task_queue import TaskStatus


@pytest_asyncio.fixture
async def web_client_no_orch():
    """Client with module-level orchestrator forcibly cleared."""
    prev = web_api._orchestrator
    web_api._orchestrator = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    web_api._orchestrator = prev


@pytest_asyncio.fixture
async def web_client_orch(orchestrator):
    """Client wired to a real orchestrator with empty project (no package.json)."""
    set_orchestrator(orchestrator)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    web_api._orchestrator = None


@pytest_asyncio.fixture
async def web_client_orch_pkg_json(orchestrator):
    """Client wired to a real orchestrator with a Vite-style package.json present."""
    pkg = orchestrator.config.project_dir / "package.json"
    pkg.write_text(json.dumps({
        "name": "demo",
        "scripts": {"dev": "vite"},
        "devDependencies": {"vite": "^5.0.0"},
    }))
    set_orchestrator(orchestrator)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    web_api._orchestrator = None


# ── GET /api/run_config ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_run_config_returns_503_when_orch_missing(web_client_no_orch):
    """Without an initialized orchestrator we get the standard 503."""
    resp = await web_client_no_orch.get("/api/run_config")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_get_run_config_returns_404_when_unset(web_client_orch):
    resp = await web_client_orch.get("/api/run_config")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_run_config_returns_saved(web_client_orch, orchestrator):
    cfg = RunConfig(
        command="npm run dev",
        ready_signal="Ready in",
        base_url="http://localhost:5173",
        startup_timeout=30,
        discovered_by="user_input",
    )
    await orchestrator.context.save_run_config(cfg)

    resp = await web_client_orch.get("/api/run_config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["command"] == "npm run dev"
    assert data["ready_signal"] == "Ready in"
    assert data["base_url"] == "http://localhost:5173"
    assert data["startup_timeout"] == 30


# ── POST /api/run_config ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_run_config_saves(web_client_orch, orchestrator):
    payload = {
        "command": "npm run dev",
        "ready_signal": "Local:",
        "base_url": "http://localhost:5173",
        "startup_timeout": 45,
    }
    resp = await web_client_orch.post("/api/run_config", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"status": "saved"}

    # And verify persistence
    saved = await orchestrator.context.get_run_config()
    assert saved is not None
    assert saved.command == "npm run dev"
    assert saved.ready_signal == "Local:"
    assert saved.base_url == "http://localhost:5173"
    assert saved.startup_timeout == 45
    assert saved.discovered_by == "user_input"


# ── GET /api/run_config/detect ──────────────────────────────────────

@pytest.mark.asyncio
async def test_detect_run_config_404_when_nothing(web_client_orch):
    resp = await web_client_orch.get("/api/run_config/detect")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_detect_run_config(web_client_orch_pkg_json):
    resp = await web_client_orch_pkg_json.get("/api/run_config/detect")
    assert resp.status_code == 200
    data = resp.json()
    assert data["command"] == "npm run dev"
    # vite-style guesses
    assert data["base_url"] == "http://localhost:5173"
    assert data["ready_signal"] == "Local:"
    assert data["discovered_by"] == "heuristic"


# ── POST /api/run_config/test ───────────────────────────────────────

@pytest.mark.asyncio
async def test_test_run_config_starts_and_stops(web_client_orch, orchestrator):
    """Spawn a python process that emits a ready signal, then verify the endpoint
    starts it, sees the signal, stops the process, and reports ok=True."""
    # Pre-existing config so we can check last_test_* gets stamped
    initial = RunConfig(
        command="placeholder",
        ready_signal=None,
        discovered_by="user_input",
    )
    await orchestrator.context.save_run_config(initial)

    payload = {
        "command": (
            "python -c \"import time; print('READY-MARKER', flush=True); "
            "time.sleep(30)\""
        ),
        "ready_signal": "READY-MARKER",
        "base_url": "http://localhost:9999",
        "startup_timeout": 10,
    }
    resp = await web_client_orch.post("/api/run_config/test", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True

    # last_test_* on the existing config should be stamped
    saved = await orchestrator.context.get_run_config()
    assert saved is not None
    assert saved.last_test_ok is True
    assert saved.last_test_at is not None


@pytest.mark.asyncio
async def test_test_run_config_failure_reports_error(web_client_orch, orchestrator):
    """If the dev server never emits the ready signal, we should get ok=false
    plus a log_path, and last_test_ok flips to false on the existing config."""
    initial = RunConfig(
        command="placeholder",
        ready_signal=None,
        discovered_by="user_input",
    )
    await orchestrator.context.save_run_config(initial)

    payload = {
        "command": "python -c \"import time; time.sleep(30)\"",
        "ready_signal": "NEVER_APPEARS",
        "base_url": "http://localhost:9999",
        "startup_timeout": 1,
    }
    resp = await web_client_orch.post("/api/run_config/test", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "error" in data
    assert "log_path" in data

    saved = await orchestrator.context.get_run_config()
    assert saved is not None
    assert saved.last_test_ok is False
    assert saved.last_test_at is not None


# ── POST /api/tasks/{task_id}/retry (Task 4.3) ──────────────────────

@pytest.mark.asyncio
async def test_retry_endpoint_400_for_non_failed_task(web_client_orch, orchestrator):
    """Endpoint should reject retries on tasks that are not in FAILED state."""
    await orchestrator.task_queue.add_requirement("r1", "x")
    await orchestrator.task_queue.add_proposal(
        "p1", "r1", features=[{"id": "ta", "title": "A"}]
    )
    await orchestrator.task_queue.add_task(
        "ta", title="A", priority=0, depends_on=[], requirement_id="r1"
    )
    # Task is in IDEA (not FAILED) — retry should 400
    resp = await web_client_orch.post("/api/tasks/ta/retry")
    assert resp.status_code == 400
    assert "FAILED" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_retry_endpoint_succeeds_on_failed_task(web_client_orch, orchestrator):
    """Endpoint should move a FAILED task back to ASSIGNED and clear fail_reason."""
    await orchestrator.task_queue.add_requirement("r1", "x")
    await orchestrator.task_queue.add_proposal(
        "p1", "r1", features=[{"id": "ta", "title": "A"}]
    )
    await orchestrator.task_queue.add_task(
        "ta", title="A", priority=0, depends_on=[], requirement_id="r1"
    )
    # Force into FAILED state so retry is permitted
    await orchestrator.task_queue.transition(
        "ta", TaskStatus.FAILED, fail_reason="boom"
    )

    resp = await web_client_orch.post("/api/tasks/ta/retry")
    assert resp.status_code == 200
    assert resp.json() == {"status": "retrying"}

    t = await orchestrator.task_queue.get_task("ta")
    assert t.status == TaskStatus.ASSIGNED
    assert t.fail_reason is None


@pytest.mark.asyncio
async def test_retry_endpoint_503_when_orch_missing(web_client_no_orch):
    resp = await web_client_no_orch.post("/api/tasks/whatever/retry")
    assert resp.status_code == 503
