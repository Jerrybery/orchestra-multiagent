"""FR-failure sibling cascade — pauses proposal via auto_pauses,
freezes unstarted siblings (auto_paused), leaves in-progress siblings alone.

Migrated from the old `_handle_fr_failure` API to the new
`_on_fr_failed_cascade` hook fired by AgentRunManager on auto FR failures.
"""

import pytest

from orchestra.core.task_queue import TaskStatus


@pytest.mark.asyncio
async def test_fr_failure_marks_task_failed_and_pauses_proposal(orchestrator):
    await orchestrator.task_queue.add_requirement("r1", "test")
    await orchestrator.task_queue.add_proposal("p1", "r1", features=[
        {"id": "ta", "title": "A"},
        {"id": "tb", "title": "B"},
        {"id": "tc", "title": "C"},
    ])
    for fid in ("ta", "tb", "tc"):
        await orchestrator.task_queue.add_task(
            fid, title="x", priority=0, depends_on=[], requirement_id="r1"
        )
    await orchestrator.task_queue.update_proposal_status("p1", "approved")

    # Move ta IDEA → PLANNING → PLANNED → ASSIGNED → IN_PROGRESS, then trip the cascade
    await orchestrator.task_queue.transition("ta", TaskStatus.PLANNING)
    await orchestrator.task_queue.transition("ta", TaskStatus.PLANNED)
    await orchestrator.task_queue.transition("ta", TaskStatus.ASSIGNED)
    await orchestrator.task_queue.transition("ta", TaskStatus.IN_PROGRESS)
    await orchestrator._on_fr_failed_cascade("ta", run_id=1, reason="claude died")

    ta_after = await orchestrator.task_queue.get_task("ta")
    assert ta_after.status == TaskStatus.FAILED
    assert ta_after.fail_reason == "claude died"

    # IDEA/ASSIGNED siblings get auto-paused (not transitioned to FAILED in
    # the new model — they wait, the AutoDriver skips them via is_auto_paused).
    tb = await orchestrator.task_queue.get_task("tb")
    tc = await orchestrator.task_queue.get_task("tc")
    assert tb.status == TaskStatus.IDEA
    assert tc.status == TaskStatus.IDEA
    assert await orchestrator.task_queue.is_auto_paused("task", "tb")
    assert await orchestrator.task_queue.is_auto_paused("task", "tc")

    # Proposal-level auto_pause registered (the AutoDriver checks this
    # before issuing further FR runs against the proposal's tasks).
    assert await orchestrator.task_queue.is_auto_paused("proposal", "p1")


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
    await orchestrator.task_queue.transition("ta", TaskStatus.PLANNING)
    await orchestrator.task_queue.transition("ta", TaskStatus.PLANNED)
    await orchestrator.task_queue.transition("ta", TaskStatus.ASSIGNED)
    await orchestrator.task_queue.transition("ta", TaskStatus.IN_PROGRESS)
    await orchestrator.task_queue.transition("tb", TaskStatus.PLANNING)
    await orchestrator.task_queue.transition("tb", TaskStatus.PLANNED)
    await orchestrator.task_queue.transition("tb", TaskStatus.ASSIGNED)
    await orchestrator.task_queue.transition("tb", TaskStatus.IN_PROGRESS)
    await orchestrator._on_fr_failed_cascade("ta", run_id=1, reason="x")

    # In-progress siblings are not auto-paused (they keep running) and not
    # cascade-failed; they finish naturally.
    tb = await orchestrator.task_queue.get_task("tb")
    assert tb.status == TaskStatus.IN_PROGRESS
    assert not await orchestrator.task_queue.is_auto_paused("task", "tb")
