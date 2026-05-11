"""Tests for TaskQueue helper methods added in Task 1.3.

Note: `approve_proposal` lives on Orchestrator, not TaskQueue. Since these tests
exercise TaskQueue's public surface, we materialize tasks directly via
`add_task` (which is what `Orchestrator.approve_proposal` does internally).
"""

import pytest
from pathlib import Path
from orchestra.core.task_queue import TaskQueue, TaskStatus


@pytest.mark.asyncio
async def test_get_tasks_for_proposal_returns_all_features(tmp_path: Path):
    q = TaskQueue(tmp_path / "t.db")
    await q.init()
    await q.add_requirement("r1", "test")
    await q.add_proposal(
        "p1", "r1",
        features=[{"id": "t1", "title": "x"}, {"id": "t2", "title": "y"}],
    )
    # Materialize both features as tasks (mirrors approve_proposal behavior).
    await q.add_task("t1", "x", requirement_id="r1")
    await q.add_task("t2", "y", requirement_id="r1")

    tasks = await q.get_tasks_for_proposal("p1")
    ids = sorted(t.id for t in tasks)
    assert ids == ["t1", "t2"]
    await q.close()


@pytest.mark.asyncio
async def test_get_tasks_for_proposal_missing_proposal_returns_empty(tmp_path: Path):
    q = TaskQueue(tmp_path / "t.db")
    await q.init()
    tasks = await q.get_tasks_for_proposal("does-not-exist")
    assert tasks == []
    await q.close()


@pytest.mark.asyncio
async def test_get_tasks_for_proposal_empty_features_returns_empty(tmp_path: Path):
    q = TaskQueue(tmp_path / "t.db")
    await q.init()
    await q.add_requirement("r1", "test")
    await q.add_proposal("p1", "r1", features=[])
    tasks = await q.get_tasks_for_proposal("p1")
    assert tasks == []
    await q.close()


@pytest.mark.asyncio
async def test_transition_persists_fail_reason(tmp_path: Path):
    q = TaskQueue(tmp_path / "t.db")
    await q.init()
    await q.add_requirement("r1", "test")
    await q.add_proposal("p1", "r1", features=[{"id": "t1", "title": "x"}])
    await q.add_task("t1", "x", requirement_id="r1")
    # Walk to IN_PROGRESS, then FAILED with reason
    await q.transition("t1", TaskStatus.ASSIGNED)
    await q.transition("t1", TaskStatus.IN_PROGRESS)
    await q.transition("t1", TaskStatus.FAILED, fail_reason="boom")
    t = await q.get_task("t1")
    assert t.status == TaskStatus.FAILED
    assert t.fail_reason == "boom"
    await q.close()
