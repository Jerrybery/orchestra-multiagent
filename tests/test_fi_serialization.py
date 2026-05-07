"""Task 5.4 — FI mutex serialization.

Two concurrent `_run_fi` calls must serialize via `self._fi_lock` (added in
Task 5.2): the dev server each FI spawns binds the project's port, so parallel
FI runs would conflict. We assert serialization by timing two `_run_fi`
invocations issued via `asyncio.gather` — if the lock is removed they finish
in roughly the per-FI time (~0.5s), with the lock they finish in ~2x that.
"""

import asyncio
import subprocess
import time

import pytest

from orchestra.core.orchestrator import Orchestrator, OrchestraConfig
from orchestra.core.run_config import RunConfig
from orchestra.core.task_queue import TaskStatus

from conftest import FAKE_CLAUDE


@pytest.mark.asyncio
async def test_two_fi_runs_serialize(
    git_repo, orchestra_dir, tmp_path, fake_claude_env
):
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
    try:
        # Two tasks under one approved proposal, walked to IMPLEMENTED.
        await orch.task_queue.add_requirement("r1", "x")
        await orch.task_queue.add_proposal(
            "p1", "r1",
            features=[{"id": "ta", "title": "A"}, {"id": "tb", "title": "B"}],
        )
        for tid in ("ta", "tb"):
            await orch.task_queue.add_task(
                tid, title=tid, priority=0, depends_on=[], requirement_id="r1"
            )
        await orch.task_queue.update_proposal_status("p1", "approved")
        for tid in ("ta", "tb"):
            await orch.task_queue.transition(tid, TaskStatus.ASSIGNED)
            await orch.task_queue.transition(tid, TaskStatus.IN_PROGRESS)
            await orch.task_queue.transition(tid, TaskStatus.IMPLEMENTED)

        # Independent worktree per task (each has its own initial commit so
        # `git status --porcelain` and `rev-parse HEAD` succeed in the FI
        # violation-detection path).
        for tid in ("ta", "tb"):
            wt = tmp_path / f"wt-{tid}"
            wt.mkdir()
            subprocess.check_call(["git", "init", "-b", "main"], cwd=wt)
            (wt / "x").write_text("init")
            subprocess.check_call(["git", "add", "."], cwd=wt)
            subprocess.check_call(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-m", "i"],
                cwd=wt,
            )
        orch.context.get_worktree_path = lambda tid: tmp_path / f"wt-{tid}"

        # Pre-write empty report files so the parser returns no findings
        # (we don't care about findings here — only timing).
        for tid in ("ta", "tb"):
            rep = orch.context.get_report_path(tid)
            rep.parent.mkdir(parents=True, exist_ok=True)
            rep.write_text(
                "# Verification Report\n"
                "## Critical (Must Fix)\n"
                "## Important (Should Fix)\n"
            )

        # RunConfig with a tiny dev server (prints ready signal, then sleeps).
        cfg = RunConfig(
            command=(
                'python -c "import time; '
                "print('Ready in', flush=True); time.sleep(30)\""
            ),
            ready_signal="Ready in",
            base_url="http://localhost:9999",
            startup_timeout=5,
        )
        await orch.context.save_run_config(cfg)

        # Each FI invocation pauses 0.5s inside fake_claude before returning
        # the result event, so a single _run_fi takes ~0.5s of dominant time.
        fake_claude_env([
            {"type": "system", "model": "sonnet", "session_id": "fi"},
            {"_sleep": 0.5},
            {"type": "result",
             "result": "ORCHESTRA_RESULT:{\"recommendation\":\"accept\"}",
             "is_error": False, "duration_ms": 500, "num_turns": 1},
        ])

        ta = await orch.task_queue.get_task("ta")
        tb = await orch.task_queue.get_task("tb")

        t0 = time.monotonic()
        await asyncio.gather(orch._run_fi(ta), orch._run_fi(tb))
        elapsed = time.monotonic() - t0

        # Serial (lock held): ≥ 2 × 0.5s sleep + dev-server overhead.
        # Parallel (lock missing): ~0.5s + overhead, well under 0.9s.
        assert elapsed >= 0.9, (
            f"Expected serial >= 0.9s, got {elapsed:.2f}s — "
            "_fi_lock is not serializing _run_fi calls"
        )
    finally:
        await orch.close()
