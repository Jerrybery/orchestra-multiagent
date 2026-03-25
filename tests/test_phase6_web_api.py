"""Phase 6: Web API endpoint tests.

Validates:
- GET /api/graph returns correct structure
- GET /api/tasks, /api/tasks/{id}
- GET /api/proposals, /api/proposals/{id}
- POST /api/submit
- POST /api/proposals/{id}/review
- POST /api/tasks/{id}/review
- GET /api/branches
- GET /api/agents
- GET /api/summary
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from orchestra.core.orchestrator import Orchestrator, OrchestraConfig
from orchestra.core.task_queue import TaskStatus
from orchestra.web.api import app, set_orchestrator


@pytest_asyncio.fixture
async def web_client(orchestrator):
    """An httpx AsyncClient wired to the FastAPI app with a real orchestrator."""
    set_orchestrator(orchestrator)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestGraphEndpoint:

    @pytest.mark.asyncio
    async def test_empty_graph(self, web_client):
        resp = await web_client.get("/api/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert "branches" in data
        assert "proposals" in data

    @pytest.mark.asyncio
    async def test_graph_with_tasks(self, web_client, orchestrator):
        tq = orchestrator.task_queue
        await tq.add_requirement("req-001", "Build something")
        await tq.add_task("feat-001", "Feature 1", requirement_id="req-001")

        resp = await web_client.get("/api/graph")
        data = resp.json()
        assert len(data["nodes"]) == 2  # 1 requirement + 1 task
        assert len(data["edges"]) == 1  # requirement → task


class TestTaskEndpoints:

    @pytest.mark.asyncio
    async def test_list_tasks_empty(self, web_client):
        resp = await web_client.get("/api/tasks")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_get_task_detail(self, web_client, orchestrator):
        tq = orchestrator.task_queue
        await tq.add_requirement("req-001", "Build something")
        await tq.add_task("feat-001", "Feature 1", requirement_id="req-001")
        orchestrator.context.write_spec("feat-001", "# Spec content")

        resp = await web_client.get("/api/tasks/feat-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "feat-001"
        assert data["spec"] == "# Spec content"
        assert data["requirement"]["content"] == "Build something"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, web_client):
        resp = await web_client.get("/api/tasks/nonexistent")
        assert resp.status_code == 404


class TestProposalEndpoints:

    @pytest.mark.asyncio
    async def test_list_proposals(self, web_client, orchestrator):
        tq = orchestrator.task_queue
        await tq.add_requirement("req-001", "Build something")
        await tq.add_proposal("prop-001", "req-001", [
            {"id": "feat-001", "title": "Test", "depends_on": []}
        ])

        resp = await web_client.get("/api/proposals")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "prop-001"

    @pytest.mark.asyncio
    async def test_get_proposal_detail(self, web_client, orchestrator):
        tq = orchestrator.task_queue
        await tq.add_requirement("req-001", "Build something")
        await tq.add_proposal("prop-001", "req-001", [
            {"id": "feat-001", "title": "Test", "depends_on": []}
        ])
        orchestrator.context.write_spec("feat-001", "# Spec")

        resp = await web_client.get("/api/proposals/prop-001")
        data = resp.json()
        assert data["features"][0]["spec"] == "# Spec"

    @pytest.mark.asyncio
    async def test_approve_proposal_via_api(self, web_client, orchestrator):
        tq = orchestrator.task_queue
        await tq.add_requirement("req-001", "Build something")
        await tq.add_proposal("prop-001", "req-001", [
            {"id": "feat-001", "title": "Feature A", "depends_on": [], "priority": 10},
            {"id": "feat-002", "title": "Feature B", "depends_on": [], "priority": 5},
        ])

        resp = await web_client.post("/api/proposals/prop-001/review", json={
            "action": "approve",
            "feature_ids": ["feat-001"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks_created"] == 1

        # Only feat-001 should exist as a task
        tasks = await tq.get_tasks()
        assert len(tasks) == 1
        assert tasks[0].id == "feat-001"

    @pytest.mark.asyncio
    async def test_reject_proposal_via_api(self, web_client, orchestrator):
        tq = orchestrator.task_queue
        await tq.add_requirement("req-001", "Build something")
        await tq.add_proposal("prop-001", "req-001", [])

        resp = await web_client.post("/api/proposals/prop-001/review", json={
            "action": "reject",
        })
        assert resp.status_code == 200

        prop = await tq.get_proposal("prop-001")
        assert prop.status == "rejected"


class TestTaskReviewEndpoint:

    @pytest.mark.asyncio
    async def test_review_not_in_review_status(self, web_client, orchestrator):
        tq = orchestrator.task_queue
        await tq.add_task("feat-001", "Test")
        resp = await web_client.post("/api/tasks/feat-001/review", json={"action": "accept"})
        assert resp.status_code == 400


class TestMiscEndpoints:

    @pytest.mark.asyncio
    async def test_summary(self, web_client, orchestrator):
        tq = orchestrator.task_queue
        await tq.add_task("feat-001", "A")
        await tq.add_task("feat-002", "B")
        resp = await web_client.get("/api/summary")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    @pytest.mark.asyncio
    async def test_agents_empty(self, web_client):
        resp = await web_client.get("/api/agents")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_branches(self, web_client):
        resp = await web_client.get("/api/branches")
        assert resp.status_code == 200
        # Should have at least one branch from the test git repo
        assert len(resp.json()) >= 1

    @pytest.mark.asyncio
    async def test_index_html(self, web_client):
        resp = await web_client.get("/")
        assert resp.status_code == 200
        assert "Orchestra" in resp.text
