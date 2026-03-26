"""Git worktree lifecycle management."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class WorktreeManager:
    """Create, merge, and clean up git worktrees for feature branches."""

    def __init__(self, repo_dir: Path, worktrees_dir: Path):
        self.repo_dir = repo_dir
        self.worktrees_dir = worktrees_dir
        self._branch_cache: dict[str, str] = {}

    async def _run(self, *cmd: str, cwd: Path | None = None) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd or self.repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode().strip(), stderr.decode().strip()

    async def ensure_repo(self) -> None:
        """Ensure the repo directory is a git repository."""
        if not (self.repo_dir / ".git").exists():
            rc, out, err = await self._run("git", "init", str(self.repo_dir))
            if rc != 0:
                raise RuntimeError(f"git init failed: {err}")
            # Create initial commit so branches can be created
            readme = self.repo_dir / "README.md"
            if not readme.exists():
                readme.write_text("# Project\n")
            await self._run("git", "add", ".")
            await self._run("git", "commit", "-m", "Initial commit")
            log.info("Initialized git repo at %s", self.repo_dir)

    def _branch_name(self, task_id: str, title: str = "") -> str:
        """Generate branch name from task title, e.g. feat/001-keeper-app-scaffold."""
        if title:
            import re
            # Slugify: lowercase, replace non-alphanum with hyphens, trim
            slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:50]
            num = task_id.replace("feat-", "")
            return f"feat/{num}-{slug}"
        return f"feat/{task_id}"

    def get_branch_name(self, task_id: str) -> str:
        """Get the branch name for a task (from cache or fallback)."""
        return self._branch_cache.get(task_id, f"feat/{task_id}")

    async def create_worktree(self, task_id: str, title: str = "") -> Path:
        """Create a new worktree + branch for a feature task."""
        branch = self._branch_name(task_id, title)
        self._branch_cache[task_id] = branch
        wt_path = self.worktrees_dir / task_id

        if wt_path.exists():
            log.warning("Worktree already exists: %s", wt_path)
            return wt_path

        wt_path.mkdir(parents=True, exist_ok=True)

        # Create branch and worktree in one step
        rc, out, err = await self._run(
            "git", "worktree", "add", "-b", branch, str(wt_path)
        )
        if rc != 0:
            raise RuntimeError(f"Failed to create worktree for {task_id}: {err}")

        log.info("Created worktree %s on branch %s", wt_path, branch)
        return wt_path

    async def _has_remote(self) -> bool:
        rc, _, _ = await self._run("git", "remote", "get-url", "origin")
        return rc == 0

    async def push_branch(self, task_id: str) -> bool:
        """Push a feature branch to the remote."""
        branch = self.get_branch_name(task_id)

        if not await self._has_remote():
            log.info("No remote 'origin' — skipping push for %s", branch)
            return False

        rc, out, err = await self._run("git", "push", "-u", "origin", branch)
        if rc != 0:
            log.warning("Push failed for %s: %s", branch, err)
            return False

        log.info("Pushed %s to origin", branch)
        return True

    async def create_pr(self, task_id: str, title: str, body: str) -> tuple[bool, str]:
        """Create a pull request for a feature branch. Returns (success, pr_url)."""
        branch = self.get_branch_name(task_id)

        if not await self._has_remote():
            return False, "no remote"

        # Ensure branch is pushed
        await self.push_branch(task_id)

        # Get main branch name
        rc, main_branch, _ = await self._run("git", "symbolic-ref", "--short", "HEAD")
        if rc != 0:
            main_branch = "main"

        rc, out, err = await self._run(
            "gh", "pr", "create",
            "--base", main_branch,
            "--head", branch,
            "--title", title,
            "--body", body,
        )
        if rc != 0:
            # Maybe PR already exists
            if "already exists" in err.lower():
                # Get existing PR URL
                rc2, url, _ = await self._run(
                    "gh", "pr", "view", branch, "--json", "url", "--jq", ".url"
                )
                if rc2 == 0 and url:
                    log.info("PR already exists for %s: %s", branch, url)
                    return True, url
            log.error("Failed to create PR for %s: %s", branch, err)
            return False, err[:200]

        pr_url = out.strip()
        log.info("Created PR for %s: %s", branch, pr_url)
        return True, pr_url

    async def push_main(self) -> bool:
        """Push the main branch to remote after merge."""
        if not await self._has_remote():
            return False

        rc, main_branch, _ = await self._run("git", "symbolic-ref", "--short", "HEAD")
        if rc != 0:
            main_branch = "main"

        rc, out, err = await self._run("git", "push", "origin", main_branch)
        if rc != 0:
            log.warning("Push main failed: %s", err)
            return False

        log.info("Pushed %s to origin", main_branch)
        return True

    async def merge_to_main(self, task_id: str) -> tuple[bool, str]:
        """Merge a feature branch back into main.

        Returns (success, message). On conflict, attempts rebase in the worktree
        first, then retries the merge.
        """
        branch = self.get_branch_name(task_id)
        wt_path = self.worktrees_dir / task_id

        # Get the main branch name
        rc, main_branch, _ = await self._run(
            "git", "symbolic-ref", "--short", "HEAD"
        )
        if rc != 0:
            main_branch = "main"

        # Try merge
        rc, out, err = await self._run("git", "merge", branch, "--no-ff",
                                       "-m", f"Merge {branch}: completed")
        if rc == 0:
            log.info("Merged %s into %s", branch, main_branch)
            return True, "merged"

        # Merge conflict — abort it
        log.warning("Merge conflict for %s, attempting rebase", branch)
        await self._run("git", "merge", "--abort")

        # Try rebasing the feature branch onto main inside the worktree
        if wt_path.exists():
            rc_rb, _, err_rb = await self._run(
                "git", "rebase", main_branch,
                cwd=wt_path,
            )
            if rc_rb == 0:
                # Rebase succeeded — retry merge
                rc2, _, err2 = await self._run("git", "merge", branch, "--no-ff",
                                               "-m", f"Merge {branch}: completed (rebased)")
                if rc2 == 0:
                    log.info("Merged %s into %s after rebase", branch, main_branch)
                    return True, "merged after rebase"
                else:
                    log.error("Merge still failed after rebase for %s: %s", branch, err2)
                    await self._run("git", "merge", "--abort")
            else:
                # Rebase also failed — abort it
                log.error("Rebase failed for %s: %s", branch, err_rb)
                await self._run("git", "rebase", "--abort", cwd=wt_path)

        return False, f"merge conflict: {err[:200]}"

    async def cleanup_worktree(self, task_id: str) -> None:
        """Remove local worktree. Keep branches (local + remote) for history."""
        wt_path = self.worktrees_dir / task_id

        if wt_path.exists():
            rc, _, err = await self._run("git", "worktree", "remove", str(wt_path), "--force")
            if rc != 0:
                log.warning("Failed to remove worktree %s: %s", wt_path, err)

        log.info("Cleaned up worktree for %s (branches preserved)", task_id)

    async def list_worktrees(self) -> list[dict[str, str]]:
        rc, out, _ = await self._run("git", "worktree", "list", "--porcelain")
        if rc != 0 or not out:
            return []

        worktrees = []
        current: dict[str, str] = {}
        for line in out.split("\n"):
            if not line.strip():
                if current:
                    worktrees.append(current)
                    current = {}
            elif line.startswith("worktree "):
                current["path"] = line.split(" ", 1)[1]
            elif line.startswith("branch "):
                current["branch"] = line.split(" ", 1)[1]
        if current:
            worktrees.append(current)

        return worktrees

    async def list_branches(self) -> list[dict[str, str]]:
        """List all git branches with last commit info."""
        rc, out, _ = await self._run(
            "git", "branch", "-a", "--format=%(refname:short)\t%(objectname:short)\t%(subject)\t%(creatordate:relative)"
        )
        if rc != 0 or not out:
            return []

        branches = []
        for line in out.splitlines():
            parts = line.split("\t", 3)
            if len(parts) >= 2:
                name = parts[0].strip()
                # Skip orchestra feature branches — they show in the DAG
                if name.startswith("feat/"):
                    continue
                branches.append({
                    "name": name,
                    "commit": parts[1] if len(parts) > 1 else "",
                    "message": parts[2] if len(parts) > 2 else "",
                    "date": parts[3] if len(parts) > 3 else "",
                })
        return branches

    async def get_log(self, max_count: int = 50) -> list[dict[str, str]]:
        """Get git log with branch decorations for the graph visualization."""
        rc, out, _ = await self._run(
            "git", "log", "--all", f"--max-count={max_count}",
            "--format=%H\t%h\t%s\t%an\t%cr\t%D",
            "--topo-order",
        )
        if rc != 0 or not out:
            return []

        commits = []
        for line in out.splitlines():
            parts = line.split("\t", 5)
            if len(parts) < 3:
                continue
            refs = parts[5].strip() if len(parts) > 5 else ""
            # Parse branch refs from decoration
            branch_refs = []
            if refs:
                for ref in refs.split(","):
                    ref = ref.strip()
                    if ref.startswith("HEAD -> "):
                        ref = ref[8:]
                    if ref and not ref.startswith("tag:"):
                        branch_refs.append(ref)

            commits.append({
                "hash": parts[0],
                "short": parts[1],
                "message": parts[2],
                "author": parts[3] if len(parts) > 3 else "",
                "date": parts[4] if len(parts) > 4 else "",
                "branches": branch_refs,
            })
        return commits

    async def get_log_graph(self, max_count: int = 50) -> list[dict]:
        """Get git log with parent info for DAG rendering."""
        rc, out, _ = await self._run(
            "git", "log", "--all", f"--max-count={max_count}",
            "--format=%H\t%P\t%h\t%s\t%an\t%cr\t%D",
            "--topo-order",
        )
        if rc != 0 or not out:
            return []

        commits = []
        for line in out.splitlines():
            parts = line.split("\t", 6)
            if len(parts) < 4:
                continue
            parents = parts[1].split() if parts[1] else []
            refs = parts[6].strip() if len(parts) > 6 else ""
            branch_refs = []
            is_head = False
            if refs:
                for ref in refs.split(","):
                    ref = ref.strip()
                    if ref == "HEAD":
                        is_head = True
                        continue
                    if ref.startswith("HEAD -> "):
                        is_head = True
                        ref = ref[8:]
                    if ref and not ref.startswith("tag:"):
                        branch_refs.append(ref)

            commits.append({
                "hash": parts[0],
                "parents": parents,
                "short": parts[2],
                "message": parts[3],
                "author": parts[4] if len(parts) > 4 else "",
                "date": parts[5] if len(parts) > 5 else "",
                "branches": branch_refs,
                "is_head": is_head,
            })
        return commits
