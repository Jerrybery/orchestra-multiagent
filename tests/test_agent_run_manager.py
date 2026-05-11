import pytest
from unittest.mock import AsyncMock, MagicMock

from orchestra.core.agent_run_manager import AgentRunManager
from orchestra.core.runners.base import RunResult


@pytest.mark.asyncio
async def test_submit_creates_running_run_and_invokes_runner(tmp_path):
    # Minimal setup: fake task_queue, fake runner
    tq = MagicMock()
    fake_run = MagicMock(id=1)
    tq.add_agent_run = AsyncMock(return_value=fake_run)
    tq.get_agent_run = AsyncMock(return_value=MagicMock(
        previous_run_id=None, session_id=None,
    ))
    tq.finish_agent_run = AsyncMock()
    tq.is_auto_paused = AsyncMock(return_value=False)
    tq.transition = AsyncMock()
    tq.add_event = AsyncMock()
    tq.remove_auto_pause = AsyncMock()

    runner = MagicMock()
    runner.run = AsyncMock(return_value=RunResult(
        status="succeeded", session_id="s1",
        result_snapshot={"x": 1}, error_message=None,
    ))
    mgr = AgentRunManager(
        task_queue=tq,
        runners={"hl": runner, "fr": MagicMock(), "fi": MagicMock()},
        context={},  # passed through to RunContext
        log_path_fn=lambda role, tid: f"/tmp/{role}-{tid}.log",
    )
    rid = await mgr.submit(role="hl", target_kind="requirement",
                            target_id="req-1", mode="manual")
    assert rid == 1
    # Wait for the background task to finish
    await mgr.wait_for_finish(rid)
    runner.run.assert_called_once()
    tq.finish_agent_run.assert_called_once()
    # finish was called with succeeded
    call = tq.finish_agent_run.call_args
    assert call.kwargs.get("status") == "succeeded" or call.args[1] == "succeeded"


@pytest.mark.asyncio
async def test_finish_auto_failure_adds_auto_pause(tmp_path):
    tq = MagicMock()
    fake_run = MagicMock(id=2, role="hl", target_kind="requirement",
                          target_id="req-1", mode="auto")
    fake_run.previous_run_id = None
    tq.add_agent_run = AsyncMock(return_value=fake_run)
    tq.get_agent_run = AsyncMock(return_value=fake_run)
    tq.finish_agent_run = AsyncMock()
    tq.add_auto_pause = AsyncMock()
    tq.is_auto_paused = AsyncMock(return_value=False)
    tq.add_event = AsyncMock()

    runner = MagicMock()
    runner.run = AsyncMock(return_value=RunResult(
        status="failed", session_id=None, result_snapshot={},
        error_message="boom",
    ))
    mgr = AgentRunManager(
        task_queue=tq,
        runners={"hl": runner, "fr": MagicMock(), "fi": MagicMock()},
        context={}, log_path_fn=lambda r, t: "/tmp/x",
    )
    rid = await mgr.submit(role="hl", target_kind="requirement",
                            target_id="req-1", mode="auto")
    await mgr.wait_for_finish(rid)
    tq.add_auto_pause.assert_called_once()
    args = tq.add_auto_pause.call_args
    assert args.args[0] == "requirement" or args.kwargs.get("target_kind") == "requirement"


@pytest.mark.asyncio
async def test_chat_creates_new_run_linked_to_origin(tmp_path):
    tq = MagicMock()
    origin_run = MagicMock(id=10, role="hl", target_kind="requirement",
                           target_id="req-1", session_id="sess-orig")
    tq.get_agent_run = AsyncMock(return_value=origin_run)
    new_run = MagicMock(id=11)
    new_run.previous_run_id = None
    tq.add_agent_run = AsyncMock(return_value=new_run)
    tq.add_run_message = AsyncMock()
    tq.finish_agent_run = AsyncMock()
    tq.is_auto_paused = AsyncMock(return_value=False)
    tq.remove_auto_pause = AsyncMock()

    runner = MagicMock()
    runner.run = AsyncMock(return_value=RunResult(
        status="succeeded", session_id="sess-new",
        result_snapshot={"kind": "chat", "reply": "got it"},
        error_message=None,
    ))
    mgr = AgentRunManager(
        task_queue=tq,
        runners={"hl": runner, "fr": MagicMock(), "fi": MagicMock()},
        context={}, log_path_fn=lambda r, t: "/tmp/x",
    )
    rid = await mgr.chat(origin_run_id=10, message="please refine")
    await mgr.wait_for_finish(rid)
    # User message stored
    tq.add_run_message.assert_any_call(11, "user", "please refine")
    # Allow time for the background reply recording task to fire
    import asyncio
    for _ in range(10):
        await asyncio.sleep(0.01)
        if any(c.args[1] == "assistant" for c in tq.add_run_message.call_args_list):
            break
    found_assistant = any(
        c.args[1] == "assistant"
        for c in tq.add_run_message.call_args_list
    )
    assert found_assistant
