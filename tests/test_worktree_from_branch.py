import subprocess
from pathlib import Path
import pytest
import pytest_asyncio

from orchestra.core.worktree_manager import WorktreeManager


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], capture_output=True)
    (repo / "README.md").write_text("# test\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], capture_output=True, check=True)
    # Create a feature branch with a commit
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feat/my-feature"], capture_output=True, check=True)
    (repo / "feature.py").write_text("# feature code\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "add feature"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-"], capture_output=True, check=True)
    return repo


@pytest.fixture
def wt_mgr(git_repo, tmp_path):
    wt_dir = tmp_path / "worktrees"
    wt_dir.mkdir()
    return WorktreeManager(git_repo, wt_dir)


@pytest.mark.asyncio
async def test_ensure_worktree_from_branch_creates_worktree(wt_mgr, tmp_path):
    wt_path = await wt_mgr.ensure_worktree_from_branch("my-feature", "feat/my-feature")
    assert wt_path.exists()
    assert (wt_path / "feature.py").exists()
    assert wt_mgr.get_branch_name("my-feature") == "feat/my-feature"


@pytest.mark.asyncio
async def test_ensure_worktree_from_branch_idempotent(wt_mgr):
    wt1 = await wt_mgr.ensure_worktree_from_branch("my-feature", "feat/my-feature")
    wt2 = await wt_mgr.ensure_worktree_from_branch("my-feature", "feat/my-feature")
    assert wt1 == wt2


@pytest.mark.asyncio
async def test_ensure_worktree_from_branch_bad_branch(wt_mgr):
    with pytest.raises(RuntimeError, match="Failed to create worktree"):
        await wt_mgr.ensure_worktree_from_branch("nope", "nonexistent-branch")
