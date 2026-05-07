"""Lifecycle / exception-safety tests for Orchestrator internals."""

import pytest

from orchestra.core.task_queue import TaskStatus


@pytest.mark.asyncio
async def test_running_tasks_cleared_when_spawner_raises(orchestrator, monkeypatch, tmp_path):
    """If spawner.wait() raises, task must be removed from _running_tasks.

    Regression guard for the exception-safety fix in _run_fr: previously a
    raise from spawner.wait() would skip the `del self._running_tasks[task.id]`
    line and permanently lock the task out of subsequent _tick dispatches.
    """
    await orchestrator.task_queue.add_requirement("r1", "x")
    await orchestrator.task_queue.add_proposal(
        "p1", "r1", features=[{"id": "ta", "title": "A"}]
    )
    await orchestrator.task_queue.add_task(
        "ta", title="A", priority=0, depends_on=[], requirement_id="r1"
    )
    await orchestrator.task_queue.update_proposal_status("p1", "approved")
    await orchestrator.task_queue.transition("ta", TaskStatus.ASSIGNED)

    # Stub the worktree so _run_fr can resolve a path without git plumbing.
    wt = tmp_path / "wt"
    wt.mkdir()
    orchestrator.context.get_worktree_path = lambda tid: wt

    # Stub the spawner: spawn returns a sentinel handle, wait raises.
    async def fake_spawn(*args, **kwargs):
        return object()

    async def boom(handle):
        raise RuntimeError("simulated spawner crash")

    monkeypatch.setattr(orchestrator.spawner, "spawn", fake_spawn)
    monkeypatch.setattr(orchestrator.spawner, "wait", boom)

    task = await orchestrator.task_queue.get_task("ta")
    with pytest.raises(RuntimeError):
        await orchestrator._run_fr(task)

    assert "ta" not in orchestrator._running_tasks
