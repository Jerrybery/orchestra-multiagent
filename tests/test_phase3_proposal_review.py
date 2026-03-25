"""Phase 3: Proposal review tests.

Validates:
- Approve full proposal → tasks created as IDEA
- Approve subset → only selected features become tasks
- Reject proposal → no tasks created
- Task dependency wiring after approval
- Promote ready tasks (IDEA → ASSIGNED)
"""

import pytest
import pytest_asyncio

from orchestra.core.task_queue import TaskQueue, TaskStatus


async def _setup_proposal(task_queue):
    """Helper: create a requirement + proposal with 3 features."""
    await task_queue.add_requirement("req-t", "Test requirement")
    features = [
        {"id": "feat-001", "title": "Base setup", "depends_on": [], "priority": 10},
        {"id": "feat-002", "title": "Player", "depends_on": ["feat-001"], "priority": 5},
        {"id": "feat-003", "title": "Enemy AI", "depends_on": ["feat-001"], "priority": 5},
    ]
    return await task_queue.add_proposal("prop-t", "req-t", features)


class TestApproveProposal:

    @pytest.mark.asyncio
    async def test_approve_all(self, orchestrator):
        tq = orchestrator.task_queue
        await _setup_proposal(tq)
        tasks = await orchestrator.approve_proposal("prop-t")
        assert len(tasks) == 3

        # All should be IDEA status
        for t in tasks:
            assert t.status == TaskStatus.IDEA

        # Proposal should be marked approved
        prop = await tq.get_proposal("prop-t")
        assert prop.status == "approved"

    @pytest.mark.asyncio
    async def test_approve_subset(self, orchestrator):
        tq = orchestrator.task_queue
        await _setup_proposal(tq)
        tasks = await orchestrator.approve_proposal("prop-t", approved_feature_ids=["feat-001", "feat-003"])
        assert len(tasks) == 2
        ids = {t.id for t in tasks}
        assert ids == {"feat-001", "feat-003"}

    @pytest.mark.asyncio
    async def test_reject_proposal(self, orchestrator):
        tq = orchestrator.task_queue
        await _setup_proposal(tq)
        await orchestrator.reject_proposal("prop-t")

        prop = await tq.get_proposal("prop-t")
        assert prop.status == "rejected"

        # No tasks should exist
        tasks = await tq.get_tasks()
        assert len(tasks) == 0


class TestDependencyPromotion:

    @pytest.mark.asyncio
    async def test_promote_no_deps(self, orchestrator):
        """Tasks with no dependencies should promote IDEA → ASSIGNED."""
        tq = orchestrator.task_queue
        await _setup_proposal(tq)
        await orchestrator.approve_proposal("prop-t")

        promoted = await tq.promote_ready_tasks()
        # Only feat-001 has no deps
        assert len(promoted) == 1
        assert promoted[0].id == "feat-001"
        assert promoted[0].status == TaskStatus.ASSIGNED

    @pytest.mark.asyncio
    async def test_blocked_tasks_not_promoted(self, orchestrator):
        """Tasks depending on undone tasks should stay IDEA."""
        tq = orchestrator.task_queue
        await _setup_proposal(tq)
        await orchestrator.approve_proposal("prop-t")
        await tq.promote_ready_tasks()

        # feat-002 and feat-003 depend on feat-001 which is ASSIGNED, not DONE
        ideas = await tq.get_tasks(TaskStatus.IDEA)
        assert len(ideas) == 2
        assert {t.id for t in ideas} == {"feat-002", "feat-003"}

    @pytest.mark.asyncio
    async def test_promote_after_dependency_done(self, orchestrator):
        """Once feat-001 is DONE, feat-002 and feat-003 should promote."""
        tq = orchestrator.task_queue
        await _setup_proposal(tq)
        await orchestrator.approve_proposal("prop-t")
        await tq.promote_ready_tasks()

        # Manually walk feat-001 through to DONE
        await tq.transition("feat-001", TaskStatus.IN_PROGRESS)
        await tq.transition("feat-001", TaskStatus.IMPLEMENTED)
        await tq.transition("feat-001", TaskStatus.TESTING)
        await tq.transition("feat-001", TaskStatus.REVIEW)
        await tq.transition("feat-001", TaskStatus.ACCEPTED)
        await tq.transition("feat-001", TaskStatus.DONE)

        promoted = await tq.promote_ready_tasks()
        assert len(promoted) == 2
        assert {t.id for t in promoted} == {"feat-002", "feat-003"}
