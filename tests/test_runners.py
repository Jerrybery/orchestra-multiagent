import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

from orchestra.core.runners.base import RunContext, CancelToken
from orchestra.core.runners.hl import HLRunner


@pytest.mark.asyncio
async def test_hl_runner_success(tmp_path):
    spawner = MagicMock()
    handle = MagicMock()
    handle.process.returncode = 0
    spawner.spawn = AsyncMock(return_value=handle)
    res = MagicMock()
    res.stdout = ('some log\nORCHESTRA_RESULT:'
                  '{"summary":"x","features":[{"id":"feat-001","title":"a",'
                  '"depends_on":[],"priority":1,"spec":"s"}]}\n')
    res.stderr = ""
    res.exit_code = 0
    res.session_id = "sess-1"
    spawner.wait = AsyncMock(return_value=res)

    runner = HLRunner(spawner, requirement_loader=AsyncMock(return_value="REQ TEXT"),
                      prompt_loader=lambda: "PROMPT TEMPLATE")
    ctx = RunContext(
        role="hl", target_kind="requirement", target_id="req-1",
        mode="auto", resume_session_id=None, prev_run=None,
        project_dir=tmp_path, orchestra_dir=tmp_path / ".o",
        log_path=str(tmp_path / "hl.log"),
    )
    result = await runner.run(ctx, CancelToken())
    assert result.status == "succeeded"
    assert result.result_snapshot["summary"] == "x"
    assert len(result.result_snapshot["features"]) == 1
    assert result.session_id == "sess-1"
