"""Integration test: _run_fr uses --resume when reject_reason + fr_session_id present."""

import pytest

from orchestra.core.orchestrator import Orchestrator, OrchestraConfig
from orchestra.core.task_queue import TaskStatus

from conftest import FAKE_CLAUDE


@pytest.mark.asyncio
async def test_resume_used_when_reject_reason_and_session_id(
    git_repo, orchestra_dir, fake_claude_env
):
    """Second FR run on a rejected task with a captured session_id must use --resume.

    First run captures sess-A. Then the task is walked through TESTING → REVIEW →
    REJECTED (with a reason) → ASSIGNED to simulate FI feedback. The second run
    must invoke fake_claude with `--resume sess-A`; FAKE_CLAUDE_RESUME_ID=sess-A
    enforces that requirement (mismatch → exit 1). The new system event yields
    sess-B, which is persisted as the new fr_session_id.
    """
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
        # Materialize a task in ASSIGNED state ready for FR
        await orch.task_queue.add_requirement("r1", "test req")
        await orch.task_queue.add_task(
            task_id="t1",
            title="feature 1",
            requirement_id="r1",
            spec_path=str(orch.context.get_spec_path("t1")),
        )
        # Write a minimal spec so _load_prompt succeeds
        orch.context.write_spec("t1", "# t1: feature 1\n\nDo the thing.")
        # IDEA → ASSIGNED
        await orch.task_queue.transition("t1", TaskStatus.ASSIGNED)

        # First FR run — captures session_id "sess-A".
        fake_claude_env([
            {"type": "system", "model": "sonnet", "session_id": "sess-A"},
            {
                "type": "result",
                "result": "ORCHESTRA_RESULT:{\"status\":\"completed\"}",
                "is_error": False,
                "duration_ms": 50,
                "num_turns": 1,
            },
        ])
        task = await orch.task_queue.get_task("t1")
        await orch._run_fr(task)

        refreshed = await orch.task_queue.get_task("t1")
        assert refreshed.fr_session_id == "sess-A"
        assert refreshed.status == TaskStatus.IMPLEMENTED

        # Walk: IMPLEMENTED → TESTING → REVIEW → REJECTED → ASSIGNED
        await orch.task_queue.transition("t1", TaskStatus.TESTING)
        await orch.task_queue.transition("t1", TaskStatus.REVIEW)
        await orch.task_queue.transition(
            "t1", TaskStatus.REJECTED, reject_reason="Fix the foo"
        )
        await orch.task_queue.transition("t1", TaskStatus.ASSIGNED)
        refreshed = await orch.task_queue.get_task("t1")
        assert refreshed.reject_reason == "Fix the foo"
        assert refreshed.fr_session_id == "sess-A"

        # Second run — must invoke fake_claude with --resume sess-A.
        # FAKE_CLAUDE_RESUME_ID=sess-A enforces the resume arg.
        fake_claude_env([
            {"type": "system", "model": "sonnet", "session_id": "sess-B"},
            {
                "type": "result",
                "result": "ORCHESTRA_RESULT:{\"status\":\"completed\"}",
                "is_error": False,
                "duration_ms": 50,
                "num_turns": 1,
            },
        ], resume_id="sess-A")
        await orch._run_fr(refreshed)

        final = await orch.task_queue.get_task("t1")
        # New session captured on the resume run.
        assert final.fr_session_id == "sess-B"
        assert final.status == TaskStatus.IMPLEMENTED
    finally:
        await orch.close()
