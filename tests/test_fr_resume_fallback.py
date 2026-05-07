"""Integration test: _spawn_fr_with_resume_fallback path.

When --resume <stale-id> exits non-zero within 2s, the orchestrator must:
  1. Emit `fr_resume_fallback`.
  2. Clear `task.fr_session_id` (drop the stale id).
  3. Re-spawn FR without --resume so it captures a FRESH session id.
"""

import pytest

from orchestra.core.orchestrator import Orchestrator, OrchestraConfig
from orchestra.core.task_queue import TaskStatus

from conftest import FAKE_CLAUDE


@pytest.mark.asyncio
async def test_resume_fallback_when_session_lost(
    git_repo, orchestra_dir, fake_claude_env
):
    """Stale fr_session_id triggers --resume; mismatch exits 1; fallback respawns fresh.

    Setup: task in ASSIGNED with fr_session_id='stale-id' and reject_reason='fix it'
    so _run_fr takes the resume branch.

    fake_claude is configured with FAKE_CLAUDE_RESUME_ID='DIFFERENT'. The first
    spawn passes `--resume stale-id`, mismatches the expected id, and exits 1.
    The 2s probe in _spawn_fr_with_resume_fallback catches it, emits
    fr_resume_fallback, clears the stale id, and re-spawns without --resume.
    Since the second spawn omits --resume, fake_claude does no enforcement and
    emits the scripted system event with session_id='fresh-id'.
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
        # Materialize a task and walk it to (ASSIGNED, reject_reason set, stale sid).
        await orch.task_queue.add_requirement("r1", "test req")
        await orch.task_queue.add_task(
            task_id="t1",
            title="feature 1",
            requirement_id="r1",
            spec_path=str(orch.context.get_spec_path("t1")),
        )
        orch.context.write_spec("t1", "# t1: feature 1\n\nDo the thing.")
        # IDEA → ASSIGNED → IN_PROGRESS → IMPLEMENTED → TESTING → REVIEW
        # → REJECTED (with reason) → ASSIGNED.
        await orch.task_queue.transition("t1", TaskStatus.ASSIGNED)
        await orch.task_queue.transition("t1", TaskStatus.IN_PROGRESS)
        await orch.task_queue.transition("t1", TaskStatus.IMPLEMENTED)
        await orch.task_queue.transition("t1", TaskStatus.TESTING)
        await orch.task_queue.transition("t1", TaskStatus.REVIEW)
        await orch.task_queue.transition(
            "t1", TaskStatus.REJECTED, reject_reason="fix it"
        )
        await orch.task_queue.transition("t1", TaskStatus.ASSIGNED)
        # Plant the stale session id that fake_claude will reject.
        await orch.task_queue.set_fr_session_id("t1", "stale-id")

        task = await orch.task_queue.get_task("t1")
        assert task.fr_session_id == "stale-id"
        assert task.reject_reason == "fix it"
        assert task.status == TaskStatus.ASSIGNED

        # First spawn passes --resume stale-id, mismatches DIFFERENT → exit 1.
        # Fallback spawn omits --resume → no enforcement → emits fresh-id.
        fake_claude_env([
            {"type": "system", "model": "sonnet", "session_id": "fresh-id"},
            {
                "type": "result",
                "result": "ORCHESTRA_RESULT:{\"status\":\"completed\"}",
                "is_error": False,
                "duration_ms": 50,
                "num_turns": 1,
            },
        ], resume_id="DIFFERENT")

        # Capture emit events. Wrap, don't replace, so normal flow continues.
        events_seen: list[tuple[str, dict | None]] = []
        original_emit = orch._emit

        async def capture(event, payload=None):
            events_seen.append((event, payload))
            await original_emit(event, payload)

        orch._emit = capture

        await orch._run_fr(task)

        # Fallback event was emitted.
        event_names = [e for e, _ in events_seen]
        assert "fr_resume_fallback" in event_names, (
            f"expected fr_resume_fallback in events; saw {event_names}"
        )

        # Stale id was cleared during fallback, then fresh id captured by callback.
        refreshed = await orch.task_queue.get_task("t1")
        assert refreshed.fr_session_id == "fresh-id", (
            f"expected fresh-id, got {refreshed.fr_session_id!r}"
        )
        # Final status should be IMPLEMENTED (fallback spawn ran scripted success).
        assert refreshed.status == TaskStatus.IMPLEMENTED
    finally:
        await orch.close()
