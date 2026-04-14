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

    # ── Pull Requests ──────────────────────────────────────────

    async def list_prs(self, state: str = "open", limit: int = 30) -> list[dict]:
        """List pull requests."""
        if not await self.is_available():
            return []
        rc, out, _ = await self._run(
            "gh", "pr", "list",
            "--state", state,
            "--json", "number,title,body,labels,state,updatedAt,createdAt,author,comments,url,headRefName,baseRefName",
            "--limit", str(limit),
        )
        if rc != 0 or not out:
            return []
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return []

    async def get_pr(self, pr_number: int) -> Optional[dict]:
        """Get a PR with details and comments (reviews + issue comments)."""
        if not await self.is_available():
            return None
        rc, out, _ = await self._run(
            "gh", "pr", "view", str(pr_number),
            "--json", "number,title,body,labels,comments,reviews,state,createdAt,updatedAt,headRefName,baseRefName,author",
        )
        if rc != 0 or not out:
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None

    async def get_pr_comments(self, pr_number: int) -> list[dict]:
        """Get all comments on a PR (issue comments + review comments)."""
        pr = await self.get_pr(pr_number)
        if not pr:
            return []
        comments = pr.get("comments", [])
        # Also include review bodies as comments
        for review in pr.get("reviews", []):
            body = review.get("body", "").strip()
            if body:
                comments.append({
                    "author": review.get("author", {}),
                    "body": body,
                    "createdAt": review.get("submittedAt", ""),
                    "id": review.get("id", ""),
                })
        return comments

    async def post_pr_comment(self, pr_number: int, body: str) -> bool:
        """Post a comment on a PR."""
        if not await self.is_available():
            return False
        rc, _, err = await self._run(
            "gh", "pr", "comment", str(pr_number), "--body", body,
        )
        if rc != 0:
            log.error("Failed to comment on PR #%d: %s", pr_number, err)
        return rc == 0

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

    # ── Issue Reading & Interaction ──────────────────────────

    async def list_issues_by_label(self, label: str, state: str = "open") -> list[dict]:
        """List issues with a specific label."""
        if not await self.is_available():
            return []
        rc, out, _ = await self._run(
            "gh", "issue", "list",
            "--label", label, "--state", state,
            "--json", "number,title,body,labels,updatedAt,createdAt",
            "--limit", "30",
        )
        if rc != 0 or not out:
            return []
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return []

    async def get_issue(self, issue_number: int) -> Optional[dict]:
        """Get a single issue with full details and comments."""
        if not await self.is_available():
            return None
        rc, out, _ = await self._run(
            "gh", "issue", "view", str(issue_number),
            "--json", "number,title,body,labels,comments,state,createdAt,updatedAt",
        )
        if rc != 0 or not out:
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None

    async def get_issue_comments(self, issue_number: int) -> list[dict]:
        """Get all comments on an issue."""
        detail = await self.get_issue(issue_number)
        if not detail:
            return []
        return detail.get("comments", [])

    async def get_issue_timeline(self, issue_number: int) -> list[dict]:
        """Get issue timeline events (for discovering cross-references)."""
        if not await self.is_available():
            return []
        rc, out, _ = await self._run(
            "gh", "api",
            f"repos/{{owner}}/{{repo}}/issues/{issue_number}/timeline",
            "--paginate",
        )
        if rc != 0 or not out:
            return []
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return []

    async def find_linked_issues(self, issue_number: int) -> list[int]:
        """Extract all issue numbers referenced from an issue's body, comments, and timeline."""
        detail = await self.get_issue(issue_number)
        if not detail:
            return []

        refs: set[int] = set()

        # From body
        refs.update(self._extract_issue_refs(detail.get("body", "")))

        # From comments
        for comment in detail.get("comments", []):
            refs.update(self._extract_issue_refs(comment.get("body", "")))

        # From timeline cross-references
        timeline = await self.get_issue_timeline(issue_number)
        for event in timeline:
            if event.get("event") == "cross-referenced":
                source = event.get("source", {}).get("issue", {})
                if source.get("number"):
                    refs.add(source["number"])

        refs.discard(issue_number)
        return sorted(refs)

    @staticmethod
    def _extract_issue_refs(text: str) -> set[int]:
        """Extract #N issue references from text."""
        return {int(m) for m in re.findall(r'#(\d+)', text or "")}

    async def post_issue_comment(self, issue_number: int, body: str) -> bool:
        """Post a comment on an issue."""
        if not await self.is_available():
            return False
        rc, _, err = await self._run(
            "gh", "issue", "comment", str(issue_number), "--body", body,
        )
        if rc != 0:
            log.error("Failed to comment on #%d: %s", issue_number, err)
        return rc == 0

    async def add_label(self, issue_number: int, label: str) -> bool:
        """Add a label to an issue."""
        if not await self.is_available():
            return False
        rc, _, _ = await self._run(
            "gh", "issue", "edit", str(issue_number), "--add-label", label,
        )
        return rc == 0

    async def list_issues(self, state: str = "open", labels: str = "",
                         limit: int = 50) -> list[dict]:
        """List issues with optional label filter."""
        if not await self.is_available():
            return []
        cmd = [
            "gh", "issue", "list",
            "--state", state,
            "--json", "number,title,body,labels,state,updatedAt,createdAt,author,comments,url",
            "--limit", str(limit),
        ]
        if labels:
            cmd.extend(["--label", labels])
        rc, out, _ = await self._run(*cmd)
        if rc != 0 or not out:
            return []
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return []

    async def get_main_branch(self) -> str:
        """Get the default branch name."""
        rc, out, _ = await self._run(
            "gh", "repo", "view", "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"
        )
        return out.strip() if rc == 0 and out.strip() else "main"
