"""Full FI flow through AgentRunManager — dev server, findings storage,
violation detection. Migrated from the old `_run_fi` direct invocation
to the new `mgr.submit(role="fi", ...)` + `wait_for_finish` path.
"""

import subprocess
import pytest

from orchestra.core.orchestrator import Orchestrator, OrchestraConfig
from orchestra.core.task_queue import TaskStatus
from orchestra.core.run_config import RunConfig

from conftest import FAKE_CLAUDE


async def _build_fi_orchestrator(git_repo, orchestra_dir, tmp_path):
    """Construct an Orchestrator wired to fake_claude, materialize a task in
    IMPLEMENTED state, persist a RunConfig that boots a tiny fake dev server.
    Returns (orchestrator, task, worktree_path)."""
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
    await orch.task_queue.transition("ti", TaskStatus.PLANNING)
    await orch.task_queue.transition("ti", TaskStatus.PLANNED)
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


async def _run_fi_via_manager(orch, task_id: str):
    """Drive a single FI run through the manager and block until done."""
    rid = await orch.manager.submit(
        role="fi", target_kind="task", target_id=task_id, mode="auto",
    )
    await orch.manager.wait_for_finish(rid)
    return rid


@pytest.mark.asyncio
async def test_fi_runs_with_dev_server_and_records_findings(
    git_repo, orchestra_dir, tmp_path, fake_claude_env
):
    """Happy path: FI completes, recommendation in agent_run snapshot,
    task moves to REVIEW via the Manager state machine.
    """
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
            {"type": "system", "model": "sonnet", "session_id": "fi-1"},
            {"type": "result",
             "result": "ORCHESTRA_RESULT:{\"recommendation\":\"accept\",\"issues\":0}",
             "is_error": False, "duration_ms": 50, "num_turns": 1},
        ])

        rid = await _run_fi_via_manager(orch, task.id)

        run = await orch.task_queue.get_agent_run(rid)
        assert run.status == "succeeded"
        assert run.result_snapshot["recommendation"] == "accept"

        refreshed = await orch.task_queue.get_task(task.id)
        assert refreshed.status == TaskStatus.REVIEW
    finally:
        await orch.close()


@pytest.mark.asyncio
async def test_fi_ignores_dev_server_artifacts(
    git_repo, orchestra_dir, tmp_path, fake_claude_env
):
    """Dev-server-generated files (node_modules/, .next/, etc.) written DURING
    the FI window must not be mistaken for an FI worktree violation.

    Configure the "dev server" to print the ready signal, then sleep briefly
    and create files inside `node_modules/` and `.next/`. FI itself does
    nothing destructive. Expectation: recommendation is the parsed "accept",
    NOT "reject" (the violation-detected reject path overrides the snapshot).
    """
    orch, task, wt = await _build_fi_orchestrator(git_repo, orchestra_dir, tmp_path)
    try:
        cfg = RunConfig(
            command=(
                'python -c "'
                'import os, time, sys;'
                "print('Ready in 0.1s', flush=True);"
                "time.sleep(0.5);"
                "os.makedirs('node_modules/foo', exist_ok=True);"
                "open('node_modules/foo/cache.json','w').write('{}');"
                "os.makedirs('.next', exist_ok=True);"
                "open('.next/build-manifest.json','w').write('{}');"
                "open('next-env.d.ts','w').write('// generated');"
                "time.sleep(30)"
                '"'
            ),
            ready_signal="Ready in",
            base_url="http://localhost:9999",
            startup_timeout=5,
        )
        await orch.context.save_run_config(cfg)

        report_path = orch.context.get_report_path(task.id)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            "# Verification Report\n"
            "## Critical (Must Fix)\n"
            "## Important (Should Fix)\n"
        )

        fake_claude_env([
            {"type": "system", "model": "sonnet", "session_id": "fi-art"},
            {"type": "result",
             "result": "ORCHESTRA_RESULT:{\"recommendation\":\"accept\",\"issues\":0}",
             "is_error": False, "duration_ms": 50, "num_turns": 1},
        ])

        # Race-window guard: the dev-server command sleeps 0.5s after ready
        # before writing artifacts; the fake_claude script returns near-
        # instantly, so without this wait current_status could be taken
        # before the artifacts land.
        from orchestra.core.agent_spawner import AgentRole
        import asyncio as _asyncio
        real_spawn = orch.spawner.spawn

        async def slow_spawn(*args, **kwargs):
            handle = await real_spawn(*args, **kwargs)
            if kwargs.get("role") == AgentRole.FEATURE_INTERPRETER:
                await _asyncio.sleep(1.0)
            return handle

        orch.spawner.spawn = slow_spawn

        rid = await _run_fi_via_manager(orch, task.id)

        run = await orch.task_queue.get_agent_run(rid)
        # FIRunner returns succeeded with the actual recommendation when no
        # violation is detected. Filtered dev artifacts must not flip this
        # to reject.
        assert run.status == "succeeded"
        assert run.result_snapshot["recommendation"] == "accept"

        refreshed = await orch.task_queue.get_task(task.id)
        assert refreshed.status == TaskStatus.REVIEW
        # Sanity: confirm the artifacts were actually written.
        assert (wt / "node_modules" / "foo" / "cache.json").exists()
        assert (wt / ".next" / "build-manifest.json").exists()
    finally:
        await orch.close()


@pytest.mark.asyncio
async def test_fi_detects_worktree_violation_and_rejects(
    git_repo, orchestra_dir, tmp_path, fake_claude_env
):
    """If FI mutates the worktree, FIRunner resets and forces recommendation=reject.

    Wrap `spawner.spawn` so that when FI is spawned it side-effects a stray
    file in the worktree — exactly the violation scenario the spec forbids.
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

        rid = await _run_fi_via_manager(orch, task.id)

        run = await orch.task_queue.get_agent_run(rid)
        # FIRunner returns succeeded with recommendation=reject + reset.
        assert run.status == "succeeded"
        assert run.result_snapshot["recommendation"] == "reject"
        assert "modified the worktree" in run.result_snapshot.get("reason", "")
        # `git reset --hard` should have removed the stray file.
        assert not (wt / "stray.txt").exists()
    finally:
        await orch.close()
