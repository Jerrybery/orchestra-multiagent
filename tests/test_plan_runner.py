"""Tests for PlanRunner."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from orchestra.core.runners.base import RunContext, CancelToken
from orchestra.core.runners.pl import PLRunner, _parse_result


def _make_ctx(target_id="t1", user_message=None, prev_snapshot=None):
    prev_run = None
    if prev_snapshot:
        prev_run = MagicMock()
        prev_run.result_snapshot = prev_snapshot
    return RunContext(
        role="pl", target_kind="task", target_id=target_id,
        mode="auto", resume_session_id=None, prev_run=prev_run,
        project_dir="/tmp/project", orchestra_dir="/tmp/.orchestra",
        log_path="/tmp/log", user_message=user_message,
    )


def test_parse_result_valid():
    output = 'Some text\nORCHESTRA_RESULT:{"plan": "## Plan\\n...", "files_to_touch": ["a.py"], "estimated_complexity": "low"}\n'
    result = _parse_result(output)
    assert result is not None
    assert "plan" in result
    assert result["files_to_touch"] == ["a.py"]


def test_parse_result_no_match():
    assert _parse_result("no result here") is None


def test_parse_result_invalid_json():
    assert _parse_result("ORCHESTRA_RESULT:{bad json}") is None


@pytest.mark.asyncio
async def test_plan_runner_success():
    spawner = MagicMock()
    handle = MagicMock()
    handle.session_id = "sess-1"

    result_obj = MagicMock()
    result_obj.stdout = 'ORCHESTRA_RESULT:{"plan": "## Implementation Plan\\nStep 1...", "files_to_touch": ["src/foo.py"], "estimated_complexity": "medium"}'

    spawner.spawn = AsyncMock(return_value=handle)
    spawner.wait = AsyncMock(return_value=result_obj)

    task = MagicMock()
    task.id = "t1"
    task.title = "Feature A"

    runner = PLRunner(
        spawner,
        task_loader=AsyncMock(return_value=task),
        prompt_loader=MagicMock(return_value="You are a planner."),
    )
    ctx = _make_ctx()
    cancel = CancelToken()
    result = await runner.run(ctx, cancel)
    assert result.status == "succeeded"
    assert "plan" in result.result_snapshot


@pytest.mark.asyncio
async def test_plan_runner_no_output():
    spawner = MagicMock()
    handle = MagicMock()
    handle.session_id = "sess-1"
    result_obj = MagicMock()
    result_obj.stdout = "I analyzed the code but got confused"

    spawner.spawn = AsyncMock(return_value=handle)
    spawner.wait = AsyncMock(return_value=result_obj)

    task = MagicMock()
    task.id = "t1"
    task.title = "Feature A"

    runner = PLRunner(
        spawner,
        task_loader=AsyncMock(return_value=task),
        prompt_loader=MagicMock(return_value="prompt"),
    )
    ctx = _make_ctx()
    cancel = CancelToken()
    result = await runner.run(ctx, cancel)
    assert result.status == "failed"
