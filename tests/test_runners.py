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
    handle.session_id = "sess-1"
    spawner.spawn = AsyncMock(return_value=handle)
    res = MagicMock()
    res.stdout = ('some log\nORCHESTRA_RESULT:'
                  '{"summary":"x","features":[{"id":"feat-001","title":"a",'
                  '"depends_on":[],"priority":1,"spec":"s"}]}\n')
    res.stderr = ""
    res.exit_code = 0
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


from orchestra.core.runners.fr import FRRunner


@pytest.mark.asyncio
async def test_fr_runner_success(tmp_path, monkeypatch):
    spawner = MagicMock()
    handle = MagicMock(); handle.process.returncode = 0
    handle.session_id = "sess-fr"
    spawner.spawn = AsyncMock(return_value=handle)
    res = MagicMock()
    res.stdout = 'ORCHESTRA_RESULT:{"status":"success","notes":"impl done"}\n'
    res.stderr = ""; res.exit_code = 0
    spawner.wait = AsyncMock(return_value=res)

    # Mock worktree manager + git helpers
    wt_mgr = MagicMock()
    wt_mgr.create_worktree = AsyncMock(return_value=tmp_path / "wt")
    wt_mgr.get_branch_name = MagicMock(return_value="feat/feat-001")
    (tmp_path / "wt").mkdir()
    async def fake_head(cwd): return "abc123"
    async def fake_files(cwd, base): return ["a.py", "b.py"]
    runner = FRRunner(
        spawner=spawner, worktree_mgr=wt_mgr,
        task_loader=AsyncMock(return_value=MagicMock(
            id="feat-001", title="demo", spec="s",
            source_issue=None, reject_reason=None,
        )),
        prompt_loader=lambda task_id: "PROMPT",
        head_fn=fake_head, files_changed_fn=fake_files,
    )
    ctx = RunContext(
        role="fr", target_kind="task", target_id="feat-001",
        mode="manual", resume_session_id=None, prev_run=None,
        project_dir=tmp_path, orchestra_dir=tmp_path / ".o",
        log_path=str(tmp_path / "fr.log"),
    )
    result = await runner.run(ctx, CancelToken())
    assert result.status == "succeeded"
    assert result.result_snapshot["head_commit"] == "abc123"
    assert result.result_snapshot["files_changed"] == ["a.py", "b.py"]
    assert result.result_snapshot["branch"] == "feat/feat-001"


from orchestra.core.runners.fi import FIRunner


@pytest.mark.asyncio
async def test_fi_runner_clean_pass(tmp_path):
    spawner = MagicMock()
    handle = MagicMock(); handle.process.returncode = 0
    handle.session_id = "sess-fi"
    spawner.spawn = AsyncMock(return_value=handle)
    res = MagicMock()
    res.stdout = 'ORCHESTRA_RESULT:{"recommendation":"approve"}\n'
    res.stderr = ""; res.exit_code = 0
    spawner.wait = AsyncMock(return_value=res)

    class FakeServer:
        async def start(self): pass
        async def stop(self): pass

    # head + status unchanged → no violation
    async def head_fn(cwd): return "abc"
    async def status_fn(cwd): return ""
    async def reset_fn(cwd, h): pass
    runner = FIRunner(
        spawner=spawner,
        task_loader=AsyncMock(return_value=MagicMock(id="feat-001", title="demo")),
        prompt_loader=lambda t: "FI PROMPT {base_url} {dev_server_log_path}",
        run_config_loader=AsyncMock(return_value=MagicMock(
            command="echo", ready_signal="ok",
            base_url="http://x", startup_timeout=1,
        )),
        dev_server_factory=lambda **kw: FakeServer(),
        worktree_path_fn=lambda t: tmp_path / "wt",
        dev_log_path_fn=lambda t: tmp_path / "dev.log",
        head_fn=head_fn, status_fn=status_fn, reset_fn=reset_fn,
        report_parser=lambda t: ([], []),  # critical / important
    )
    (tmp_path / "wt").mkdir()

    ctx = RunContext(
        role="fi", target_kind="task", target_id="feat-001",
        mode="auto", resume_session_id=None, prev_run=None,
        project_dir=tmp_path, orchestra_dir=tmp_path / ".o",
        log_path=str(tmp_path / "fi.log"),
    )
    result = await runner.run(ctx, CancelToken())
    assert result.status == "succeeded"
    assert result.result_snapshot["recommendation"] == "approve"
