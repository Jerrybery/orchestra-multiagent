"""Tests for the plan pipeline: PLANNING/PLANNED states and transitions."""

import pytest
from orchestra.core.db.engine import create_db_engine, init_db
from orchestra.core.task_queue import TaskQueue, TaskStatus


async def _make_tq(tmp_path):
    engine, sf = create_db_engine(orchestra_dir=tmp_path)
    await init_db(engine)
    tq = TaskQueue(sf)
    await tq.init()
    return tq, engine


@pytest.mark.asyncio
async def test_idea_to_planning(tmp_path):
    tq, engine = await _make_tq(tmp_path)
    await tq.add_requirement("r1", "test")
    await tq.add_task("t1", "Feature A", requirement_id="r1")
    t = await tq.transition("t1", TaskStatus.PLANNING)
    assert t.status == TaskStatus.PLANNING
    await engine.dispose()


@pytest.mark.asyncio
async def test_planning_to_planned(tmp_path):
    tq, engine = await _make_tq(tmp_path)
    await tq.add_requirement("r1", "test")
    await tq.add_task("t1", "Feature A", requirement_id="r1")
    await tq.transition("t1", TaskStatus.PLANNING)
    t = await tq.transition("t1", TaskStatus.PLANNED)
    assert t.status == TaskStatus.PLANNED
    await engine.dispose()


@pytest.mark.asyncio
async def test_planned_to_assigned(tmp_path):
    tq, engine = await _make_tq(tmp_path)
    await tq.add_requirement("r1", "test")
    await tq.add_task("t1", "Feature A", requirement_id="r1")
    await tq.transition("t1", TaskStatus.PLANNING)
    await tq.transition("t1", TaskStatus.PLANNED)
    t = await tq.transition("t1", TaskStatus.ASSIGNED)
    assert t.status == TaskStatus.ASSIGNED
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_pipeline_idea_to_done(tmp_path):
    tq, engine = await _make_tq(tmp_path)
    await tq.add_requirement("r1", "test")
    await tq.add_task("t1", "Feature A", requirement_id="r1")
    for status in [
        TaskStatus.PLANNING, TaskStatus.PLANNED, TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS, TaskStatus.IMPLEMENTED, TaskStatus.TESTING,
        TaskStatus.REVIEW, TaskStatus.ACCEPTED, TaskStatus.DONE,
    ]:
        await tq.transition("t1", status)
    t = await tq.get_task("t1")
    assert t.status == TaskStatus.DONE
    await engine.dispose()


@pytest.mark.asyncio
async def test_planning_to_failed(tmp_path):
    tq, engine = await _make_tq(tmp_path)
    await tq.add_requirement("r1", "test")
    await tq.add_task("t1", "Feature A", requirement_id="r1")
    await tq.transition("t1", TaskStatus.PLANNING)
    t = await tq.transition("t1", TaskStatus.FAILED)
    assert t.status == TaskStatus.FAILED
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_to_planning(tmp_path):
    tq, engine = await _make_tq(tmp_path)
    await tq.add_requirement("r1", "test")
    await tq.add_task("t1", "Feature A", requirement_id="r1")
    await tq.transition("t1", TaskStatus.PLANNING)
    await tq.transition("t1", TaskStatus.FAILED)
    t = await tq.transition("t1", TaskStatus.PLANNING)
    assert t.status == TaskStatus.PLANNING
    await engine.dispose()


@pytest.mark.asyncio
async def test_rejected_to_assigned_skips_planning(tmp_path):
    tq, engine = await _make_tq(tmp_path)
    await tq.add_requirement("r1", "test")
    await tq.add_task("t1", "Feature A", requirement_id="r1")
    for s in [TaskStatus.PLANNING, TaskStatus.PLANNED, TaskStatus.ASSIGNED,
              TaskStatus.IN_PROGRESS, TaskStatus.IMPLEMENTED, TaskStatus.TESTING,
              TaskStatus.REVIEW, TaskStatus.REJECTED]:
        await tq.transition("t1", s)
    t = await tq.transition("t1", TaskStatus.ASSIGNED)
    assert t.status == TaskStatus.ASSIGNED
    await engine.dispose()


@pytest.mark.asyncio
async def test_idea_to_assigned_blocked(tmp_path):
    tq, engine = await _make_tq(tmp_path)
    await tq.add_requirement("r1", "test")
    await tq.add_task("t1", "Feature A", requirement_id="r1")
    with pytest.raises(ValueError, match="Invalid transition"):
        await tq.transition("t1", TaskStatus.ASSIGNED)
    await engine.dispose()


@pytest.mark.asyncio
async def test_promote_ready_tasks_goes_to_planning(tmp_path):
    tq, engine = await _make_tq(tmp_path)
    await tq.add_requirement("r1", "test")
    await tq.add_task("t1", "Feature A", requirement_id="r1")
    promoted = await tq.promote_ready_tasks()
    assert len(promoted) == 1
    assert promoted[0].status == TaskStatus.PLANNING
    await engine.dispose()
