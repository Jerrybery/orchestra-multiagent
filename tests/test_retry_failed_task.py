"""Task 4.2: retry_failed_task API — moves FAILED task back to ASSIGNED,
clears fail_reason, and auto-unpauses the parent proposal once no FAILED
siblings remain."""

import pytest

from orchestra.core.task_queue import TaskStatus


@pytest.mark.asyncio
async def test_retry_moves_task_back_to_assigned(orchestrator):
    await orchestrator.task_queue.add_requirement("r1", "x")
    await orchestrator.task_queue.add_proposal(
        "p1", "r1", features=[{"id": "ta", "title": "A"}]
    )
    await orchestrator.task_queue.add_task(
        "ta", title="A", priority=0, depends_on=[], requirement_id="r1"
    )
    await orchestrator.task_queue.update_proposal_status("p1", "approved")
    await orchestrator.task_queue.transition("ta", TaskStatus.ASSIGNED)
    await orchestrator.task_queue.transition("ta", TaskStatus.IN_PROGRESS)
    await orchestrator.task_queue.transition(
        "ta", TaskStatus.FAILED, fail_reason="x"
    )

    await orchestrator.retry_failed_task("ta")
    t = await orchestrator.task_queue.get_task("ta")
    assert t.status == TaskStatus.ASSIGNED
    assert t.fail_reason is None


@pytest.mark.asyncio
async def test_retry_rejects_non_failed_task(orchestrator):
    await orchestrator.task_queue.add_requirement("r1", "x")
    await orchestrator.task_queue.add_proposal(
        "p1", "r1", features=[{"id": "ta", "title": "A"}]
    )
    await orchestrator.task_queue.add_task(
        "ta", title="A", priority=0, depends_on=[], requirement_id="r1"
    )
    await orchestrator.task_queue.update_proposal_status("p1", "approved")
    # task in IDEA state — not FAILED
    with pytest.raises(ValueError):
        await orchestrator.retry_failed_task("ta")


@pytest.mark.asyncio
async def test_retry_unpauses_proposal_when_no_failed_left(orchestrator):
    """When the last FAILED sibling is retried, the proposal is auto-unpaused.

    Setup mirrors the cascade outcome: ta in FAILED, tb in FAILED,
    proposal status = paused (legacy state — `retry_failed_task` still
    flips that status back to approved when no FAILED siblings remain).
    """
    await orchestrator.task_queue.add_requirement("r1", "x")
    await orchestrator.task_queue.add_proposal(
        "p1", "r1",
        features=[{"id": "ta", "title": "A"}, {"id": "tb", "title": "B"}],
    )
    for fid in ("ta", "tb"):
        await orchestrator.task_queue.add_task(
            fid, title="x", priority=0, depends_on=[], requirement_id="r1"
        )
    await orchestrator.task_queue.update_proposal_status("p1", "approved")
    # Simulate the cascade outcome directly: both tasks FAILED, proposal paused.
    for fid in ("ta", "tb"):
        await orchestrator.task_queue.transition(fid, TaskStatus.ASSIGNED)
        await orchestrator.task_queue.transition(fid, TaskStatus.IN_PROGRESS)
        await orchestrator.task_queue.transition(
            fid, TaskStatus.FAILED, fail_reason="x"
        )
    await orchestrator.task_queue.update_proposal_status("p1", "paused")

    # Retry tb first — proposal still paused (ta still FAILED)
    await orchestrator.retry_failed_task("tb")
    p = await orchestrator.task_queue.get_proposal("p1")
    assert p.status == "paused"

    # Retry ta — proposal unpauses
    await orchestrator.retry_failed_task("ta")
    p = await orchestrator.task_queue.get_proposal("p1")
    assert p.status == "approved"
