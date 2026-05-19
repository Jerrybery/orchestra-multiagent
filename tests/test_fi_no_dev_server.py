import pytest
from unittest.mock import AsyncMock, MagicMock

from orchestra.core.runners.fi import FIRunner
from orchestra.core.runners.base import RunContext, RunResult, CancelToken


@pytest.mark.asyncio
async def test_fi_runs_without_run_config():
    """FI should still execute when run_config is None (no dev server)."""
    spawner = MagicMock()
    mock_handle = MagicMock()
    mock_handle.session_id = "sess-1"
    mock_handle.process = MagicMock()
    mock_handle.process.returncode = None
    spawner.spawn = AsyncMock(return_value=mock_handle)

    mock_result = MagicMock()
    mock_result.stdout = 'ORCHESTRA_RESULT:{"recommendation":"accept"}'
    mock_result.exit_code = 0
    spawner.wait = AsyncMock(return_value=mock_result)

    task = MagicMock(id="t1", title="Test Feature")

    runner = FIRunner(
        spawner,
        task_loader=AsyncMock(return_value=task),
        prompt_loader=MagicMock(return_value="system prompt {base_url} {dev_server_log_path} {chat_context_block}"),
        run_config_loader=AsyncMock(return_value=None),  # No run config!
        dev_server_factory=MagicMock(),
        worktree_path_fn=MagicMock(return_value="/tmp/wt"),
        dev_log_path_fn=MagicMock(return_value="/tmp/dev.log"),
        head_fn=AsyncMock(return_value="abc123"),
        status_fn=AsyncMock(return_value=""),
        reset_fn=AsyncMock(),
        report_parser=MagicMock(return_value=([], [])),
    )

    ctx = RunContext(
        role="fi", target_kind="task", target_id="t1",
        mode="standalone", resume_session_id=None, prev_run=None,
        project_dir=None, orchestra_dir=None, log_path="/tmp/fi.log",
    )
    cancel = CancelToken()
    result = await runner.run(ctx, cancel)

    assert result.status == "succeeded"
    assert result.result_snapshot["recommendation"] == "accept"
    # dev_server_factory should NOT have been called
    runner._dev_server.assert_not_called()
