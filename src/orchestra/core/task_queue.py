"""SQLAlchemy-backed task queue with state machine for feature lifecycle."""

from __future__ import annotations

import enum
import time
from typing import Optional

from sqlalchemy import select, func, delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from .db.models import (
    Requirement as RequirementModel,
    Proposal as ProposalModel,
    Task as TaskModel,
    Event as EventModel,
    Discussion as DiscussionModel,
    DiscussionIssue as DiscussionIssueModel,
    DraftComment as DraftCommentModel,
    DraftMessage as DraftMessageModel,
    AgentRun as AgentRunModel,
    AutoPause as AutoPauseModel,
    RunMessage as RunMessageModel,
    ReviewFinding as ReviewFindingModel,
)


class TaskStatus(str, enum.Enum):
    IDEA = "idea"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    TESTING = "testing"
    REVIEW = "review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DONE = "done"
    FAILED = "failed"


TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.IDEA: {TaskStatus.ASSIGNED, TaskStatus.FAILED},
    TaskStatus.ASSIGNED: {TaskStatus.IN_PROGRESS, TaskStatus.FAILED},
    TaskStatus.IN_PROGRESS: {TaskStatus.IMPLEMENTED, TaskStatus.FAILED},
    TaskStatus.IMPLEMENTED: {TaskStatus.TESTING},
    TaskStatus.TESTING: {TaskStatus.REVIEW, TaskStatus.FAILED},
    TaskStatus.REVIEW: {TaskStatus.ACCEPTED, TaskStatus.REJECTED},
    TaskStatus.REJECTED: {TaskStatus.ASSIGNED},
    TaskStatus.ACCEPTED: {TaskStatus.DONE},
    TaskStatus.FAILED: {TaskStatus.ASSIGNED},
}


# Type aliases — ORM models replace the old dataclasses.
Requirement = RequirementModel
Proposal = ProposalModel
Task = TaskModel
Discussion = DiscussionModel
DiscussionIssue = DiscussionIssueModel
DraftComment = DraftCommentModel
DraftMessage = DraftMessageModel
AgentRun = AgentRunModel
AutoPause = AutoPauseModel
RunMessage = RunMessageModel


class TaskQueue:
    """Async ORM-backed task queue with state machine enforcement."""

    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory

    async def init(self) -> None:
        pass

    async def close(self) -> None:
        pass

    # ── Requirements ───────────────────────────────────────────

    async def add_requirement(self, req_id: str, content: str) -> RequirementModel:
        now = time.time()
        req = RequirementModel(id=req_id, content=content, created_at=now)
        async with self._session_factory() as session:
            session.add(req)
            await session.commit()
            await session.refresh(req)
        return req

    async def get_requirement(self, req_id: str) -> Optional[RequirementModel]:
        async with self._session_factory() as session:
            return await session.get(RequirementModel, req_id)

    async def get_all_requirements(self) -> list[RequirementModel]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RequirementModel).order_by(RequirementModel.created_at.asc())
            )
            return list(result.scalars().all())

    async def update_requirement_status(self, req_id: str, status: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(RequirementModel)
                .where(RequirementModel.id == req_id)
                .values(status=status)
            )
            await session.commit()

    # ── Proposals ──────────────────────────────────────────────

    async def add_proposal(self, proposal_id: str, requirement_id: str,
                           features: list[dict], summary: str = "") -> ProposalModel:
        now = time.time()
        proposal = ProposalModel(
            id=proposal_id, requirement_id=requirement_id,
            features=features, summary=summary,
            status="pending", created_at=now,
        )
        async with self._session_factory() as session:
            session.add(proposal)
            await session.commit()
            await session.refresh(proposal)
        return proposal

    async def get_proposal(self, proposal_id: str) -> Optional[ProposalModel]:
        async with self._session_factory() as session:
            return await session.get(ProposalModel, proposal_id)

    async def get_proposals(self, status: str | None = None) -> list[ProposalModel]:
        async with self._session_factory() as session:
            stmt = select(ProposalModel).order_by(ProposalModel.created_at.desc())
            if status:
                stmt = stmt.where(ProposalModel.status == status)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_proposal_status(self, proposal_id: str, status: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(ProposalModel)
                .where(ProposalModel.id == proposal_id)
                .values(status=status)
            )
            await session.commit()

    # ── Events ─────────────────────────────────────────────────

    async def add_event(self, event: str, data: dict) -> None:
        async with self._session_factory() as session:
            session.add(EventModel(
                event=event,
                data={k: str(v) if not isinstance(v, (str, int, float, bool, list, dict, type(None))) else v
                      for k, v in data.items()},
                created_at=time.time(),
            ))
            await session.commit()

    async def get_events(self, since_id: int = 0, limit: int = 100) -> list[dict]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(EventModel)
                .where(EventModel.id > since_id)
                .order_by(EventModel.id.asc())
                .limit(limit)
            )
            return [
                {"id": e.id, "event": e.event, "data": e.data, "created_at": e.created_at}
                for e in result.scalars()
            ]

    # ── Tasks ──────────────────────────────────────────────────

    async def add_task(self, task_id: str, title: str, priority: int = 0,
                       depends_on: list[str] | None = None,
                       spec_path: str | None = None,
                       requirement_id: str | None = None,
                       source_issue: int | None = None) -> TaskModel:
        now = time.time()
        task = TaskModel(
            id=task_id, title=title, status=TaskStatus.IDEA.value,
            priority=priority, depends_on=depends_on or [],
            requirement_id=requirement_id, spec_path=spec_path,
            source_issue=source_issue, created_at=now, updated_at=now,
        )
        async with self._session_factory() as session:
            session.add(task)
            await session.commit()
            await session.refresh(task)
        return task

    async def get_task(self, task_id: str) -> Optional[TaskModel]:
        async with self._session_factory() as session:
            task = await session.get(TaskModel, task_id)
            if task:
                task.status = TaskStatus(task.status)
            return task

    async def get_tasks(self, status: TaskStatus | None = None) -> list[TaskModel]:
        async with self._session_factory() as session:
            stmt = select(TaskModel).order_by(
                TaskModel.priority.desc(), TaskModel.created_at.asc()
            )
            if status:
                stmt = stmt.where(TaskModel.status == status.value)
            result = await session.execute(stmt)
            tasks = list(result.scalars().all())
            for t in tasks:
                t.status = TaskStatus(t.status)
            return tasks

    async def update_task_fields(self, task_id: str, **kwargs) -> Optional[TaskModel]:
        async with self._session_factory() as session:
            task = await session.get(TaskModel, task_id)
            if not task:
                return None
            task.updated_at = time.time()
            for col in ("branch", "assigned_to", "worktree_path", "reject_reason"):
                if col in kwargs:
                    setattr(task, col, kwargs[col])
            await session.commit()
            await session.refresh(task)
            task.status = TaskStatus(task.status)
            return task

    async def transition(self, task_id: str, new_status: TaskStatus, **kwargs) -> TaskModel:
        async with self._session_factory() as session:
            task = await session.get(TaskModel, task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")

            current = TaskStatus(task.status)
            allowed = TRANSITIONS.get(current, set())
            if new_status not in allowed:
                raise ValueError(
                    f"Invalid transition: {current.value} → {new_status.value}. "
                    f"Allowed: {[s.value for s in allowed]}"
                )

            task.status = new_status.value
            task.updated_at = time.time()
            for col in ("assigned_to", "branch", "worktree_path",
                        "reject_reason", "fail_reason"):
                if col in kwargs:
                    setattr(task, col, kwargs[col])

            await session.commit()
            await session.refresh(task)
            task.status = TaskStatus(task.status)
            return task

    async def update_task_spec(self, task_id: str, spec: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(TaskModel)
                .where(TaskModel.id == task_id)
                .values(spec=spec, updated_at=time.time())
            )
            await session.commit()

    async def get_tasks_for_proposal(self, proposal_id: str) -> list[TaskModel]:
        prop = await self.get_proposal(proposal_id)
        if not prop:
            return []
        feature_ids = [f["id"] for f in prop.features]
        if not feature_ids:
            return []
        async with self._session_factory() as session:
            result = await session.execute(
                select(TaskModel).where(TaskModel.id.in_(feature_ids))
            )
            tasks = list(result.scalars().all())
            for t in tasks:
                t.status = TaskStatus(t.status)
            return tasks

    async def get_ready_tasks(self) -> list[TaskModel]:
        ideas = await self.get_tasks(TaskStatus.IDEA)
        if not ideas:
            return []
        async with self._session_factory() as session:
            result = await session.execute(
                select(TaskModel.id).where(TaskModel.status == TaskStatus.DONE.value)
            )
            done_ids = {row[0] for row in result}
        return [t for t in ideas if all(dep in done_ids for dep in t.depends_on)]

    async def promote_ready_tasks(self) -> list[TaskModel]:
        ready = await self.get_ready_tasks()
        promoted = []
        for task in ready:
            t = await self.transition(task.id, TaskStatus.ASSIGNED)
            promoted.append(t)
        return promoted

    async def all_tasks_summary(self) -> dict[str, int]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(TaskModel.status, func.count()).group_by(TaskModel.status)
            )
            return {row[0]: row[1] for row in result}

    # ── Review Findings ────────────────────────────────────────

    async def add_review_finding(
        self,
        task_id: str,
        round: int,
        recommendation: str,
        critical: list[dict],
        important: list[dict],
        report_path: str,
    ) -> None:
        async with self._session_factory() as session:
            session.add(ReviewFindingModel(
                task_id=task_id, round=round, recommendation=recommendation,
                critical=critical, important=important,
                report_path=report_path, created_at=time.time(),
            ))
            await session.commit()

    async def get_latest_review_finding(self, task_id: str) -> Optional[dict]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ReviewFindingModel)
                .where(ReviewFindingModel.task_id == task_id)
                .order_by(ReviewFindingModel.round.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if not row:
                return None
            return {
                "id": row.id, "task_id": row.task_id, "round": row.round,
                "recommendation": row.recommendation,
                "critical": row.critical or [],
                "important": row.important or [],
                "report_path": row.report_path, "created_at": row.created_at,
                "run_id": row.run_id,
            }

    async def get_review_finding(self, task_id: str, round: int) -> Optional[dict]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ReviewFindingModel)
                .where(ReviewFindingModel.task_id == task_id)
                .where(ReviewFindingModel.round == round)
            )
            row = result.scalar_one_or_none()
            if not row:
                return None
            return {
                "id": row.id, "task_id": row.task_id, "round": row.round,
                "recommendation": row.recommendation,
                "critical": row.critical or [],
                "important": row.important or [],
                "report_path": row.report_path, "created_at": row.created_at,
                "run_id": row.run_id,
            }

    # ── Discussions ────────────────────────────────────────────

    async def upsert_discussion(self, root_issue: int, title: str,
                                 status: str = "watching") -> DiscussionModel:
        now = time.time()
        async with self._session_factory() as session:
            existing = await session.get(DiscussionModel, root_issue)
            if existing:
                existing.title = title
                existing.updated_at = now
                await session.commit()
                await session.refresh(existing)
                return existing
            disc = DiscussionModel(
                root_issue=root_issue, title=title, status=status,
                created_at=now, updated_at=now,
            )
            session.add(disc)
            await session.commit()
            await session.refresh(disc)
            return disc

    async def get_discussion(self, root_issue: int) -> Optional[DiscussionModel]:
        async with self._session_factory() as session:
            return await session.get(DiscussionModel, root_issue)

    async def get_discussions(self, status: Optional[str] = None) -> list[DiscussionModel]:
        async with self._session_factory() as session:
            stmt = select(DiscussionModel).order_by(DiscussionModel.updated_at.desc())
            if status:
                stmt = stmt.where(DiscussionModel.status == status)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_discussion(self, root_issue: int, **kwargs) -> None:
        async with self._session_factory() as session:
            disc = await session.get(DiscussionModel, root_issue)
            if not disc:
                return
            disc.updated_at = time.time()
            for col in ("status", "last_analysis", "title"):
                if col in kwargs:
                    setattr(disc, col, kwargs[col])
            await session.commit()

    async def upsert_discussion_issue(self, root_issue: int, issue_number: int,
                                       title: str, parent_issue: Optional[int] = None,
                                       body: str = "") -> DiscussionIssueModel:
        now = time.time()
        async with self._session_factory() as session:
            result = await session.execute(
                select(DiscussionIssueModel)
                .where(DiscussionIssueModel.issue_number == issue_number)
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.title = title
                existing.body = body
                await session.commit()
                await session.refresh(existing)
                return existing
            issue = DiscussionIssueModel(
                root_issue=root_issue, issue_number=issue_number,
                title=title, parent_issue=parent_issue,
                body=body, created_at=now,
            )
            session.add(issue)
            await session.commit()
            await session.refresh(issue)
            return issue

    async def get_discussion_issues(self, root_issue: int) -> list[DiscussionIssueModel]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DiscussionIssueModel)
                .where(DiscussionIssueModel.root_issue == root_issue)
                .order_by(DiscussionIssueModel.issue_number)
            )
            return list(result.scalars().all())

    async def update_discussion_issue(self, issue_number: int, **kwargs) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DiscussionIssueModel)
                .where(DiscussionIssueModel.issue_number == issue_number)
            )
            issue = result.scalar_one_or_none()
            if not issue:
                return
            for col in ("last_comment_id", "snapshot", "body"):
                if col in kwargs:
                    setattr(issue, col, kwargs[col])
            await session.commit()

    # ── Draft Comments ─────────────────────────────────────────

    async def add_draft_comment(self, root_issue: int, target_issue: int,
                                 body: str, source: str = "analyst") -> DraftCommentModel:
        now = time.time()
        draft = DraftCommentModel(
            root_issue=root_issue, target_issue=target_issue,
            body=body, source=source, status="pending", created_at=now,
        )
        async with self._session_factory() as session:
            session.add(draft)
            await session.commit()
            await session.refresh(draft)
        return draft

    async def get_draft_comments(self, status: str = "pending") -> list[DraftCommentModel]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DraftCommentModel)
                .where(DraftCommentModel.status == status)
                .order_by(DraftCommentModel.created_at.desc())
            )
            return list(result.scalars().all())

    async def get_draft_comment(self, draft_id: int) -> Optional[DraftCommentModel]:
        async with self._session_factory() as session:
            return await session.get(DraftCommentModel, draft_id)

    async def update_draft_status(self, draft_id: int, status: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(DraftCommentModel)
                .where(DraftCommentModel.id == draft_id)
                .values(status=status)
            )
            await session.commit()

    async def update_draft_body(self, draft_id: int, body: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(DraftCommentModel)
                .where(DraftCommentModel.id == draft_id)
                .values(body=body)
            )
            await session.commit()

    # ── Draft Messages ─────────────────────────────────────────

    async def add_draft_message(self, draft_id: int, role: str,
                                 content: str) -> DraftMessageModel:
        now = time.time()
        msg = DraftMessageModel(
            draft_id=draft_id, role=role, content=content, created_at=now,
        )
        async with self._session_factory() as session:
            session.add(msg)
            await session.commit()
            await session.refresh(msg)
        return msg

    async def get_draft_messages(self, draft_id: int) -> list[DraftMessageModel]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DraftMessageModel)
                .where(DraftMessageModel.draft_id == draft_id)
                .order_by(DraftMessageModel.created_at.asc())
            )
            return list(result.scalars().all())

    # ── Agent Runs ─────────────────────────────────────────────

    async def add_agent_run(self, role: str, target_kind: str, target_id: str,
                            mode: str, log_path: str,
                            resumed_from_run_id: Optional[int] = None,
                            fallback_from_run_id: Optional[int] = None) -> AgentRunModel:
        now = time.time()
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentRunModel.id)
                .where(AgentRunModel.role == role)
                .where(AgentRunModel.target_id == target_id)
                .where(AgentRunModel.status == "succeeded")
                .order_by(AgentRunModel.started_at.desc())
                .limit(1)
            )
            row = result.first()
            previous_run_id = row[0] if row else None

            run = AgentRunModel(
                role=role, target_kind=target_kind, target_id=target_id,
                mode=mode, status="running", started_at=now, log_path=log_path,
                resumed_from_run_id=resumed_from_run_id,
                fallback_from_run_id=fallback_from_run_id,
                previous_run_id=previous_run_id,
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)
        return run

    async def get_agent_run(self, run_id: int) -> Optional[AgentRunModel]:
        async with self._session_factory() as session:
            return await session.get(AgentRunModel, run_id)

    async def list_agent_runs(self, target_id: Optional[str] = None,
                              role: Optional[str] = None,
                              status: Optional[str] = None,
                              limit: int = 50) -> list[AgentRunModel]:
        async with self._session_factory() as session:
            stmt = select(AgentRunModel).order_by(AgentRunModel.started_at.desc()).limit(limit)
            if target_id:
                stmt = stmt.where(AgentRunModel.target_id == target_id)
            if role:
                stmt = stmt.where(AgentRunModel.role == role)
            if status:
                stmt = stmt.where(AgentRunModel.status == status)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def finish_agent_run(self, run_id: int, status: str,
                               result_snapshot: Optional[dict] = None,
                               session_id: Optional[str] = None,
                               error_message: Optional[str] = None) -> None:
        async with self._session_factory() as session:
            run = await session.get(AgentRunModel, run_id)
            if not run:
                return
            run.status = status
            run.finished_at = time.time()
            run.result_snapshot = result_snapshot
            run.session_id = session_id
            run.error_message = error_message
            await session.commit()

    # ── Auto Pauses ────────────────────────────────────────────

    async def add_auto_pause(self, target_kind: str, target_id: str,
                              caused_by_run_id: Optional[int] = None,
                              reason: Optional[str] = None) -> None:
        async with self._session_factory() as session:
            existing = await session.get(AutoPauseModel, (target_kind, target_id))
            if existing:
                existing.paused_at = time.time()
                existing.caused_by_run_id = caused_by_run_id
                existing.reason = reason or ""
            else:
                session.add(AutoPauseModel(
                    target_kind=target_kind, target_id=target_id,
                    paused_at=time.time(), caused_by_run_id=caused_by_run_id,
                    reason=reason or "",
                ))
            await session.commit()

    async def remove_auto_pause(self, target_kind: str, target_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(AutoPauseModel)
                .where(AutoPauseModel.target_kind == target_kind)
                .where(AutoPauseModel.target_id == target_id)
            )
            await session.commit()

    async def is_auto_paused(self, target_kind: str, target_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.get(AutoPauseModel, (target_kind, target_id))
            return result is not None

    async def list_auto_pauses(self) -> list[AutoPauseModel]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AutoPauseModel).order_by(AutoPauseModel.paused_at.desc())
            )
            return list(result.scalars().all())

    # ── Run Messages ───────────────────────────────────────────

    async def add_run_message(self, run_id: int, role: str, content: str) -> RunMessageModel:
        now = time.time()
        msg = RunMessageModel(
            run_id=run_id, role=role, content=content, created_at=now,
        )
        async with self._session_factory() as session:
            session.add(msg)
            await session.commit()
            await session.refresh(msg)
        return msg

    async def get_run_messages(self, run_id: int) -> list[RunMessageModel]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RunMessageModel)
                .where(RunMessageModel.run_id == run_id)
                .order_by(RunMessageModel.created_at.asc())
            )
            return list(result.scalars().all())

    # ── Proposal-Task lookup (moved from orchestrator.py) ──────

    async def get_proposal_for_task(self, task_id: str) -> Optional[str]:
        async with self._session_factory() as session:
            result = await session.execute(select(ProposalModel))
            for p in result.scalars():
                if any(f.get("id") == task_id for f in (p.features or [])):
                    return p.id
        return None

    # ── User Identity ──────────────────────────────────────────

    async def register_user(self, user_id: str, display_name: str | None = None):
        """Register or touch a user. Idempotent: existing users get last_seen_at updated."""
        from .db.models import User as UserModel
        now = time.time()
        async with self._session_factory() as session:
            existing = await session.get(UserModel, user_id)
            if existing:
                existing.last_seen_at = now
                await session.commit()
                await session.refresh(existing)
                return existing
            user = UserModel(
                id=user_id, display_name=display_name,
                created_at=now, last_seen_at=now,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    async def get_user(self, user_id: str):
        from .db.models import User as UserModel
        async with self._session_factory() as session:
            return await session.get(UserModel, user_id)

    async def list_users(self) -> list:
        from .db.models import User as UserModel
        async with self._session_factory() as session:
            result = await session.execute(
                select(UserModel).order_by(UserModel.created_at.asc())
            )
            return list(result.scalars().all())
