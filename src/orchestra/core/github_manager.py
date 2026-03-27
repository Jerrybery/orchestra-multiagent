"""GitHub issue and PR management via gh CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class GitHubManager:
    """Manages GitHub issues and PRs via the gh CLI."""

    def __init__(self, repo_dir: Path):
        self.repo_dir = repo_dir
        self._available: Optional[bool] = None

    async def _run(self, *cmd: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode().strip(), stderr.decode().strip()

    async def is_available(self) -> bool:
        """Check if gh CLI is authenticated and repo is connected."""
        if self._available is not None:
            return self._available
        rc, _, _ = await self._run("gh", "auth", "status")
        if rc != 0:
            self._available = False
            return False
        rc, _, _ = await self._run("gh", "repo", "view", "--json", "nameWithOwner")
        self._available = rc == 0
        return self._available

    # ── Issues ──────────────────────────────────────────────────

    async def create_idea_issue(self, title: str, body: str) -> Optional[dict]:
        """Create a parent issue for an idea. Returns {number, url}."""
        if not await self.is_available():
            return None

        rc, out, err = await self._run(
            "gh", "issue", "create",
            "--title", title,
            "--body", body,
            "--label", "idea",
        )
        if rc != 0:
            # Label might not exist, try without
            rc, out, err = await self._run(
                "gh", "issue", "create",
                "--title", title,
                "--body", body,
            )
        if rc != 0:
            log.error("Failed to create idea issue: %s", err)
            return None

        url = out.strip()
        # Extract issue number from URL
        m = re.search(r'/issues/(\d+)', url)
        number = int(m.group(1)) if m else 0
        log.info("Created idea issue #%d: %s", number, url)
        return {"number": number, "url": url}

    async def create_feat_issue(self, title: str, body: str,
                                parent_number: int) -> Optional[dict]:
        """Create a feature issue linked to the parent idea issue."""
        if not await self.is_available():
            return None

        # Reference parent in body
        linked_body = f"Part of #{parent_number}\n\n{body}"

        rc, out, err = await self._run(
            "gh", "issue", "create",
            "--title", title,
            "--body", linked_body,
            "--label", "feat",
        )
        if rc != 0:
            rc, out, err = await self._run(
                "gh", "issue", "create",
                "--title", title,
                "--body", linked_body,
            )
        if rc != 0:
            log.error("Failed to create feat issue: %s", err)
            return None

        url = out.strip()
        m = re.search(r'/issues/(\d+)', url)
        number = int(m.group(1)) if m else 0
        log.info("Created feat issue #%d (parent #%d): %s", number, parent_number, url)
        return {"number": number, "url": url}

    # ── PRs ─────────────────────────────────────────────────────

    async def create_pr(self, branch: str, base: str, title: str,
                        body: str) -> tuple[bool, str]:
        """Create a PR. Returns (success, pr_url)."""
        if not await self.is_available():
            return False, "gh not available"

        rc, out, err = await self._run(
            "gh", "pr", "create",
            "--base", base,
            "--head", branch,
            "--title", title,
            "--body", body,
        )
        if rc != 0:
            if "already exists" in err.lower():
                rc2, url, _ = await self._run(
                    "gh", "pr", "view", branch, "--json", "url", "--jq", ".url"
                )
                if rc2 == 0 and url:
                    return True, url
            log.error("PR create failed: %s", err)
            return False, err[:200]

        return True, out.strip()

    async def get_main_branch(self) -> str:
        """Get the default branch name."""
        rc, out, _ = await self._run(
            "gh", "repo", "view", "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"
        )
        return out.strip() if rc == 0 and out.strip() else "main"
