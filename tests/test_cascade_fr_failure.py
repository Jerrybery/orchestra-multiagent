"""Task 4.1: verify _handle_fr_failure cascade — pauses proposal,
freezes unstarted siblings, leaves in-progress siblings alone."""

import pytest

from orchestra.core.task_queue import TaskStatus


@pytest.mark.asyncio
async def test_fr_failure_marks_task_failed_and_pauses_proposal(orchestrator):
    # 3 features
    await orchestrator.task_queue.add_requirement("r1", "test")
    await orchestrator.task_queue.add_proposal("p1", "r1", features=[
        {"id": "ta", "title": "A"},
        {"id": "tb", "title": "B"},
        {"id": "tc", "title": "C"},
    ])
    # Materialize tasks (use add_task directly, mirror Task 1.4 setup)
    for fid in ("ta", "tb", "tc"):
        await orchestrator.task_queue.add_task(
            fid, title="x", priority=0, depends_on=[], requirement_id="r1"
        )
    await orchestrator.task_queue.update_proposal_status("p1", "approved")

    # Move ta IDEA → ASSIGNED → IN_PROGRESS, then fail
    await orchestrator.task_queue.transition("ta", TaskStatus.ASSIGNED)
    await orchestrator.task_queue.transition("ta", TaskStatus.IN_PROGRESS)
    ta = await orchestrator.task_queue.get_task("ta")
    await orchestrator._handle_fr_failure(ta, reason="claude died")

    ta_after = await orchestrator.task_queue.get_task("ta")
    assert ta_after.status == TaskStatus.FAILED
    assert ta_after.fail_reason == "claude died"

    tb = await orchestrator.task_queue.get_task("tb")
    tc = await orchestrator.task_queue.get_task("tc")
    assert tb.status == TaskStatus.FAILED
    assert "Cancelled" in (tb.fail_reason or "")
    assert tc.status == TaskStatus.FAILED

    p = await orchestrator.task_queue.get_proposal("p1")
    assert p.status == "paused"


@pytest.mark.asyncio
async def test_in_progress_siblings_not_killed(orchestrator):
    await orchestrator.task_queue.add_requirement("r1", "test")
    await orchestrator.task_queue.add_proposal("p1", "r1", features=[
        {"id": "ta", "title": "A"},
        {"id": "tb", "title": "B"},
    ])
    for fid in ("ta", "tb"):
        await orchestrator.task_queue.add_task(
            fid, title="x", priority=0, depends_on=[], requirement_id="r1"
        )
    await orchestrator.task_queue.update_proposal_status("p1", "approved")
    await orchestrator.task_queue.transition("ta", TaskStatus.ASSIGNED)
    await orchestrator.task_queue.transition("ta", TaskStatus.IN_PROGRESS)
    await orchestrator.task_queue.transition("tb", TaskStatus.ASSIGNED)
    await orchestrator.task_queue.transition("tb", TaskStatus.IN_PROGRESS)
    ta = await orchestrator.task_queue.get_task("ta")
    await orchestrator._handle_fr_failure(ta, reason="x")

    tb = await orchestrator.task_queue.get_task("tb")
    assert tb.status == TaskStatus.IN_PROGRESS
