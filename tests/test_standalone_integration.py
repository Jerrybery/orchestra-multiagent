"""Integration test: standalone FR with fake_claude."""
import json
import subprocess
from pathlib import Path

import pytest

from orchestra.core.orchestrator import OrchestraConfig
from orchestra.core.standalone import StandaloneSession
from orchestra.core.task_queue import TaskStatus

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FAKE_CLAUDE = str(FIXTURE_DIR / "fake_claude.py")


@pytest.fixture
def config(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "init", str(project)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(project), "config", "user.email", "t@t"], capture_output=True)
    subprocess.run(["git", "-C", str(project), "config", "user.name", "T"], capture_output=True)
    (project / "README.md").write_text("# test\n")
    subprocess.run(["git", "-C", str(project), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(project), "commit", "-m", "init"], capture_output=True, check=True)
    return OrchestraConfig(
        project_dir=project,
        orchestra_dir=tmp_path / ".orchestra",
        # Use the executable fake_claude directly (it has a shebang); the
        # spawner builds cmd = [claude_cmd, "-p", "--verbose", ...] and calls
        # create_subprocess_exec, so it must be a single executable path.
        claude_cmd=FAKE_CLAUDE,
    )


@pytest.fixture
def fr_script(tmp_path):
    """Write a fake_claude JSONL script that emits a successful FR result."""
    script = tmp_path / "fr_script.jsonl"
    events = [
        {"type": "system", "session_id": "sess-fr-1", "model": "sonnet"},
        {"type": "result", "result": 'ORCHESTRA_RESULT:{"status":"done","notes":"implemented"}'},
    ]
    with script.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return script


@pytest.mark.asyncio
async def test_standalone_fr_end_to_end(config, fr_script, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(fr_script))
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "0")

    spec_file = tmp_path / "feature-login.md"
    spec_file.write_text("# Login Feature\n\nBuild a login page with email/password.")

    session = StandaloneSession(config, quiet=True)
    await session.init()

    result = await session.run_fr(spec_file)

    assert result["status"] == "succeeded"
    assert result["task_id"] == "login"

    # Verify task ended in IMPLEMENTED state
    task = await session.core.task_queue.get_task("login")
    assert task.status == TaskStatus.IMPLEMENTED

    # Verify agent_run recorded with standalone mode
    runs = await session.core.task_queue.list_agent_runs(role="fr")
    assert len(runs) >= 1
    assert runs[0].mode == "standalone"

    await session.close()
