"""Phase 2: Requirement submission & HL decomposition tests.

Validates:
- Requirement storage (including duplicate handling)
- Proposal creation from HL output
- Proposal listing and retrieval
- ORCHESTRA_RESULT parsing
"""

import pytest
import pytest_asyncio

from orchestra.core.task_queue import TaskQueue
from orchestra.core.orchestrator import _parse_agent_result


class TestRequirementStorage:
    """Requirements DB operations."""

    @pytest.mark.asyncio
    async def test_add_requirement(self, task_queue):
        req = await task_queue.add_requirement("req-001", "Build a game")
        assert req.id == "req-001"
        assert req.content == "Build a game"

    @pytest.mark.asyncio
    async def test_get_requirement(self, task_queue):
        await task_queue.add_requirement("req-001", "Build a game")
        req = await task_queue.get_requirement("req-001")
        assert req is not None
        assert req.content == "Build a game"

    @pytest.mark.asyncio
    async def test_get_nonexistent_requirement(self, task_queue):
        req = await task_queue.get_requirement("req-missing")
        assert req is None

    @pytest.mark.asyncio
    async def test_duplicate_requirement_id_raises(self, task_queue):
        """Same ID should raise IntegrityError — this is the bug we need to fix."""
        await task_queue.add_requirement("req-dup", "Content A")
        with pytest.raises(Exception):  # IntegrityError
            await task_queue.add_requirement("req-dup", "Content A")

    @pytest.mark.asyncio
    async def test_list_all_requirements(self, task_queue):
        await task_queue.add_requirement("req-001", "First")
        await task_queue.add_requirement("req-002", "Second")
        reqs = await task_queue.get_all_requirements()
        assert len(reqs) == 2


class TestProposalStorage:
    """Proposal CRUD operations."""

    @pytest.mark.asyncio
    async def test_add_proposal(self, task_queue):
        await task_queue.add_requirement("req-001", "Build a game")
        features = [
            {"id": "feat-001", "title": "Player movement", "depends_on": [], "priority": 10},
            {"id": "feat-002", "title": "Collision", "depends_on": ["feat-001"], "priority": 5},
        ]
        prop = await task_queue.add_proposal("prop-001", "req-001", features)
        assert prop.status == "pending"
        assert len(prop.features) == 2

    @pytest.mark.asyncio
    async def test_get_proposal(self, task_queue):
        await task_queue.add_requirement("req-001", "Build a game")
        await task_queue.add_proposal("prop-001", "req-001", [{"id": "feat-001", "title": "Test"}])
        prop = await task_queue.get_proposal("prop-001")
        assert prop is not None
        assert prop.features[0]["title"] == "Test"

    @pytest.mark.asyncio
    async def test_list_pending_proposals(self, task_queue):
        await task_queue.add_requirement("req-001", "Build a game")
        await task_queue.add_proposal("prop-001", "req-001", [])
        await task_queue.add_proposal("prop-002", "req-001", [])
        await task_queue.update_proposal_status("prop-002", "approved")

        pending = await task_queue.get_proposals(status="pending")
        assert len(pending) == 1
        assert pending[0].id == "prop-001"

    @pytest.mark.asyncio
    async def test_update_proposal_status(self, task_queue):
        await task_queue.add_requirement("req-001", "Build a game")
        await task_queue.add_proposal("prop-001", "req-001", [])
        await task_queue.update_proposal_status("prop-001", "rejected")
        prop = await task_queue.get_proposal("prop-001")
        assert prop.status == "rejected"


class TestResultParsing:
    """ORCHESTRA_RESULT extraction from agent output."""

    def test_parse_valid_result(self):
        output = "Some output\nORCHESTRA_RESULT:{\"features\": [{\"id\": \"feat-001\"}]}\nMore output"
        result = _parse_agent_result(output)
        assert result is not None
        assert result["features"][0]["id"] == "feat-001"

    def test_parse_no_result(self):
        result = _parse_agent_result("Just some random output with no result marker")
        assert result is None

    def test_parse_invalid_json(self):
        result = _parse_agent_result("ORCHESTRA_RESULT:{broken json")
        assert result is None

    def test_parse_takes_last_result(self):
        """If multiple ORCHESTRA_RESULT lines, take the last one."""
        output = (
            "ORCHESTRA_RESULT:{\"status\": \"partial\"}\n"
            "ORCHESTRA_RESULT:{\"status\": \"done\"}\n"
        )
        result = _parse_agent_result(output)
        assert result["status"] == "done"

    def test_parse_result_with_surrounding_text(self):
        output = "prefix ORCHESTRA_RESULT:{\"ok\": true} suffix"
        result = _parse_agent_result(output)
        # The regex should capture just the JSON
        assert result is not None
        assert result["ok"] is True
