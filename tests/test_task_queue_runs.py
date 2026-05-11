import pytest

from orchestra.core.task_queue import TaskQueue, AgentRun, AutoPause, RunMessage


@pytest.mark.asyncio
async def test_add_and_get_agent_run(tmp_path):
    q = TaskQueue(tmp_path / "t.db")
    await q.init()
    run = await q.add_agent_run(
        role="hl", target_kind="requirement", target_id="req-1",
        mode="manual", log_path="/tmp/log",
    )
    assert run.id > 0 and run.status == "running"

    fetched = await q.get_agent_run(run.id)
    assert fetched.role == "hl" and fetched.target_id == "req-1"


@pytest.mark.asyncio
async def test_finish_agent_run_with_snapshot(tmp_path):
    q = TaskQueue(tmp_path / "t.db")
    await q.init()
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


@pytest.mark.asyncio
async def test_previous_run_id_links_succeeded_only(tmp_path):
    q = TaskQueue(tmp_path / "t.db")
    await q.init()
    r1 = await q.add_agent_run("hl", "requirement", "req-1", "auto", "/tmp/log")
    await q.finish_agent_run(r1.id, status="succeeded", result_snapshot={"x": 1})
    r2 = await q.add_agent_run("hl", "requirement", "req-1", "manual", "/tmp/log")
    await q.finish_agent_run(r2.id, status="failed", error_message="boom")
    r3 = await q.add_agent_run("hl", "requirement", "req-1", "manual", "/tmp/log")
    # previous_run_id should point at r1 (succeeded), not r2 (failed)
    fetched = await q.get_agent_run(r3.id)
    assert fetched.previous_run_id == r1.id


@pytest.mark.asyncio
async def test_auto_pause_crud(tmp_path):
    q = TaskQueue(tmp_path / "t.db")
    await q.init()
    await q.add_auto_pause("task", "feat-001", caused_by_run_id=None,
                           reason="test")
    paused = await q.list_auto_pauses()
    assert any(p.target_id == "feat-001" for p in paused)
    await q.remove_auto_pause("task", "feat-001")
    assert not await q.is_auto_paused("task", "feat-001")


@pytest.mark.asyncio
async def test_run_messages_append(tmp_path):
    q = TaskQueue(tmp_path / "t.db")
    await q.init()
    run = await q.add_agent_run("hl", "requirement", "req-1", "manual", "/tmp/log")
    await q.add_run_message(run.id, "user", "拆得太细了")
    await q.add_run_message(run.id, "assistant", "好的,我重拆")
    msgs = await q.get_run_messages(run.id)
    assert len(msgs) == 2 and msgs[0].role == "user"


@pytest.mark.asyncio
async def test_finish_agent_run_empty_snapshot_roundtrips(tmp_path):
    """Empty dict snapshot must survive DB round-trip as {}, not None."""
    q = TaskQueue(tmp_path / "t.db")
    await q.init()
    run = await q.add_agent_run("fr", "task", "feat-001", "auto", "/tmp/log")
    await q.finish_agent_run(run.id, status="cancelled", result_snapshot={})
    fetched = await q.get_agent_run(run.id)
    assert fetched.result_snapshot == {}


@pytest.mark.asyncio
async def test_migrate_is_idempotent(tmp_path):
    """_migrate must be safe to call multiple times against the same DB."""
    q = TaskQueue(tmp_path / "t.db")
    await q.init()
    # Call _migrate explicitly multiple times — must not raise
    await q._migrate()
    await q._migrate()
    await q._migrate()
    # Verify new fields exist
    async with q._db.execute("PRAGMA table_info(tasks)") as cur:
        cols = {r["name"] async for r in cur}
    assert "spec" in cols
    async with q._db.execute("PRAGMA table_info(requirements)") as cur:
        cols = {r["name"] async for r in cur}
    assert "status" in cols
    async with q._db.execute("PRAGMA table_info(review_findings)") as cur:
        cols = {r["name"] async for r in cur}
    assert "run_id" in cols
    # Also verify re-open path works (the original scenario)
    await q.close()
    q2 = TaskQueue(tmp_path / "t.db")
    await q2.init()
    await q2.close()
