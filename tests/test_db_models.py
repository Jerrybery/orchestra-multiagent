"""Verify ORM models can create tables and perform basic CRUD."""

import pytest
from sqlalchemy import select, text

from orchestra.core.db.models import Base, Requirement, Task, User
from orchestra.core.db.engine import create_db_engine, init_db


@pytest.mark.asyncio
async def test_create_all_tables(tmp_path):
    engine, sf = create_db_engine(orchestra_dir=tmp_path)
    await init_db(engine)

    async with engine.begin() as conn:
        tables = await conn.run_sync(
            lambda sync_conn: list(sync_conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ))
        )
    table_names = {row[0] for row in tables}
    assert "requirements" in table_names
    assert "tasks" in table_names
    assert "users" in table_names
    assert "agent_runs" in table_names
    assert len(table_names) >= 13
    await engine.dispose()


@pytest.mark.asyncio
async def test_basic_crud(tmp_path):
    engine, sf = create_db_engine(orchestra_dir=tmp_path)
    await init_db(engine)

    async with sf() as session:
        req = Requirement(id="r1", content="Build feature X")
        session.add(req)
        await session.commit()

    async with sf() as session:
        result = await session.execute(
            select(Requirement).where(Requirement.id == "r1")
        )
        r = result.scalar_one()
        assert r.content == "Build feature X"
        assert r.status == "pending"
        assert r.created_at is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_task_json_fields(tmp_path):
    engine, sf = create_db_engine(orchestra_dir=tmp_path)
    await init_db(engine)

    async with sf() as session:
        req = Requirement(id="r1", content="test")
        session.add(req)
        await session.flush()
        task = Task(
            id="t1", title="Test task",
            depends_on=["t0"], requirement_id="r1",
        )
        session.add(task)
        await session.commit()

    async with sf() as session:
        t = await session.get(Task, "t1")
        assert t.depends_on == ["t0"]
        assert t.status == "idea"
        assert t.priority == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_env_var_override(tmp_path, monkeypatch):
    db_path = tmp_path / "env_test.db"
    monkeypatch.setenv("ORCHESTRA_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    engine, sf = create_db_engine()
    await init_db(engine)

    async with sf() as session:
        session.add(User(id="jerry", display_name="Jerry"))
        await session.commit()

    async with sf() as session:
        u = await session.get(User, "jerry")
        assert u.display_name == "Jerry"

    monkeypatch.delenv("ORCHESTRA_DATABASE_URL")
    await engine.dispose()
