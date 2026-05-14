"""ORM models for Orchestra — 13 tables (12 existing + users)."""

from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import (
    String, Integer, Text, Float, Index,
    ForeignKey, JSON, func,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship,
)
from sqlalchemy.types import TypeDecorator, DateTime


class EpochDateTime(TypeDecorator):
    """DateTime that stores as epoch float in SQLite, native TIMESTAMP in PG."""
    impl = Float
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        if isinstance(value, (int, float)):
            return float(value)
        return value.timestamp()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, datetime.datetime):
            return value
        return datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc)

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(DateTime(timezone=True))
        return dialect.type_descriptor(Float())


def _now_epoch():
    import time
    return time.time()


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[float] = mapped_column(EpochDateTime, default=_now_epoch)
    last_seen_at: Mapped[Optional[float]] = mapped_column(EpochDateTime, nullable=True)


class Requirement(Base):
    __tablename__ = "requirements"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(EpochDateTime, nullable=False, default=_now_epoch)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    user_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    tasks: Mapped[list["Task"]] = relationship(back_populates="requirement")
    proposals: Mapped[list["Proposal"]] = relationship(back_populates="requirement")


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    requirement_id: Mapped[str] = mapped_column(String, ForeignKey("requirements.id"), nullable=False)
    features: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[float] = mapped_column(EpochDateTime, nullable=False, default=_now_epoch)
    requirement: Mapped["Requirement"] = relationship(back_populates="proposals")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("idx_tasks_status", "status"),
        Index("idx_tasks_requirement", "requirement_id"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="idea")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    requirement_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("requirements.id"), nullable=True)
    assigned_to: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    branch: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    worktree_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    spec_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    spec: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reject_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fail_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_issue: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[float] = mapped_column(EpochDateTime, nullable=False, default=_now_epoch)
    updated_at: Mapped[float] = mapped_column(EpochDateTime, nullable=False, default=_now_epoch)
    user_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    requirement: Mapped[Optional["Requirement"]] = relationship(back_populates="tasks")


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event: Mapped[str] = mapped_column(String, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(EpochDateTime, nullable=False, default=_now_epoch)
    user_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("users.id"), nullable=True)


class Discussion(Base):
    __tablename__ = "discussions"
    root_issue: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="watching")
    last_analysis: Mapped[Optional[str]] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(EpochDateTime, nullable=False, default=_now_epoch)
    updated_at: Mapped[float] = mapped_column(EpochDateTime, nullable=False, default=_now_epoch)
    issues: Mapped[list["DiscussionIssue"]] = relationship(back_populates="discussion")


class DiscussionIssue(Base):
    __tablename__ = "discussion_issues"
    __table_args__ = (Index("idx_disc_issues_root", "root_issue"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    root_issue: Mapped[int] = mapped_column(Integer, ForeignKey("discussions.root_issue"), nullable=False)
    issue_number: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String, default="")
    parent_issue: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    body: Mapped[Optional[str]] = mapped_column(Text, default="")
    last_comment_id: Mapped[int] = mapped_column(Integer, default=0)
    snapshot: Mapped[Optional[str]] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(EpochDateTime, nullable=False, default=_now_epoch)
    discussion: Mapped["Discussion"] = relationship(back_populates="issues")


class DraftComment(Base):
    __tablename__ = "draft_comments"
    __table_args__ = (Index("idx_drafts_status", "status"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    root_issue: Mapped[int] = mapped_column(Integer, ForeignKey("discussions.root_issue"), nullable=False)
    target_issue: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String, default="analyst")
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[float] = mapped_column(EpochDateTime, nullable=False, default=_now_epoch)


class DraftMessage(Base):
    __tablename__ = "draft_messages"
    __table_args__ = (Index("idx_draft_messages_draft", "draft_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(Integer, ForeignKey("draft_comments.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(EpochDateTime, nullable=False, default=_now_epoch)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("idx_runs_target", "role", "target_id", "started_at"),
        Index("idx_runs_status", "status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    target_kind: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, default="auto")
    status: Mapped[str] = mapped_column(String, default="running")
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    log_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    result_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    previous_run_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agent_runs.id"), nullable=True)
    resumed_from_run_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agent_runs.id"), nullable=True)
    fallback_from_run_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agent_runs.id"), nullable=True)
    started_at: Mapped[float] = mapped_column(EpochDateTime, nullable=False, default=_now_epoch)
    finished_at: Mapped[Optional[float]] = mapped_column(EpochDateTime, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("users.id"), nullable=True)


class AutoPause(Base):
    __tablename__ = "auto_pauses"
    target_kind: Mapped[str] = mapped_column(String, primary_key=True)
    target_id: Mapped[str] = mapped_column(String, primary_key=True)
    paused_at: Mapped[float] = mapped_column(EpochDateTime, nullable=False, default=_now_epoch)
    caused_by_run_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agent_runs.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")


class RunMessage(Base):
    __tablename__ = "run_messages"
    __table_args__ = (Index("idx_run_messages_run", "run_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("agent_runs.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(EpochDateTime, nullable=False, default=_now_epoch)


class ReviewFinding(Base):
    __tablename__ = "review_findings"
    __table_args__ = (Index("idx_review_findings_task", "task_id", "round"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String, ForeignKey("tasks.id"), nullable=False)
    run_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agent_runs.id"), nullable=True)
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    recommendation: Mapped[str] = mapped_column(String, default="unknown")
    critical: Mapped[list] = mapped_column(JSON, default=list)
    important: Mapped[list] = mapped_column(JSON, default=list)
    report_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[float] = mapped_column(EpochDateTime, default=_now_epoch)
