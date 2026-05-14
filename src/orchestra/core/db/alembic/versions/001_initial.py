"""Initial schema — 13 tables.

Revision ID: 001
Revises:
Create Date: 2026-05-14 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users
    op.create_table(
        "users",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("last_seen_at", sa.Float(), nullable=True),
    )

    # 2. requirements
    op.create_table(
        "requirements",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
    )

    # 3. proposals
    op.create_table(
        "proposals",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("requirement_id", sa.String(), sa.ForeignKey("requirements.id"), nullable=False),
        sa.Column("features", sa.JSON(), nullable=True),
        sa.Column("summary", sa.Text(), server_default=""),
        sa.Column("status", sa.String(), server_default="pending"),
        sa.Column("created_at", sa.Float(), nullable=False),
    )

    # 4. tasks
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="idea"),
        sa.Column("priority", sa.Integer(), server_default="0"),
        sa.Column("depends_on", sa.JSON(), nullable=True),
        sa.Column("requirement_id", sa.String(), sa.ForeignKey("requirements.id"), nullable=True),
        sa.Column("assigned_to", sa.String(), nullable=True),
        sa.Column("branch", sa.String(), nullable=True),
        sa.Column("worktree_path", sa.String(), nullable=True),
        sa.Column("spec_path", sa.String(), nullable=True),
        sa.Column("spec", sa.Text(), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("fail_reason", sa.Text(), nullable=True),
        sa.Column("source_issue", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("idx_tasks_status", "tasks", ["status"])
    op.create_index("idx_tasks_requirement", "tasks", ["requirement_id"])

    # 5. events
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
    )

    # 6. discussions
    op.create_table(
        "discussions",
        sa.Column("root_issue", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("title", sa.String(), server_default=""),
        sa.Column("status", sa.String(), server_default="watching"),
        sa.Column("last_analysis", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )

    # 7. discussion_issues
    op.create_table(
        "discussion_issues",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("root_issue", sa.Integer(), sa.ForeignKey("discussions.root_issue"), nullable=False),
        sa.Column("issue_number", sa.Integer(), unique=True, nullable=False),
        sa.Column("title", sa.String(), server_default=""),
        sa.Column("parent_issue", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("last_comment_id", sa.Integer(), server_default="0"),
        sa.Column("snapshot", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("idx_disc_issues_root", "discussion_issues", ["root_issue"])

    # 8. draft_comments
    op.create_table(
        "draft_comments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("root_issue", sa.Integer(), sa.ForeignKey("discussions.root_issue"), nullable=False),
        sa.Column("target_issue", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source", sa.String(), server_default="analyst"),
        sa.Column("status", sa.String(), server_default="pending"),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("idx_drafts_status", "draft_comments", ["status"])

    # 9. draft_messages
    op.create_table(
        "draft_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("draft_comments.id"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("idx_draft_messages_draft", "draft_messages", ["draft_id"])

    # 10. agent_runs
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("target_kind", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), server_default="auto"),
        sa.Column("status", sa.String(), server_default="running"),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("log_path", sa.String(), nullable=True),
        sa.Column("result_snapshot", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("previous_run_id", sa.Integer(), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("resumed_from_run_id", sa.Integer(), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("fallback_from_run_id", sa.Integer(), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("started_at", sa.Float(), nullable=False),
        sa.Column("finished_at", sa.Float(), nullable=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("idx_runs_target", "agent_runs", ["role", "target_id", "started_at"])
    op.create_index("idx_runs_status", "agent_runs", ["status"])

    # 11. auto_pauses
    op.create_table(
        "auto_pauses",
        sa.Column("target_kind", sa.String(), primary_key=True, nullable=False),
        sa.Column("target_id", sa.String(), primary_key=True, nullable=False),
        sa.Column("paused_at", sa.Float(), nullable=False),
        sa.Column("caused_by_run_id", sa.Integer(), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("reason", sa.Text(), server_default=""),
    )

    # 12. run_messages
    op.create_table(
        "run_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("idx_run_messages_run", "run_messages", ["run_id"])

    # 13. review_findings
    op.create_table(
        "review_findings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("recommendation", sa.String(), server_default="unknown"),
        sa.Column("critical", sa.JSON(), nullable=True),
        sa.Column("important", sa.JSON(), nullable=True),
        sa.Column("report_path", sa.String(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=True),
    )
    op.create_index("idx_review_findings_task", "review_findings", ["task_id", "round"])


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_index("idx_review_findings_task", table_name="review_findings")
    op.drop_table("review_findings")

    op.drop_index("idx_run_messages_run", table_name="run_messages")
    op.drop_table("run_messages")

    op.drop_table("auto_pauses")

    op.drop_index("idx_runs_status", table_name="agent_runs")
    op.drop_index("idx_runs_target", table_name="agent_runs")
    op.drop_table("agent_runs")

    op.drop_index("idx_draft_messages_draft", table_name="draft_messages")
    op.drop_table("draft_messages")

    op.drop_index("idx_drafts_status", table_name="draft_comments")
    op.drop_table("draft_comments")

    op.drop_index("idx_disc_issues_root", table_name="discussion_issues")
    op.drop_table("discussion_issues")

    op.drop_table("discussions")

    op.drop_table("events")

    op.drop_index("idx_tasks_requirement", table_name="tasks")
    op.drop_index("idx_tasks_status", table_name="tasks")
    op.drop_table("tasks")

    op.drop_table("proposals")

    op.drop_table("requirements")

    op.drop_table("users")
