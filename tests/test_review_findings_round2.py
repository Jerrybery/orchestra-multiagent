"""Task 5.3 — round-2 FI prompt injection of prior critical findings.

Round 1 FI rejects with one critical issue. The task is walked back to
IMPLEMENTED, FI runs again, and we capture the spawn's task_prompt to confirm
`_render_previous_findings` (Task 5.2) actually wired into the round-2 prompt.

Pure integration test — no production change. This is the wire-up sentinel.
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
        await orch._run_fi(task)

        # Sanity: round 1 finding persisted with the critical bullet parsed.
        f1 = await orch.task_queue.get_latest_review_finding(task.id)
        assert f1 is not None
        assert f1["round"] == 1
        assert f1["recommendation"] == "reject"
        assert any(
            it.get("desc") == "null deref in fooBar" for it in f1["critical"]
        ), f"round-1 critical not parsed from report: {f1['critical']!r}"

        # ── Walk back to IMPLEMENTED for round 2 ──────────────────────
        # _run_fi left the task in REVIEW. State machine path:
        # REVIEW → REJECTED → ASSIGNED → IN_PROGRESS → IMPLEMENTED.
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

        # Round 2 FI: accept this time. Report is rewritten by FI in real
        # life; for the test we just leave the round-1 file in place so the
        # parsed round-2 critical is non-empty too — but what we care about
        # is the *prompt* injection, not the resulting findings.
        fake_claude_env([
            {"type": "system", "model": "sonnet", "session_id": "fi-r2"},
            {"type": "result",
             "result": "ORCHESTRA_RESULT:{\"recommendation\":\"accept\"}",
             "is_error": False, "duration_ms": 50, "num_turns": 1},
        ])
        task2 = await orch.task_queue.get_task(task.id)
        await orch._run_fi(task2)

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
