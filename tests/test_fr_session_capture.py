"""Integration test: FR session_id from spawner is persisted to the task table."""

import pytest

from orchestra.core.orchestrator import Orchestrator, OrchestraConfig
from orchestra.core.task_queue import TaskStatus

from conftest import FAKE_CLAUDE


@pytest.mark.asyncio
async def test_fr_session_id_persists_to_task_table(
    git_repo, orchestra_dir, fake_claude_env
):
    """When FR runs, the session_id from the first system event must be saved on Task."""
    fake_claude_env([
        {"type": "system", "model": "sonnet", "session_id": "abc-xyz"},
        {
            "type": "result",
            "result": "ORCHESTRA_RESULT:{\"status\":\"completed\",\"notes\":\"done\"}",
            "is_error": False,
            "duration_ms": 100,
            "num_turns": 1,
        },
    ])

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

        task = await orch.task_queue.get_task("t1")
        await orch._run_fr(task)

        refreshed = await orch.task_queue.get_task("t1")
        assert refreshed.fr_session_id == "abc-xyz"
    finally:
        await orch.close()
