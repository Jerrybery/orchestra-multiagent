"""Tests for ORM-based DB initialization (replaces old aiosqlite migration tests)."""

import pytest
from pathlib import Path
from orchestra.core.task_queue import TaskQueue
from orchestra.core.db.engine import create_db_engine, init_db


@pytest.mark.asyncio
async def test_init_db_creates_all_tables(tmp_path: Path):
    engine, sf = create_db_engine(orchestra_dir=tmp_path)
    await init_db(engine)
    q = TaskQueue(sf)
    await q.init()
    await q.close()

    # Re-open and re-init — must not error or duplicate tables
    engine2, sf2 = create_db_engine(orchestra_dir=tmp_path)
    await init_db(engine2)
    q2 = TaskQueue(sf2)
    await q2.init()

    # Verify key tables exist by exercising CRUD
    await q2.add_task("t1", "Test task")
    t = await q2.get_task("t1")
    assert t is not None
    assert t.title == "Test task"
    await q2.close()
    await engine.dispose()
    await engine2.dispose()


@pytest.mark.asyncio
async def test_init_db_creates_review_findings_table(tmp_path: Path):
    engine, sf = create_db_engine(orchestra_dir=tmp_path)
    await init_db(engine)
    q = TaskQueue(sf)
    await q.init()

    # Verify review_findings table works
    await q.add_task("t1", "Test task")
    await q.add_review_finding(
        task_id="t1", round=1, recommendation="approve",
        critical=[], important=[], report_path="/tmp/r.md",
    )
    finding = await q.get_latest_review_finding("t1")
    assert finding is not None
    assert finding["recommendation"] == "approve"
    await q.close()
    await engine.dispose()
