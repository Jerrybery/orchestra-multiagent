"""Comprehensive ORM-backed TaskQueue tests.

All tests use a fresh in-memory (tmp_path) SQLite database via the ORM
session-factory constructor: TaskQueue(session_factory).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from orchestra.core.task_queue import TaskQueue, TaskStatus


# ── Helper ──────────────────────────────────────────────────────────────────

async def _make_tq(tmp_path: Path):
    from orchestra.core.db.engine import create_db_engine, init_db

    engine, sf = create_db_engine(orchestra_dir=tmp_path)
    await init_db(engine)
    tq = TaskQueue(sf)
    await tq.init()
    return tq, engine


# ── Requirements ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_and_get_requirement(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    req = await tq.add_requirement("r1", "Do something important")
    assert req.id == "r1"
    assert req.content == "Do something important"
    assert req.status == "pending"

    fetched = await tq.get_requirement("r1")
    assert fetched is not None
    assert fetched.id == "r1"
    assert fetched.content == "Do something important"

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_all_requirements(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    t0 = time.time()
    await tq.add_requirement("r1", "First requirement")
    # Small sleep to guarantee distinct created_at values
    import asyncio
    await asyncio.sleep(0.01)
    await tq.add_requirement("r2", "Second requirement")

    reqs = await tq.get_all_requirements()
    assert len(reqs) == 2
    assert reqs[0].id == "r1"
    assert reqs[1].id == "r2"

    await engine.dispose()


@pytest.mark.asyncio
async def test_update_requirement_status(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.add_requirement("r1", "Update me")
    await tq.update_requirement_status("r1", "processed")

    req = await tq.get_requirement("r1")
    assert req.status == "processed"

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_nonexistent_requirement(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    result = await tq.get_requirement("does-not-exist")
    assert result is None

    await engine.dispose()


# ── Proposals ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_and_get_proposal(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.add_requirement("r1", "req")
    features = [{"id": "t1", "title": "Alpha"}, {"id": "t2", "title": "Beta"}]
    proposal = await tq.add_proposal("p1", "r1", features=features, summary="my summary")

    assert proposal.id == "p1"
    assert proposal.requirement_id == "r1"
    assert proposal.status == "pending"
    assert proposal.summary == "my summary"

    fetched = await tq.get_proposal("p1")
    assert fetched is not None
    assert fetched.features == features  # JSON roundtrip

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_proposals_filtered(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.add_requirement("r1", "req")
    await tq.add_proposal("p1", "r1", features=[])
    await tq.add_proposal("p2", "r1", features=[])
    await tq.update_proposal_status("p1", "approved")

    approved = await tq.get_proposals(status="approved")
    pending = await tq.get_proposals(status="pending")

    assert len(approved) == 1
    assert approved[0].id == "p1"
    assert len(pending) == 1
    assert pending[0].id == "p2"

    await engine.dispose()


@pytest.mark.asyncio
async def test_update_proposal_status(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.add_requirement("r1", "req")
    await tq.add_proposal("p1", "r1", features=[])
    await tq.update_proposal_status("p1", "rejected")

    p = await tq.get_proposal("p1")
    assert p.status == "rejected"

    await engine.dispose()


# ── Tasks ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_and_get_task(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    task = await tq.add_task(
        "t1", "Build feature", priority=5,
        depends_on=["t0"], spec_path="/some/spec.md",
    )
    assert task.id == "t1"
    assert task.title == "Build feature"
    assert task.priority == 5
    assert task.depends_on == ["t0"]
    assert task.spec_path == "/some/spec.md"
    assert task.status == TaskStatus.IDEA

    fetched = await tq.get_task("t1")
    assert fetched is not None
    assert fetched.id == "t1"
    assert fetched.status == TaskStatus.IDEA
    assert fetched.depends_on == ["t0"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_tasks_by_status(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.add_task("t1", "Task One")
    await tq.add_task("t2", "Task Two")
    await tq.transition("t2", TaskStatus.ASSIGNED)

    ideas = await tq.get_tasks(TaskStatus.IDEA)
    assigned = await tq.get_tasks(TaskStatus.ASSIGNED)

    assert len(ideas) == 1
    assert ideas[0].id == "t1"
    assert len(assigned) == 1
    assert assigned[0].id == "t2"

    await engine.dispose()


@pytest.mark.asyncio
async def test_transition_valid(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.add_task("t1", "Transition me")
    task = await tq.transition("t1", TaskStatus.ASSIGNED, assigned_to="agent-1")

    assert task.status == TaskStatus.ASSIGNED
    assert task.assigned_to == "agent-1"

    await engine.dispose()


@pytest.mark.asyncio
async def test_transition_invalid_raises(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.add_task("t1", "Invalid transition")

    with pytest.raises(ValueError, match="Invalid transition"):
        await tq.transition("t1", TaskStatus.DONE)

    await engine.dispose()


@pytest.mark.asyncio
async def test_update_task_fields(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.add_task("t1", "Update fields")
    updated = await tq.update_task_fields("t1", branch="feature/foo", assigned_to="bot-7")

    assert updated is not None
    assert updated.branch == "feature/foo"
    assert updated.assigned_to == "bot-7"

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_ready_tasks(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    # t1: no dependencies → ready
    await tq.add_task("t1", "Independent task")
    # t2: depends on t1 which is not DONE → not ready
    await tq.add_task("t2", "Dependent task", depends_on=["t1"])

    ready = await tq.get_ready_tasks()
    ready_ids = [t.id for t in ready]

    assert "t1" in ready_ids
    assert "t2" not in ready_ids

    await engine.dispose()


@pytest.mark.asyncio
async def test_all_tasks_summary(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.add_task("t1", "Task 1")
    await tq.add_task("t2", "Task 2")
    await tq.add_task("t3", "Task 3")
    await tq.transition("t3", TaskStatus.ASSIGNED)

    summary = await tq.all_tasks_summary()
    assert summary.get(TaskStatus.IDEA.value, 0) == 2
    assert summary.get(TaskStatus.ASSIGNED.value, 0) == 1

    await engine.dispose()


# ── Review Findings ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_review_finding_crud(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.add_task("t1", "Reviewed task")
    await tq.add_review_finding(
        task_id="t1",
        round=1,
        recommendation="approve",
        critical=[],
        important=[{"note": "minor nit"}],
        report_path="/reports/r1.md",
    )
    await tq.add_review_finding(
        task_id="t1",
        round=2,
        recommendation="approve",
        critical=[],
        important=[],
        report_path="/reports/r2.md",
    )

    latest = await tq.get_latest_review_finding("t1")
    assert latest is not None
    assert latest["round"] == 2
    assert latest["recommendation"] == "approve"

    by_round = await tq.get_review_finding("t1", round=1)
    assert by_round is not None
    assert by_round["round"] == 1
    assert by_round["important"] == [{"note": "minor nit"}]

    await engine.dispose()


# ── Discussions ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_discussion_crud(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    disc = await tq.upsert_discussion(100, "Initial Title", status="watching")
    assert disc.root_issue == 100
    assert disc.title == "Initial Title"
    assert disc.status == "watching"

    # Re-upsert should update title
    disc2 = await tq.upsert_discussion(100, "Updated Title")
    assert disc2.root_issue == 100
    assert disc2.title == "Updated Title"

    fetched = await tq.get_discussion(100)
    assert fetched is not None
    assert fetched.title == "Updated Title"

    all_discs = await tq.get_discussions()
    assert len(all_discs) == 1

    await tq.update_discussion(100, status="resolved")
    updated = await tq.get_discussion(100)
    assert updated.status == "resolved"

    await engine.dispose()


@pytest.mark.asyncio
async def test_discussion_issue_crud(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.upsert_discussion(200, "Parent discussion")

    issue = await tq.upsert_discussion_issue(
        root_issue=200,
        issue_number=201,
        title="Sub-issue",
        parent_issue=200,
        body="Initial body",
    )
    assert issue.issue_number == 201
    assert issue.title == "Sub-issue"
    assert issue.body == "Initial body"

    # Re-upsert should update title and body
    issue2 = await tq.upsert_discussion_issue(
        root_issue=200,
        issue_number=201,
        title="Updated sub-issue",
        body="Updated body",
    )
    assert issue2.issue_number == 201
    assert issue2.title == "Updated sub-issue"

    issues = await tq.get_discussion_issues(200)
    assert len(issues) == 1
    assert issues[0].issue_number == 201

    await tq.update_discussion_issue(201, last_comment_id=999, snapshot="snap")
    issues2 = await tq.get_discussion_issues(200)
    assert issues2[0].last_comment_id == 999
    assert issues2[0].snapshot == "snap"

    await engine.dispose()


# ── Draft Comments ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_draft_comment_crud(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.upsert_discussion(300, "Discussion for drafts")
    draft = await tq.add_draft_comment(
        root_issue=300, target_issue=301,
        body="Here is my comment", source="analyst",
    )
    assert draft.id is not None
    assert draft.body == "Here is my comment"
    assert draft.status == "pending"

    # get by id
    fetched = await tq.get_draft_comment(draft.id)
    assert fetched is not None
    assert fetched.body == "Here is my comment"

    # filter by status
    pending = await tq.get_draft_comments(status="pending")
    assert any(d.id == draft.id for d in pending)

    # update status
    await tq.update_draft_status(draft.id, "posted")
    posted = await tq.get_draft_comments(status="posted")
    assert any(d.id == draft.id for d in posted)

    # update body
    await tq.update_draft_body(draft.id, "Revised comment body")
    refetched = await tq.get_draft_comment(draft.id)
    assert refetched.body == "Revised comment body"

    await engine.dispose()


# ── Draft Messages ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_draft_message_crud(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.upsert_discussion(400, "Discussion for messages")
    draft = await tq.add_draft_comment(root_issue=400, target_issue=401, body="body")

    msg = await tq.add_draft_message(draft.id, role="user", content="Hello bot")
    assert msg.id is not None
    assert msg.role == "user"
    assert msg.content == "Hello bot"

    await tq.add_draft_message(draft.id, role="assistant", content="Hello user")

    messages = await tq.get_draft_messages(draft.id)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"

    await engine.dispose()


# ── Agent Runs ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_run_lifecycle(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    run = await tq.add_agent_run(
        role="implementer", target_kind="task", target_id="t1",
        mode="auto", log_path="/logs/run1.log",
    )
    assert run.id is not None
    assert run.role == "implementer"
    assert run.status == "running"
    assert run.finished_at is None

    fetched = await tq.get_agent_run(run.id)
    assert fetched is not None
    assert fetched.id == run.id

    await tq.finish_agent_run(
        run.id, status="succeeded",
        result_snapshot={"outcome": "ok"},
    )

    finished = await tq.get_agent_run(run.id)
    assert finished.status == "succeeded"
    assert finished.result_snapshot == {"outcome": "ok"}
    assert finished.finished_at is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_run_previous_run(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    run1 = await tq.add_agent_run(
        role="reviewer", target_kind="task", target_id="t2",
        mode="auto", log_path="/logs/run1.log",
    )
    await tq.finish_agent_run(run1.id, status="succeeded")

    run2 = await tq.add_agent_run(
        role="reviewer", target_kind="task", target_id="t2",
        mode="auto", log_path="/logs/run2.log",
    )
    assert run2.previous_run_id == run1.id

    await engine.dispose()


@pytest.mark.asyncio
async def test_list_agent_runs_filters(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.add_agent_run(
        role="implementer", target_kind="task", target_id="t1",
        mode="auto", log_path="/logs/a.log",
    )
    await tq.add_agent_run(
        role="reviewer", target_kind="task", target_id="t1",
        mode="auto", log_path="/logs/b.log",
    )

    implementers = await tq.list_agent_runs(role="implementer")
    reviewers = await tq.list_agent_runs(role="reviewer")

    assert len(implementers) == 1
    assert implementers[0].role == "implementer"
    assert len(reviewers) == 1
    assert reviewers[0].role == "reviewer"

    await engine.dispose()


# ── Auto Pauses ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_pause_crud(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    # Initially not paused
    assert not await tq.is_auto_paused("task", "t1")

    # Add pause
    await tq.add_auto_pause("task", "t1", reason="too many retries")

    assert await tq.is_auto_paused("task", "t1")

    pauses = await tq.list_auto_pauses()
    assert len(pauses) == 1
    assert pauses[0].target_id == "t1"
    assert pauses[0].reason == "too many retries"

    # Update (re-add)
    await tq.add_auto_pause("task", "t1", reason="updated reason")
    pauses2 = await tq.list_auto_pauses()
    assert len(pauses2) == 1
    assert pauses2[0].reason == "updated reason"

    # Remove
    await tq.remove_auto_pause("task", "t1")
    assert not await tq.is_auto_paused("task", "t1")
    pauses3 = await tq.list_auto_pauses()
    assert len(pauses3) == 0

    await engine.dispose()


# ── Run Messages ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_message_crud(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    run = await tq.add_agent_run(
        role="tester", target_kind="task", target_id="t1",
        mode="auto", log_path="/logs/run.log",
    )
    msg = await tq.add_run_message(run.id, role="user", content="Start the task")
    assert msg.id is not None
    assert msg.role == "user"
    assert msg.content == "Start the task"

    await tq.add_run_message(run.id, role="assistant", content="On it!")

    messages = await tq.get_run_messages(run.id)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"

    await engine.dispose()


# ── Proposal-Task lookup ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_proposal_for_task(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.add_requirement("r1", "req")
    features = [{"id": "t-abc", "title": "Alpha"}, {"id": "t-xyz", "title": "Beta"}]
    await tq.add_proposal("p1", "r1", features=features)

    result = await tq.get_proposal_for_task("t-abc")
    assert result == "p1"

    result2 = await tq.get_proposal_for_task("t-xyz")
    assert result2 == "p1"

    result3 = await tq.get_proposal_for_task("t-nonexistent")
    assert result3 is None

    await engine.dispose()


# ── Events ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_crud(tmp_path: Path):
    tq, engine = await _make_tq(tmp_path)

    await tq.add_event("task.created", {"task_id": "t1", "title": "First"})
    await tq.add_event("task.updated", {"task_id": "t1", "status": "assigned"})

    events = await tq.get_events()
    assert len(events) == 2
    assert events[0]["event"] == "task.created"
    assert events[1]["event"] == "task.updated"

    first_id = events[0]["id"]
    since = await tq.get_events(since_id=first_id)
    assert len(since) == 1
    assert since[0]["event"] == "task.updated"

    await engine.dispose()
