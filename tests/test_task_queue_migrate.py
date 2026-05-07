import pytest
import aiosqlite
from pathlib import Path
from orchestra.core.task_queue import TaskQueue


@pytest.mark.asyncio
async def test_migrate_adds_columns_idempotent(tmp_path: Path):
    db_path = tmp_path / "tasks.db"
    q = TaskQueue(db_path)
    await q.init()
    await q.close()

    # Re-open and re-migrate — must not error or duplicate columns
    q2 = TaskQueue(db_path)
    await q2.init()

    async with aiosqlite.connect(str(db_path)) as conn:
        async with conn.execute("PRAGMA table_info(tasks)") as cur:
            cols = {row[1] async for row in cur}

    assert "fr_session_id" in cols
    assert "fail_reason" in cols
    await q2.close()


@pytest.mark.asyncio
async def test_migrate_creates_review_findings_table(tmp_path: Path):
    db_path = tmp_path / "tasks.db"
    q = TaskQueue(db_path)
    await q.init()
    async with aiosqlite.connect(str(db_path)) as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='review_findings'"
        ) as cur:
            rows = await cur.fetchall()
    assert len(rows) == 1
    await q.close()
