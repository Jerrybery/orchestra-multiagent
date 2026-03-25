"""Phase 1: Initialization tests.

Validates:
- Orchestra directory structure creation
- Context files populated with defaults
- Git repo init / detection
- TaskQueue schema creation
- Existing repo branch listing
"""

import subprocess
import pytest
import pytest_asyncio

from orchestra.core.task_queue import TaskQueue, TaskStatus
from orchestra.core.context_manager import ContextManager
from orchestra.core.worktree_manager import WorktreeManager


class TestContextInit:
    """Context directory structure and default files."""

    def test_directories_created(self, context_mgr):
        assert context_mgr.context_dir.is_dir()
        assert context_mgr.specs_dir.is_dir()
        assert context_mgr.contracts_dir.is_dir()
        assert context_mgr.reports_dir.is_dir()
        assert context_mgr.worktrees_dir.is_dir()
        assert context_mgr.logs_dir.is_dir()

    def test_default_files_exist(self, context_mgr):
        assert (context_mgr.context_dir / "architecture.md").is_file()
        assert (context_mgr.context_dir / "conventions.md").is_file()
        assert (context_mgr.context_dir / "glossary.md").is_file()

    def test_default_files_not_overwritten(self, context_mgr):
        """Re-init should not overwrite existing files."""
        arch = context_mgr.context_dir / "architecture.md"
        arch.write_text("Custom content")
        context_mgr.init()  # re-init
        assert arch.read_text() == "Custom content"

    def test_spec_read_write(self, context_mgr):
        context_mgr.write_spec("feat-001", "# Test Spec\nSome content")
        assert context_mgr.read_spec("feat-001") == "# Test Spec\nSome content"

    def test_spec_not_found(self, context_mgr):
        assert context_mgr.read_spec("feat-nonexistent") is None

    def test_agent_env_paths(self, context_mgr):
        env = context_mgr.get_agent_env("feat-001", "feature_realizer")
        assert "context_dir" in env
        assert "spec_file" in env
        assert "workspace" in env
        assert "feat-001" in env["spec_file"]


class TestTaskQueueInit:
    """TaskQueue schema and basic operations."""

    @pytest.mark.asyncio
    async def test_empty_queue(self, task_queue):
        tasks = await task_queue.get_tasks()
        assert tasks == []

    @pytest.mark.asyncio
    async def test_summary_empty(self, task_queue):
        summary = await task_queue.all_tasks_summary()
        assert summary == {}

    @pytest.mark.asyncio
    async def test_add_and_retrieve(self, task_queue):
        t = await task_queue.add_task("feat-001", "Test Feature")
        assert t.id == "feat-001"
        assert t.status == TaskStatus.IDEA

        fetched = await task_queue.get_task("feat-001")
        assert fetched is not None
        assert fetched.title == "Test Feature"


class TestGitRepoInit:
    """Git repo detection and worktree manager."""

    @pytest.mark.asyncio
    async def test_repo_exists(self, git_repo):
        assert (git_repo / ".git").is_dir()

    @pytest.mark.asyncio
    async def test_list_branches(self, worktree_mgr):
        branches = await worktree_mgr.list_branches()
        # Should have at least one branch (master or main)
        assert len(branches) >= 1
        names = [b["name"] for b in branches]
        assert any(n in ("main", "master") for n in names)

    @pytest.mark.asyncio
    async def test_ensure_repo_idempotent(self, worktree_mgr):
        """ensure_repo on an existing repo should not fail."""
        await worktree_mgr.ensure_repo()
        branches = await worktree_mgr.list_branches()
        assert len(branches) >= 1
