"""Tests for user identity management."""

import pytest
from orchestra.core.db.engine import create_db_engine, init_db
from orchestra.core.task_queue import TaskQueue


async def _make_tq(tmp_path):
    engine, sf = create_db_engine(orchestra_dir=tmp_path)
    await init_db(engine)
    return TaskQueue(sf), engine


@pytest.mark.asyncio
async def test_register_user(tmp_path):
    tq, engine = await _make_tq(tmp_path)
    user = await tq.register_user("jerry", display_name="Jerry")
    assert user.id == "jerry"
    assert user.display_name == "Jerry"
    assert user.last_seen_at is not None
    await engine.dispose()


@pytest.mark.asyncio
async def test_register_user_idempotent(tmp_path):
    tq, engine = await _make_tq(tmp_path)
    await tq.register_user("jerry", display_name="Jerry")
    user2 = await tq.register_user("jerry", display_name="Jerry Updated")
    assert user2.display_name == "Jerry"  # NOT updated
    assert user2.last_seen_at is not None
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_user(tmp_path):
    tq, engine = await _make_tq(tmp_path)
    await tq.register_user("jerry")
    user = await tq.get_user("jerry")
    assert user is not None
    assert user.id == "jerry"
    assert await tq.get_user("nobody") is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_users(tmp_path):
    tq, engine = await _make_tq(tmp_path)
    await tq.register_user("alice")
    await tq.register_user("bob")
    users = await tq.list_users()
    assert len(users) == 2
    await engine.dispose()
