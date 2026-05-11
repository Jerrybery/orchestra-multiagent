import asyncio
import pytest
from orchestra.core.runners.base import CancelToken, RunContext, RunResult


def test_run_context_construction():
    ctx = RunContext(
        role="hl", target_kind="requirement", target_id="req-1",
        mode="manual", resume_session_id=None, prev_run=None,
        project_dir=None, orchestra_dir=None, log_path="/tmp/x.log",
    )
    assert ctx.role == "hl"


@pytest.mark.asyncio
async def test_cancel_token_fire():
    tok = CancelToken()
    assert not tok.is_set()
    tok.set()
    assert tok.is_set()
    # waiter unblocks
    await asyncio.wait_for(tok.wait(), timeout=0.1)


def test_run_result_construction():
    r = RunResult(status="succeeded", session_id="s1",
                  result_snapshot={}, error_message=None,
                  used_resume=False, fell_back=False)
    assert r.status == "succeeded"
