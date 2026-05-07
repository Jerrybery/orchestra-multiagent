"""Verify Proposal.status accepts the new "paused" value (Task 1.5)."""

from pathlib import Path

import pytest

from orchestra.core.task_queue import TaskQueue


@pytest.mark.asyncio
async def test_proposal_can_be_paused(tmp_path: Path):
    q = TaskQueue(tmp_path / "t.db")
    await q.init()
    await q.add_requirement("r1", "test")
    await q.add_proposal("p1", "r1", features=[{"id": "t1", "title": "x"}])
    await q.update_proposal_status("p1", "paused")
    p = await q.get_proposal("p1")
    assert p.status == "paused"
    # Resume back to approved
    await q.update_proposal_status("p1", "approved")
    p = await q.get_proposal("p1")
    assert p.status == "approved"
    await q.close()
