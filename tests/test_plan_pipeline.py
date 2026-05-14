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


@pytest.mark.asyncio
async def test_apply_success_pl_appends_plan_to_spec(tmp_path):
    """PL success should append plan text to task.spec."""
    tq, engine = await _make_tq(tmp_path)
    await tq.add_requirement("r1", "test")
    await tq.add_task("t1", "Feature A", requirement_id="r1")
    await tq.update_task_spec("t1", "# Spec\nBuild feature A")
    await tq.transition("t1", TaskStatus.PLANNING)

    # Simulate what AgentRunManager._apply_success does for PL
    plan_text = "## Implementation Plan\n\nStep 1: Create foo.py"
    current = await tq.get_task("t1")
    combined = (current.spec or "") + "\n\n---\n\n" + plan_text
    await tq.update_task_spec("t1", combined)
    await tq.transition("t1", TaskStatus.PLANNED)

    task = await tq.get_task("t1")
    assert task.status == TaskStatus.PLANNED
    assert "## Implementation Plan" in task.spec
    assert "# Spec" in task.spec
    assert "---" in task.spec
    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_driver_promotes_to_planning(tmp_path):
    """promote_ready_tasks should move IDEA → PLANNING."""
    tq, engine = await _make_tq(tmp_path)
    await tq.add_requirement("r1", "test")
    await tq.add_task("t1", "Feature A", requirement_id="r1")

    promoted = await tq.promote_ready_tasks()
    assert len(promoted) == 1
    assert promoted[0].status == TaskStatus.PLANNING

    planning = await tq.get_tasks(TaskStatus.PLANNING)
    assert len(planning) == 1
    assert planning[0].id == "t1"
    await engine.dispose()


@pytest.mark.asyncio
async def test_planned_auto_promotes_to_assigned(tmp_path):
    """PLANNED tasks can be promoted to ASSIGNED."""
    tq, engine = await _make_tq(tmp_path)
    await tq.add_requirement("r1", "test")
    await tq.add_task("t1", "Feature A", requirement_id="r1")
    await tq.transition("t1", TaskStatus.PLANNING)
    await tq.transition("t1", TaskStatus.PLANNED)

    planned = await tq.get_tasks(TaskStatus.PLANNED)
    for task in planned:
        await tq.transition(task.id, TaskStatus.ASSIGNED)

    t = await tq.get_task("t1")
    assert t.status == TaskStatus.ASSIGNED
    await engine.dispose()


@pytest.mark.asyncio
async def test_plan_appended_to_spec_survives_full_pipeline(tmp_path):
    """Spec + plan combined text is preserved through the full pipeline."""
    tq, engine = await _make_tq(tmp_path)
    await tq.add_requirement("r1", "test")
    await tq.add_task("t1", "Feature A", requirement_id="r1")

    # Set spec
    await tq.update_task_spec("t1", "# Acceptance Criteria\n- Feature works")

    # Go through planning
    await tq.transition("t1", TaskStatus.PLANNING)

    # Append plan
    current = await tq.get_task("t1")
    combined = current.spec + "\n\n---\n\n## Implementation Plan\nModify src/foo.py"
    await tq.update_task_spec("t1", combined)
    await tq.transition("t1", TaskStatus.PLANNED)

    # Continue through pipeline
    await tq.transition("t1", TaskStatus.ASSIGNED)
    await tq.transition("t1", TaskStatus.IN_PROGRESS)
    await tq.transition("t1", TaskStatus.IMPLEMENTED)

    # Verify spec+plan still intact
    task = await tq.get_task("t1")
    assert "# Acceptance Criteria" in task.spec
    assert "## Implementation Plan" in task.spec
    await engine.dispose()
