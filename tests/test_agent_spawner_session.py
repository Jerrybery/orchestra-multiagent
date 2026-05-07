import pytest
from orchestra.core.agent_spawner import AgentSpawner, AgentRole, _summarize_stream_event


@pytest.mark.asyncio
async def test_spawner_extracts_session_id_via_callback(tmp_path):
    """The spawner must invoke on_session_id callback when first system event arrives."""
    captured = []

    async def cb(agent_id, sid):
        captured.append((agent_id, sid))

    fake = tmp_path / "fake_claude"
    fake.write_text(
        '#!/usr/bin/env python3\n'
        'import sys, json, time\n'
        'print(json.dumps({"type":"system","model":"sonnet","session_id":"sess-1"}), flush=True)\n'
        'time.sleep(0.5)\n'
        'print(json.dumps({"type":"result","result":"ok","is_error":False,'
        '"duration_ms":100,"num_turns":1}), flush=True)\n'
    )
    fake.chmod(0o755)
    spawner = AgentSpawner(claude_cmd=str(fake), on_session_id=cb)
    handle = await spawner.spawn(
        role=AgentRole.FEATURE_REALIZER,
        system_prompt="", task_prompt="hi",
        cwd=tmp_path, task_id="t1",
    )
    await spawner.wait(handle)
    assert handle.session_id == "sess-1"
    assert captured == [(handle.agent_id, "sess-1")]
