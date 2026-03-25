"""Phase 4: Feature execution tests (FR/FI).

Validates:
- State machine transitions (full lifecycle)
- Invalid transitions are rejected
- Worktree creation for feature branches
- Worktree merge and cleanup
- Agent spawner basics (process lifecycle)
"""

import pytest
import pytest_asyncio

from orchestra.core.task_queue import TaskQueue, TaskStatus
from orchestra.core.agent_spawner import AgentSpawner, AgentRole, AgentState


class TestStateMachine:
    """Task status transitions."""

    @pytest.mark.asyncio
    async def test_full_happy_path(self, task_queue):
        """Walk a task through the entire lifecycle."""
        await task_queue.add_task("feat-001", "Test", priority=1)

        t = await task_queue.transition("feat-001", TaskStatus.ASSIGNED)
        assert t.status == TaskStatus.ASSIGNED

        t = await task_queue.transition("feat-001", TaskStatus.IN_PROGRESS, assigned_to="fr-001")
        assert t.assigned_to == "fr-001"

        t = await task_queue.transition("feat-001", TaskStatus.IMPLEMENTED)
        assert t.status == TaskStatus.IMPLEMENTED

        t = await task_queue.transition("feat-001", TaskStatus.TESTING)
        t = await task_queue.transition("feat-001", TaskStatus.REVIEW)
        t = await task_queue.transition("feat-001", TaskStatus.ACCEPTED)
        t = await task_queue.transition("feat-001", TaskStatus.DONE)
        assert t.status == TaskStatus.DONE

    @pytest.mark.asyncio
    async def test_reject_and_retry(self, task_queue):
        """Rejected tasks go back to ASSIGNED."""
        await task_queue.add_task("feat-001", "Test")
        await task_queue.transition("feat-001", TaskStatus.ASSIGNED)
        await task_queue.transition("feat-001", TaskStatus.IN_PROGRESS)
        await task_queue.transition("feat-001", TaskStatus.IMPLEMENTED)
        await task_queue.transition("feat-001", TaskStatus.TESTING)
        await task_queue.transition("feat-001", TaskStatus.REVIEW)

        t = await task_queue.transition("feat-001", TaskStatus.REJECTED, reject_reason="Missing tests")
        assert t.status == TaskStatus.REJECTED
        assert t.reject_reason == "Missing tests"

        t = await task_queue.transition("feat-001", TaskStatus.ASSIGNED)
        assert t.status == TaskStatus.ASSIGNED

    @pytest.mark.asyncio
    async def test_invalid_transition(self, task_queue):
        """Cannot skip states."""
        await task_queue.add_task("feat-001", "Test")
        with pytest.raises(ValueError, match="Invalid transition"):
            await task_queue.transition("feat-001", TaskStatus.IMPLEMENTED)  # skips ASSIGNED, IN_PROGRESS

    @pytest.mark.asyncio
    async def test_transition_nonexistent_task(self, task_queue):
        with pytest.raises(ValueError, match="not found"):
            await task_queue.transition("feat-missing", TaskStatus.ASSIGNED)


class TestWorktreeLifecycle:
    """Git worktree creation, listing, merge, cleanup."""

    @pytest.mark.asyncio
    async def test_create_worktree(self, worktree_mgr):
        wt = await worktree_mgr.create_worktree("feat-001")
        assert wt.is_dir()
        # Should have .git reference (worktrees use a file, not a dir)
        assert (wt / ".git").exists()

    @pytest.mark.asyncio
    async def test_create_duplicate_worktree(self, worktree_mgr):
        """Creating the same worktree twice should not crash."""
        wt1 = await worktree_mgr.create_worktree("feat-001")
        wt2 = await worktree_mgr.create_worktree("feat-001")
        assert wt1 == wt2

    @pytest.mark.asyncio
    async def test_list_worktrees(self, worktree_mgr):
        await worktree_mgr.create_worktree("feat-001")
        wts = await worktree_mgr.list_worktrees()
        # At least 2: main worktree + feat-001
        assert len(wts) >= 2

    @pytest.mark.asyncio
    async def test_merge_and_cleanup(self, worktree_mgr):
        """Create a worktree, add a file, merge back, cleanup."""
        wt = await worktree_mgr.create_worktree("feat-001")

        # Make a change in the worktree
        test_file = wt / "feature.txt"
        test_file.write_text("new feature code")
        import subprocess
        subprocess.run(["git", "-C", str(wt), "add", "."], capture_output=True, check=True)
        subprocess.run(["git", "-C", str(wt), "commit", "-m", "Add feature"], capture_output=True, check=True)

        # Merge
        result = await worktree_mgr.merge_to_main("feat-001")
        assert result is True

        # The file should now exist in the main repo
        assert (worktree_mgr.repo_dir / "feature.txt").is_file()

        # Cleanup
        await worktree_mgr.cleanup_worktree("feat-001")
        assert not wt.is_dir()

    @pytest.mark.asyncio
    async def test_branch_listing_excludes_feat_branches(self, worktree_mgr):
        """list_branches should exclude feat/ branches."""
        await worktree_mgr.create_worktree("feat-001")
        branches = await worktree_mgr.list_branches()
        names = [b["name"] for b in branches]
        assert "feat/feat-001" not in names


class TestAgentSpawner:
    """Agent process lifecycle (using 'echo' as mock claude)."""

    @pytest.mark.asyncio
    async def test_spawn_and_wait(self, tmp_path):
        spawner = AgentSpawner(claude_cmd="echo", max_turns=10)
        handle = await spawner.spawn(
            role=AgentRole.FEATURE_REALIZER,
            system_prompt="",
            task_prompt="hello world",
            cwd=tmp_path,
            task_id="feat-001",
            log_path=tmp_path / "test.log",
        )
        assert handle.state == AgentState.RUNNING

        result = await spawner.wait(handle)
        assert result.exit_code == 0
        # echo with -p flag and other args — just check it completed
        assert handle.state == AgentState.FINISHED

        # Log file should exist
        assert (tmp_path / "test.log").is_file()

    @pytest.mark.asyncio
    async def test_running_count(self, tmp_path):
        spawner = AgentSpawner(claude_cmd="sleep", max_turns=10)
        # spawn a long-running process
        handle = await spawner.spawn(
            role=AgentRole.FEATURE_REALIZER,
            system_prompt="",
            task_prompt="10",  # sleep 10
            cwd=tmp_path,
        )
        assert spawner.running_count(AgentRole.FEATURE_REALIZER) == 1
        assert spawner.running_count(AgentRole.FEATURE_INTERPRETER) == 0

        await spawner.kill(handle.agent_id)
        assert handle.state == AgentState.FAILED

    @pytest.mark.asyncio
    async def test_get_running(self, tmp_path):
        spawner = AgentSpawner(claude_cmd="sleep", max_turns=10)
        await spawner.spawn(role=AgentRole.FEATURE_REALIZER, system_prompt="",
                            task_prompt="10", cwd=tmp_path, task_id="feat-001")
        await spawner.spawn(role=AgentRole.FEATURE_INTERPRETER, system_prompt="",
                            task_prompt="10", cwd=tmp_path, task_id="feat-002")

        all_running = spawner.get_running()
        assert len(all_running) == 2

        fr_only = spawner.get_running(AgentRole.FEATURE_REALIZER)
        assert len(fr_only) == 1

        # Cleanup
        for h in all_running:
            await spawner.kill(h.agent_id)
