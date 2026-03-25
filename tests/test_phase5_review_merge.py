"""Phase 5: Task review & merge tests.

Validates:
- Accept task → merge to main → DONE
- Reject task → back to ASSIGNED with reason
- Events are persisted and retrievable
- Cascading promotion after task completion
"""

import subprocess
import pytest
import pytest_asyncio

from orchestra.core.task_queue import TaskQueue, TaskStatus


async def _create_implemented_task(orchestrator, task_id="feat-001"):
    """Helper: create a task and walk it to IMPLEMENTED with a real worktree."""
    tq = orchestrator.task_queue
    await tq.add_task(task_id, "Test feature")
    await tq.transition(task_id, TaskStatus.ASSIGNED)
    await tq.transition(task_id, TaskStatus.IN_PROGRESS)

    # Create worktree and add a commit
    wt = await orchestrator.worktree.create_worktree(task_id)
    (wt / "impl.py").write_text("# implementation\n")
    subprocess.run(["git", "-C", str(wt), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-m", "Implement feature"], capture_output=True, check=True)

    await tq.transition(task_id, TaskStatus.IMPLEMENTED,
                        worktree_path=str(wt), branch=f"feat/{task_id}")
    return wt


class TestAcceptTask:

    @pytest.mark.asyncio
    async def test_accept_creates_pr_and_marks_done(self, orchestrator):
        wt = await _create_implemented_task(orchestrator)

        tq = orchestrator.task_queue
        await tq.transition("feat-001", TaskStatus.TESTING)
        await tq.transition("feat-001", TaskStatus.REVIEW)

        await orchestrator.accept_task("feat-001")

        task = await tq.get_task("feat-001")
        assert task.status == TaskStatus.DONE

        # Branch should still exist (not cleaned up — PR handles merge)
        branches = subprocess.run(
            ["git", "-C", str(orchestrator.config.project_dir), "branch"],
            capture_output=True, text=True
        )
        assert "feat/feat-001" in branches.stdout

    @pytest.mark.asyncio
    async def test_accept_promotes_blocked_tasks(self, orchestrator):
        """After feat-001 is DONE, blocked feat-002 should promote."""
        tq = orchestrator.task_queue

        await _create_implemented_task(orchestrator, "feat-001")
        await tq.add_task("feat-002", "Depends on 001", depends_on=["feat-001"])

        # Walk feat-001 to REVIEW and accept
        await tq.transition("feat-001", TaskStatus.TESTING)
        await tq.transition("feat-001", TaskStatus.REVIEW)
        await orchestrator.accept_task("feat-001")

        # feat-002 should have been promoted from IDEA to ASSIGNED
        t2 = await tq.get_task("feat-002")
        assert t2.status == TaskStatus.ASSIGNED


class TestRejectTask:

    @pytest.mark.asyncio
    async def test_reject_sends_back(self, orchestrator):
        tq = orchestrator.task_queue
        await tq.add_task("feat-001", "Test")
        await tq.transition("feat-001", TaskStatus.ASSIGNED)
        await tq.transition("feat-001", TaskStatus.IN_PROGRESS)
        await tq.transition("feat-001", TaskStatus.IMPLEMENTED)
        await tq.transition("feat-001", TaskStatus.TESTING)
        await tq.transition("feat-001", TaskStatus.REVIEW)

        await orchestrator.reject_task("feat-001", "Needs more tests")

        task = await tq.get_task("feat-001")
        assert task.status == TaskStatus.ASSIGNED
        assert task.reject_reason == "Needs more tests"


class TestEventPersistence:

    @pytest.mark.asyncio
    async def test_events_stored(self, task_queue):
        await task_queue.add_event("test_event", {"key": "value"})
        events = await task_queue.get_events(since_id=0)
        assert len(events) >= 1
        assert events[-1]["event"] == "test_event"
        assert events[-1]["data"]["key"] == "value"

    @pytest.mark.asyncio
    async def test_events_since_id(self, task_queue):
        await task_queue.add_event("ev1", {})
        await task_queue.add_event("ev2", {})
        await task_queue.add_event("ev3", {})

        all_events = await task_queue.get_events(since_id=0)
        last_id = all_events[0]["id"]

        newer = await task_queue.get_events(since_id=last_id)
        assert len(newer) == len(all_events) - 1
