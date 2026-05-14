import pytest
from pathlib import Path

from orchestra.core.task_queue import TaskQueue, AgentRun, AutoPause, RunMessage
from orchestra.core.db.engine import create_db_engine, init_db


async def _make_tq(tmp_path: Path):
    engine, sf = create_db_engine(orchestra_dir=tmp_path)
    await init_db(engine)
    q = TaskQueue(sf)
    await q.init()
    return q, engine


@pytest.mark.asyncio
async def test_add_and_get_agent_run(tmp_path):
    q, engine = await _make_tq(tmp_path)
    run = await q.add_agent_run(
        role="hl", target_kind="requirement", target_id="req-1",
        mode="manual", log_path="/tmp/log",
    )
    assert run.id > 0 and run.status == "running"

    fetched = await q.get_agent_run(run.id)
    assert fetched.role == "hl" and fetched.target_id == "req-1"
    await engine.dispose()


@pytest.mark.asyncio
async def test_finish_agent_run_with_snapshot(tmp_path):
    q, engine = await _make_tq(tmp_path)
    run = await q.add_agent_run("fr", "task", "feat-001", "auto", "/tmp/log")
    await q.finish_agent_run(
        run.id, status="succeeded",
        result_snapshot={"branch": "feat/x", "head_commit": "abc"},
        session_id="sess-1",
    )
    fetched = await q.get_agent_run(run.id)
    assert fetched.status == "succeeded"
    assert fetched.result_snapshot == {"branch": "feat/x", "head_commit": "abc"}
    assert fetched.session_id == "sess-1"
    await engine.dispose()


@pytest.mark.asyncio
async def test_previous_run_id_links_succeeded_only(tmp_path):
    q, engine = await _make_tq(tmp_path)
    r1 = await q.add_agent_run("hl", "requirement", "req-1", "auto", "/tmp/log")
    await q.finish_agent_run(r1.id, status="succeeded", result_snapshot={"x": 1})
    r2 = await q.add_agent_run("hl", "requirement", "req-1", "manual", "/tmp/log")
    await q.finish_agent_run(r2.id, status="failed", error_message="boom")
    r3 = await q.add_agent_run("hl", "requirement", "req-1", "manual", "/tmp/log")
    # previous_run_id should point at r1 (succeeded), not r2 (failed)
    fetched = await q.get_agent_run(r3.id)
    assert fetched.previous_run_id == r1.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_pause_crud(tmp_path):
    q, engine = await _make_tq(tmp_path)
    await q.add_auto_pause("task", "feat-001", caused_by_run_id=None,
                           reason="test")
    paused = await q.list_auto_pauses()
    assert any(p.target_id == "feat-001" for p in paused)
    await q.remove_auto_pause("task", "feat-001")
    assert not await q.is_auto_paused("task", "feat-001")
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_messages_append(tmp_path):
    q, engine = await _make_tq(tmp_path)
    run = await q.add_agent_run("hl", "requirement", "req-1", "manual", "/tmp/log")
    await q.add_run_message(run.id, "user", "拆得太细了")
    await q.add_run_message(run.id, "assistant", "好的,我重拆")
    msgs = await q.get_run_messages(run.id)
    assert len(msgs) == 2 and msgs[0].role == "user"
    await engine.dispose()


@pytest.mark.asyncio
async def test_finish_agent_run_empty_snapshot_roundtrips(tmp_path):
    """Empty dict snapshot must survive DB round-trip as {}, not None."""
    q, engine = await _make_tq(tmp_path)
    run = await q.add_agent_run("fr", "task", "feat-001", "auto", "/tmp/log")
    await q.finish_agent_run(run.id, status="cancelled", result_snapshot={})
    fetched = await q.get_agent_run(run.id)
    assert fetched.result_snapshot == {}
    await engine.dispose()


@pytest.mark.asyncio
async def test_init_db_is_idempotent(tmp_path):
    """init_db (CREATE TABLE IF NOT EXISTS) must be safe to call multiple times."""
    engine, sf = create_db_engine(orchestra_dir=tmp_path)
    await init_db(engine)
    await init_db(engine)
    await init_db(engine)
    # Verify tables exist by creating and querying a TaskQueue
    q = TaskQueue(sf)
    await q.init()
    await q.add_task("t1", "Test task")
    t = await q.get_task("t1")
    assert t is not None and t.title == "Test task"
    await q.close()
    # Re-open with a fresh TaskQueue — must work fine
    q2 = TaskQueue(sf)
    await q2.init()
    t2 = await q2.get_task("t1")
    assert t2 is not None and t2.title == "Test task"
    await q2.close()
    await engine.dispose()
