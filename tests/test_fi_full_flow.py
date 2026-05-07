"""Task 5.2 — full FI flow: dev server, findings storage, violation detection."""

import subprocess
import pytest

from orchestra.core.orchestrator import Orchestrator, OrchestraConfig
from orchestra.core.task_queue import TaskStatus
from orchestra.core.run_config import RunConfig

from conftest import FAKE_CLAUDE


async def _build_fi_orchestrator(git_repo, orchestra_dir, tmp_path):
    """Construct an Orchestrator wired to the fake_claude binary, materialize
    a task in IMPLEMENTED state, and persist a RunConfig that boots a tiny
    fake dev server. Returns (orchestrator, task, worktree_path)."""
    config = OrchestraConfig(
        project_dir=git_repo,
        orchestra_dir=orchestra_dir,
        max_fr=2,
        max_fi=1,
        max_hl=1,
        claude_cmd=FAKE_CLAUDE,
    )
    orch = Orchestrator(config)
    await orch.init()

    await orch.task_queue.add_requirement("r1", "x")
    await orch.task_queue.add_proposal(
        "p1", "r1", features=[{"id": "ti", "title": "X"}]
    )
    await orch.task_queue.add_task(
        "ti", title="X", priority=0, depends_on=[], requirement_id="r1"
    )
    await orch.task_queue.update_proposal_status("p1", "approved")
    await orch.task_queue.transition("ti", TaskStatus.ASSIGNED)
    await orch.task_queue.transition("ti", TaskStatus.IN_PROGRESS)
    await orch.task_queue.transition("ti", TaskStatus.IMPLEMENTED)

    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=wt)
    (wt / "x.py").write_text("# initial")
    subprocess.check_call(["git", "add", "."], cwd=wt)
    subprocess.check_call(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-m", "init"],
        cwd=wt,
    )
    orch.context.get_worktree_path = lambda tid: wt

    cfg = RunConfig(
        command='python -c "import time; print(\'Ready in 0.1s\', flush=True); time.sleep(30)"',
        ready_signal="Ready in",
        base_url="http://localhost:9999",
        startup_timeout=5,
    )
    await orch.context.save_run_config(cfg)

    task = await orch.task_queue.get_task("ti")
    return orch, task, wt


@pytest.mark.asyncio
async def test_fi_runs_with_dev_server_and_records_findings(
    git_repo, orchestra_dir, tmp_path, fake_claude_env
):
    """Happy path: FI completes, findings stored as round 1, status moves to REVIEW."""
    orch, task, wt = await _build_fi_orchestrator(git_repo, orchestra_dir, tmp_path)
    try:
        # Pre-write a report file mimicking what FI would produce. Empty
        # bullet lists under the headers parse to [] critical/important.
        report_path = orch.context.get_report_path(task.id)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            "# Verification Report\n"
            "## Critical (Must Fix)\n"
            "## Important (Should Fix)\n"
        )

        fake_claude_env([
            {"type": "system", "model": "sonnet", "session_id": "fi-1"},
            {"type": "result",
             "result": "ORCHESTRA_RESULT:{\"recommendation\":\"accept\",\"issues\":0}",
             "is_error": False, "duration_ms": 50, "num_turns": 1},
        ])

        await orch._run_fi(task)

        f = await orch.task_queue.get_latest_review_finding(task.id)
        assert f is not None
        assert f["round"] == 1
        assert f["recommendation"] == "accept"

        refreshed = await orch.task_queue.get_task(task.id)
        assert refreshed.status == TaskStatus.REVIEW
    finally:
        await orch.close()


@pytest.mark.asyncio
async def test_fi_detects_worktree_violation_and_rejects(
    git_repo, orchestra_dir, tmp_path, fake_claude_env
):
    """If FI mutates the worktree, the orchestrator must reset and force REJECTED.

    We wrap `spawner.spawn` so that when FI is spawned it side-effects a stray
    file in the worktree — exactly the violation scenario the spec forbids.
    The post-run `git status --porcelain` snapshot then diverges from baseline.
    """
    from orchestra.core.agent_spawner import AgentRole

    orch, task, wt = await _build_fi_orchestrator(git_repo, orchestra_dir, tmp_path)
    try:
        report_path = orch.context.get_report_path(task.id)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            "# Verification Report\n"
            "## Critical (Must Fix)\n"
            "## Important (Should Fix)\n"
        )

        fake_claude_env([
            {"type": "system", "model": "sonnet", "session_id": "fi-v"},
            {"type": "result",
             "result": "ORCHESTRA_RESULT:{\"recommendation\":\"accept\",\"issues\":0}",
             "is_error": False, "duration_ms": 50, "num_turns": 1},
        ])

        real_spawn = orch.spawner.spawn

        async def stray_spawn(*args, **kwargs):
            if kwargs.get("role") == AgentRole.FEATURE_INTERPRETER:
                (wt / "stray.txt").write_text("FI cheated")
            return await real_spawn(*args, **kwargs)

        orch.spawner.spawn = stray_spawn

        await orch._run_fi(task)

        refreshed = await orch.task_queue.get_task(task.id)
        assert refreshed.status == TaskStatus.REJECTED
        assert (
            refreshed.reject_reason
            and "FI modified the worktree" in refreshed.reject_reason
        )
        # `git clean -fd` after `git reset --hard` should have removed it.
        assert not (wt / "stray.txt").exists()
    finally:
        await orch.close()
