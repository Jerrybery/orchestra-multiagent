"""Round-2 FI prompt injection of prior critical findings.

Round 1 FI rejects with one critical issue. The task is walked back to
IMPLEMENTED, FI runs again with `resumed_from_run_id` pointing at round 1,
and we capture the spawn's task_prompt to confirm prev_run findings are
wired into the round-2 prompt.

Migrated to drive runs through `AgentRunManager` rather than `_run_fi`.
"""

import pytest

from orchestra.core.task_queue import TaskStatus

from test_fi_full_flow import _build_fi_orchestrator


@pytest.mark.asyncio
async def test_round2_prompt_contains_previous_findings(
    git_repo, orchestra_dir, tmp_path, fake_claude_env
):
    orch, task, wt = await _build_fi_orchestrator(git_repo, orchestra_dir, tmp_path)
    try:
        # ── Round 1: FI sees a report with one Critical finding ────────
        rep = orch.context.get_report_path(task.id)
        rep.parent.mkdir(parents=True, exist_ok=True)
        rep.write_text(
            "# Verification Report\n\n"
            "### Critical (Must Fix)\n"
            "- src/foo.py:10 — null deref in fooBar\n"
            "### Important (Should Fix)\n"
        )
        fake_claude_env([
            {"type": "system", "model": "sonnet", "session_id": "fi-r1"},
            {"type": "result",
             "result": "ORCHESTRA_RESULT:{\"recommendation\":\"reject\",\"issues\":1,\"critical\":1}",
             "is_error": False, "duration_ms": 50, "num_turns": 1},
        ])
        rid1 = await orch.manager.submit(
            role="fi", target_kind="task", target_id=task.id, mode="auto",
        )
        await orch.manager.wait_for_finish(rid1)

        # Sanity: round 1 snapshot has the critical bullet parsed.
        run1 = await orch.task_queue.get_agent_run(rid1)
        assert run1 is not None
        assert run1.status == "succeeded"
        assert run1.result_snapshot["recommendation"] == "reject"
        assert any(
            it.get("desc") == "null deref in fooBar"
            for it in run1.result_snapshot["critical"]
        ), (
            f"round-1 critical not parsed from report: "
            f"{run1.result_snapshot['critical']!r}"
        )

        # ── Walk back to IMPLEMENTED for round 2 ──────────────────────
        await orch.task_queue.transition(
            task.id, TaskStatus.REJECTED, reject_reason="see report"
        )
        await orch.task_queue.transition(task.id, TaskStatus.ASSIGNED)
        await orch.task_queue.transition(task.id, TaskStatus.IN_PROGRESS)
        await orch.task_queue.transition(task.id, TaskStatus.IMPLEMENTED)

        # ── Capture round-2 spawn's task_prompt ──────────────────────
        captured_prompts: list[str] = []
        original_spawn = orch.spawner.spawn

        async def capture_spawn(*args, **kwargs):
            captured_prompts.append(kwargs.get("task_prompt", ""))
            return await original_spawn(*args, **kwargs)

        orch.spawner.spawn = capture_spawn

        fake_claude_env([
            {"type": "system", "model": "sonnet", "session_id": "fi-r2"},
            {"type": "result",
             "result": "ORCHESTRA_RESULT:{\"recommendation\":\"accept\"}",
             "is_error": False, "duration_ms": 50, "num_turns": 1},
        ])
        # Round-2 run — `add_agent_run` auto-resolves previous_run_id from
        # the most recent succeeded run for (role, target_id), so the
        # Manager injects round-1's snapshot as prev_run for FIRunner to
        # render in the prompt.
        rid2 = await orch.manager.submit(
            role="fi", target_kind="task", target_id=task.id, mode="auto",
        )
        await orch.manager.wait_for_finish(rid2)

        # ── Assert: round-2 prompt carries the round-1 critical ──────
        assert captured_prompts, "FI spawn was never called in round 2"
        assert any("Previous Review Findings" in p for p in captured_prompts), (
            f"missing 'Previous Review Findings' header in any prompt: "
            f"{captured_prompts!r}"
        )
        assert any("null deref" in p for p in captured_prompts), (
            f"missing critical desc in any prompt: {captured_prompts!r}"
        )
        assert any("src/foo.py:10" in p for p in captured_prompts), (
            f"missing file:line in any prompt: {captured_prompts!r}"
        )
    finally:
        await orch.close()
