import pytest
from orchestra.core.context_manager import ContextManager
from orchestra.core.task_queue import TaskQueue


@pytest.mark.asyncio
async def test_spec_db_io(tmp_path):
    od = tmp_path / ".orchestra"
    cm = ContextManager(od)
    cm.init()
    q = TaskQueue(od / "tasks.db")
    await q.init()
    # add a fake task
    await q.add_task("feat-001", "demo", priority=0)
    cm.attach_db(q)

    await cm.write_spec_async("feat-001", "# spec content")
    spec = await cm.read_spec_async("feat-001")
    assert spec == "# spec content"
    # disk file should NOT be written (DB is source of truth)
    assert not cm.get_spec_path("feat-001").exists()


@pytest.mark.asyncio
async def test_spec_one_shot_migration(tmp_path):
    od = tmp_path / ".orchestra"
    cm = ContextManager(od)
    cm.init()
    # simulate legacy disk file
    cm.get_spec_path("feat-001").write_text("legacy spec")
    q = TaskQueue(od / "tasks.db")
    await q.init()
    await q.add_task("feat-001", "demo", priority=0)
    cm.attach_db(q)

    await cm.migrate_specs_from_disk()
    spec = await cm.read_spec_async("feat-001")
    assert spec == "legacy spec"
