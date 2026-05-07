import pytest
from pathlib import Path
from orchestra.core.task_queue import TaskQueue


@pytest.mark.asyncio
async def test_add_and_get_latest_finding(tmp_path: Path):
    q = TaskQueue(tmp_path / "t.db")
    await q.init()
    await q.add_requirement("r1", "test")
    await q.add_proposal("p1", "r1", features=[{"id": "t1", "title": "x"}])
    # NOTE: approve_proposal lives on Orchestrator, not TaskQueue.
    # Materialize the task directly via add_task instead.
    await q.add_task("t1", title="x", priority=0, depends_on=[], requirement_id="r1")

    await q.add_review_finding(
        task_id="t1", round=1, recommendation="reject",
        critical=[{"file": "a.py", "line": 10, "desc": "null deref"}],
        important=[],
        report_path="/tmp/rep1.md",
    )
    await q.add_review_finding(
        task_id="t1", round=2, recommendation="accept",
        critical=[], important=[],
        report_path="/tmp/rep2.md",
    )

    latest = await q.get_latest_review_finding("t1")
    assert latest["round"] == 2
    assert latest["recommendation"] == "accept"

    # Round 1 specifically
    earlier = await q.get_review_finding("t1", round=1)
    assert earlier["recommendation"] == "reject"
    assert earlier["critical"][0]["desc"] == "null deref"

    assert latest["critical"] == []
    assert latest["important"] == []
    await q.close()


@pytest.mark.asyncio
async def test_get_latest_returns_none_when_empty(tmp_path: Path):
    q = TaskQueue(tmp_path / "t.db")
    await q.init()
    assert await q.get_latest_review_finding("nonexistent") is None
    await q.close()
