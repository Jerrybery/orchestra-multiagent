import pytest
from unittest.mock import AsyncMock, MagicMock

from orchestra.core.agent_run_manager import AgentRunManager
from orchestra.core.runners.base import RunResult
from orchestra.core.task_queue import TaskStatus


@pytest.mark.asyncio
async def test_standalone_hl_success_advances_requirement_status():
    """standalone mode should advance requirement status like auto mode."""
    tq = MagicMock()
    fake_run = MagicMock(id=1, previous_run_id=None)
    tq.add_agent_run = AsyncMock(return_value=fake_run)
    tq.get_agent_run = AsyncMock(return_value=MagicMock(
        previous_run_id=None, session_id=None,
    ))
    tq.finish_agent_run = AsyncMock()
    tq.add_event = AsyncMock()
    tq.update_requirement_status = AsyncMock()

    hl_done = AsyncMock()

    runner = MagicMock()
    runner.run = AsyncMock(return_value=RunResult(
        status="succeeded", session_id="s1",
        result_snapshot={"features": [{"id": "f1", "title": "F"}]},
        error_message=None,
    ))
    mgr = AgentRunManager(
        task_queue=tq,
        runners={"hl": runner},
        context={},
        log_path_fn=lambda r, t: "/tmp/x",
        hl_done_hook=hl_done,
    )
    rid = await mgr.submit(role="hl", target_kind="requirement",
                            target_id="req-1", mode="standalone")
    await mgr.wait_for_finish(rid)

    hl_done.assert_called_once()
    tq.update_requirement_status.assert_called_once_with("req-1", "processed")


@pytest.mark.asyncio
async def test_standalone_fr_success_advances_task_status():
    """standalone FR should advance ASSIGNED -> IN_PROGRESS -> IMPLEMENTED."""
    tq = MagicMock()
    fake_run = MagicMock(id=2, previous_run_id=None)
    tq.add_agent_run = AsyncMock(return_value=fake_run)
    tq.get_agent_run = AsyncMock(return_value=MagicMock(
        previous_run_id=None, session_id=None,
    ))
    tq.finish_agent_run = AsyncMock()
    tq.add_event = AsyncMock()
    tq.get_task = AsyncMock(return_value=MagicMock(
        status=TaskStatus.ASSIGNED,
    ))
    tq.transition = AsyncMock()

    runner = MagicMock()
    runner.run = AsyncMock(return_value=RunResult(
        status="succeeded", session_id="s2",
        result_snapshot={"branch": "feat/x", "files_changed": []},
        error_message=None,
    ))
    mgr = AgentRunManager(
        task_queue=tq,
        runners={"fr": runner},
        context={},
        log_path_fn=lambda r, t: "/tmp/x",
    )
    rid = await mgr.submit(role="fr", target_kind="task",
                            target_id="t1", mode="standalone")
    await mgr.wait_for_finish(rid)

    calls = [c.args for c in tq.transition.call_args_list]
    assert ("t1", TaskStatus.IN_PROGRESS) in calls
    assert ("t1", TaskStatus.IMPLEMENTED) in calls


@pytest.mark.asyncio
async def test_standalone_failure_does_not_auto_pause():
    """standalone failures should NOT add auto_pause (unlike auto mode)."""
    tq = MagicMock()
    fake_run = MagicMock(id=3, previous_run_id=None)
    tq.add_agent_run = AsyncMock(return_value=fake_run)
    tq.get_agent_run = AsyncMock(return_value=MagicMock(
        previous_run_id=None, session_id=None,
    ))
    tq.finish_agent_run = AsyncMock()
    tq.add_event = AsyncMock()
    tq.add_auto_pause = AsyncMock()

    runner = MagicMock()
    runner.run = AsyncMock(return_value=RunResult(
        status="failed", session_id=None,
        result_snapshot={}, error_message="boom",
    ))
    mgr = AgentRunManager(
        task_queue=tq,
        runners={"hl": runner},
        context={},
        log_path_fn=lambda r, t: "/tmp/x",
    )
    rid = await mgr.submit(role="hl", target_kind="requirement",
                            target_id="req-1", mode="standalone")
    await mgr.wait_for_finish(rid)

    tq.add_auto_pause.assert_not_called()
