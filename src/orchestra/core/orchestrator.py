"""Core orchestrator — the main loop that coordinates all agents."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, Awaitable

from .task_queue import TaskQueue, TaskStatus, Task
from .context_manager import ContextManager
from .worktree_manager import WorktreeManager
from .github_manager import GitHubManager
from .agent_spawner import AgentSpawner, AgentRole, AgentHandle, AgentResult
from .issue_tracker import IssueTracker, WatchConfig, DiscussionTree

log = logging.getLogger(__name__)

# Regex to extract the structured result from agent output
RESULT_PATTERN = re.compile(r"ORCHESTRA_RESULT:({.*})")


def _parse_agent_result(output: str) -> Optional[dict]:
    """Extract ORCHESTRA_RESULT JSON from agent stdout."""
    for line in reversed(output.splitlines()):
        m = RESULT_PATTERN.search(line)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                log.warning("Failed to parse ORCHESTRA_RESULT: %s", m.group(1))
    return None


@dataclass
class OrchestraConfig:
    project_dir: Path          # Where the user's git repo lives (or will be created)
    orchestra_dir: Path        # .orchestra/ directory
    max_fr: int = 2
    max_fi: int = 1
    max_hl: int = 1
    claude_cmd: str = "claude"
    max_turns: int = 50
    model: str = "sonnet"
    auto_accept: bool = False
    tracked_branch: Optional[str] = None  # auto-checkout to latest on startup


class Orchestrator:
    """Coordinates Head Leaders, Feature Realizers, and Feature Interpreters."""

    def __init__(self, config: OrchestraConfig):
        self.config = config
        self.task_queue = TaskQueue(config.orchestra_dir / "tasks.db")
        self.context = ContextManager(config.orchestra_dir)
        self.worktree = WorktreeManager(config.project_dir, config.orchestra_dir / "worktrees")
        self.github = GitHubManager(config.project_dir)
        self.spawner = AgentSpawner(
            claude_cmd=config.claude_cmd,
            max_turns=config.max_turns,
            model=config.model,
            on_output=self._on_agent_output,
        )
        self._running_tasks: dict[str, AgentHandle] = {}  # task_id -> handle
        self._on_event: Optional[Callable[[str, dict], Awaitable[None]]] = None
        self._stop = False
        self.tracker: Optional[IssueTracker] = None
        self._tracker_task: Optional[asyncio.Task] = None

    async def init(self) -> None:
        """Initialize all subsystems."""
        self.context.init()
        await self.worktree.ensure_repo()
        await self.worktree.ensure_orchestra_gitignored()
        await self.task_queue.init()

        if self.config.tracked_branch:
            log.info("Fetching remote and checking out tracked branch: %s",
                     self.config.tracked_branch)
            fetched = await self.worktree.fetch_remote()
            if fetched:
                ok, msg = await self.worktree.checkout_branch_latest(
                    self.config.tracked_branch
                )
                log.info("Tracked branch checkout: %s (%s)", ok, msg)
            # After checkout, .orchestra may have been removed — recover
            await self.recover_if_needed()

        log.info("Orchestra initialized at %s", self.config.orchestra_dir)

    async def recover_if_needed(self) -> bool:
        """Check orchestra state after git operations. Re-init if .orchestra is gone.

        Returns True if recovery was needed.
        """
        orchestra_dir = self.config.orchestra_dir
        db_path = orchestra_dir / "tasks.db"

        if orchestra_dir.exists() and db_path.exists():
            # Verify DB is still valid
            try:
                await self.task_queue.all_tasks_summary()
                return False
            except Exception as e:
                log.warning("DB unreadable after git op: %s — will reinit", e)

        log.warning(".orchestra missing or DB invalid — re-initializing")

        # Ensure gitignore first so the directory survives next switch
        await self.worktree.ensure_orchestra_gitignored()

        # Rebuild context dirs and DB (preserves prior data if still there)
        self.context.init()
        try:
            await self.task_queue.close()
        except Exception:
            pass
        await self.task_queue.init()
        await self._emit("orchestra_recovered", {
            "orchestra_dir": str(orchestra_dir),
        })
        return True

    async def close(self) -> None:
        await self.task_queue.close()

    def on_event(self, callback: Callable[[str, dict], Awaitable[None]]) -> None:
        """Register an event callback for TUI or logging."""
        self._on_event = callback

    async def _on_agent_output(self, agent_id: str, stream: str, line: str) -> None:
        """Called for each line of agent output — push to event stream."""
        # Only emit non-empty, non-trivially-verbose lines to avoid flooding
        stripped = line.strip()
        if not stripped:
            return
        await self._emit("agent_log", {
            "agent_id": agent_id,
            "stream": stream,
            "line": stripped[:500],  # truncate very long lines
        })

    async def _emit(self, event: str, data: dict) -> None:
        log.info("Event: %s %s", event, json.dumps(data, default=str))
        await self.task_queue.add_event(event, data)
        if self._on_event:
            await self._on_event(event, data)

    # ── Discussion Tracking ──────────────────────────────────────────

    async def start_tracking(self, watch_config: WatchConfig) -> None:
        """Start watching GitHub issues for discussions."""
        # Stop any existing tracker first
        self.stop_tracking()
        self.tracker = IssueTracker(
            github=self.github,
            spawner=self.spawner,
            task_queue=self.task_queue,
            project_dir=self.config.project_dir,
            orchestra_dir=self.config.orchestra_dir,
            config=watch_config,
            on_event=self._on_event,
            on_ready=self._on_discussion_ready,
        )
        self._tracker_task = asyncio.create_task(self.tracker.run())
        self._tracker_task.add_done_callback(self._on_tracker_done)
        await self._emit("tracking_started", {"labels": watch_config.watch_labels})

    def _on_tracker_done(self, task: asyncio.Task) -> None:
        """Log tracker crashes instead of silently dropping them."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error("IssueTracker crashed: %s", exc, exc_info=exc)

    def stop_tracking(self) -> None:
        """Stop the issue tracker and cancel its background task."""
        if self.tracker:
            self.tracker.stop()
            self.tracker = None
        if self._tracker_task and not self._tracker_task.done():
            self._tracker_task.cancel()
            self._tracker_task = None

    async def _on_discussion_ready(self, tree: DiscussionTree, requirement: str) -> None:
        """Called when a discussion tree matures — submit as requirement."""
        tagged_req = (
            f"[From Discussion Tree #{tree.root_issue}: {tree.title}]\n"
            f"[Tracked issues: {', '.join(f'#{n}' for n in tree.all_issue_numbers)}]\n\n"
            f"{requirement}"
        )

        if self.tracker and self.tracker.config.auto_submit:
            proposal_id = await self.submit_requirement(tagged_req)
            await self.github.post_issue_comment(
                tree.root_issue,
                f"This discussion has been submitted for implementation.\n"
                f"Proposal: `{proposal_id}`\n\n"
                f"Tracked sub-issues: {', '.join(f'#{n}' for n in tree.all_issue_numbers)}",
            )
            tree.status = "submitted"
            await self.task_queue.update_discussion(tree.root_issue, status="submitted")
        else:
            await self._emit("discussion_pending_submit", {
                "root_issue": tree.root_issue,
                "title": tree.title,
                "requirement": tagged_req[:500],
                "issue_count": len(tree.nodes),
            })

    async def submit_discussion(self, root_issue: int) -> str:
        """Manually submit a ready discussion for implementation."""
        if not self.tracker:
            raise ValueError("Tracker not running")
        tree = self.tracker.get_tree(root_issue)
        if not tree:
            raise ValueError(f"Discussion #{root_issue} not found")
        if tree.status != "ready":
            raise ValueError(f"Discussion #{root_issue} is '{tree.status}', not ready")

        tagged_req = (
            f"[From Discussion Tree #{tree.root_issue}: {tree.title}]\n"
            f"[Tracked issues: {', '.join(f'#{n}' for n in tree.all_issue_numbers)}]\n\n"
            f"{tree.last_analysis}"
        )
        proposal_id = await self.submit_requirement(tagged_req)

        await self.github.post_issue_comment(
            tree.root_issue,
            f"Discussion submitted for implementation. Proposal: `{proposal_id}`",
        )
        tree.status = "submitted"
        await self.task_queue.update_discussion(root_issue, status="submitted")
        return proposal_id

    async def submit_issue_as_idea(self, issue_number: int,
                                    extra_instruction: str = "") -> str:
        """Submit a specific GitHub issue as an idea to Head Leader.

        Fetches the issue content + comments directly from GitHub (no tracker
        required), builds a requirement, and links the task to the source issue.
        """
        issue = await self.github.get_issue(issue_number)
        if not issue:
            raise ValueError(f"Issue #{issue_number} not found")

        title = issue.get("title", "")
        body = issue.get("body", "") or ""
        state = issue.get("state", "open")
        comments = issue.get("comments", [])

        parts = [
            f"[From Discussion Tree #{issue_number}: {title}]",
            "",
            f"# {title}",
            f"Source: GitHub issue #{issue_number} ({state})",
            "",
        ]

        if body:
            parts.append("## Description")
            parts.append(body)
            parts.append("")

        human_comments = [
            c for c in comments
            if "Orchestra Discussion Analyst" not in c.get("body", "")
        ]
        if human_comments:
            parts.append(f"## Discussion ({len(human_comments)} comments)")
            for c in human_comments:
                author = c.get("author", {}).get("login", "?")
                ctext = c.get("body", "")[:1500]
                parts.append(f"\n**@{author}:** {ctext}")

        if extra_instruction:
            parts.append(f"\n## Additional Instructions\n{extra_instruction}")

        requirement = "\n".join(parts)

        proposal_id = await self.submit_requirement(requirement)

        await self.github.post_issue_comment(
            issue_number,
            f"This issue has been submitted as an idea for implementation.\n"
            f"Proposal: `{proposal_id}`",
        )

        await self._emit("issue_submitted_as_idea", {
            "issue_number": issue_number,
            "proposal_id": proposal_id,
        })

        return proposal_id

    async def submit_discussion_as_idea(self, root_issue: int,
                                         extra_instruction: str = "") -> str:
        """Submit a discussion tree as an idea, regardless of maturity status.

        Builds a full requirement from all issue bodies, comments, snapshots,
        and agent analysis — gives HL the complete picture to decompose.
        """
        if not self.tracker:
            raise ValueError("Tracker not running")
        tree = self.tracker.get_tree(root_issue)
        if not tree:
            raise ValueError(f"Discussion #{root_issue} not found")

        # Build comprehensive requirement from the full tree
        parts = [
            f"# Idea from Discussion Tree #{tree.root_issue}: {tree.title}",
            f"Tracked issues: {', '.join(f'#{n}' for n in tree.all_issue_numbers)}",
            "",
        ]

        # Include agent's analysis if available
        if tree.last_analysis:
            parts.append("## Discussion Analysis Summary")
            parts.append(tree.last_analysis)
            parts.append("")

        # Include each issue's content and snapshots
        parts.append("## Issue Details")
        for num in sorted(tree.nodes):
            node = tree.nodes[num]
            parts.append(f"\n### Issue #{num}: {node.title}")
            if node.body:
                parts.append(node.body[:3000])
            if node.snapshot:
                parts.append(f"\n**Analyst snapshot:** {node.snapshot}")
            # Include human comments (not bot's)
            human_comments = [
                c for c in node.comments
                if "Orchestra Discussion Analyst" not in c.get("body", "")
            ]
            if human_comments:
                parts.append(f"\n**Discussion ({len(human_comments)} comments):**")
                for c in human_comments:
                    author = c.get("author", {}).get("login", "?")
                    parts.append(f"- @{author}: {c.get('body', '')[:800]}")

        if extra_instruction:
            parts.append(f"\n## Additional Instructions\n{extra_instruction}")

        requirement = "\n".join(parts)

        proposal_id = await self.submit_requirement(requirement)

        await self.github.post_issue_comment(
            tree.root_issue,
            f"Discussion submitted as idea for implementation.\n"
            f"Proposal: `{proposal_id}`\n"
            f"Included issues: {', '.join(f'#{n}' for n in tree.all_issue_numbers)}",
        )

        tree.status = "submitted"
        await self.task_queue.update_discussion(root_issue, status="submitted")

        await self._emit("discussion_submitted_as_idea", {
            "root_issue": root_issue,
            "proposal_id": proposal_id,
            "issue_count": len(tree.nodes),
        })

        return proposal_id

    # ── Prompt Loading ──────────────────────────────────────────────

    def _load_prompt(self, role: AgentRole, task_id: Optional[str] = None) -> str:
        """Load prompt template and inject both paths and file contents."""
        prompt_file = Path(__file__).parent.parent / "prompts" / f"{role.value}.md"
        template = prompt_file.read_text()

        if task_id:
            env = self.context.get_agent_env(task_id, role.value)
        else:
            env = self.context.get_agent_env("__global__", role.value)

        # Replace {key} path placeholders
        for key, value in env.items():
            template = template.replace(f"{{{key}}}", value)

        # Inject file contents directly so agents don't waste tool calls reading them
        def _read_safe(path: str) -> str:
            try:
                p = Path(path)
                return p.read_text() if p.exists() else "(not yet created)"
            except Exception:
                return "(unreadable)"

        # Architecture and conventions — always available
        arch_content = _read_safe(env.get("architecture", ""))
        conv_content = _read_safe(env.get("conventions", ""))
        template = template.replace("{architecture_content}", arch_content)
        template = template.replace("{conventions_content}", conv_content)

        # Spec content — for FR and FI
        if "spec_file" in env:
            spec = self.context.read_spec(task_id) if task_id else None
            template = template.replace("{spec_content}", spec or "(no spec found)")

        # API contracts — read all files in the directory
        contracts = []
        if self.context.contracts_dir.is_dir():
            for f in sorted(self.context.contracts_dir.iterdir()):
                if f.is_file():
                    contracts.append(f"### {f.name}\n{f.read_text()}")
        template = template.replace("{contracts_content}",
                                    "\n\n".join(contracts) if contracts else "(no contracts defined yet)")

        return template

    # ── Head Leader ─────────────────────────────────────────────────

    @staticmethod
    def _extract_title(requirement: str) -> str:
        """Extract a clean human-readable title from a requirement string."""
        import re as _re
        # If requirement starts with [From Discussion Tree #N: TITLE], use TITLE
        m = _re.match(r'\[From Discussion Tree #\d+:\s*(.+?)\]', requirement)
        if m:
            return m.group(1).strip()[:80]
        # If starts with markdown heading, use that
        m = _re.match(r'^\s*#+\s*(.+)$', requirement.splitlines()[0] if requirement else "")
        if m:
            return m.group(1).strip()[:80]
        # Otherwise first non-empty line, truncated
        for line in requirement.splitlines():
            line = line.strip()
            if line and not line.startswith('['):
                return line[:80]
        return requirement[:80]

    @staticmethod
    def _extract_source_issue(requirement: str) -> Optional[int]:
        """Extract source issue number from '[From Discussion Tree #N: ...]' tag."""
        import re as _re
        m = _re.match(r'\[From Discussion Tree #(\d+):', requirement)
        return int(m.group(1)) if m else None

    async def submit_requirement(self, requirement: str) -> str:
        """Send a requirement to a Head Leader. Returns proposal_id for human review."""
        import hashlib
        import time as _time
        unique = f"{requirement}:{_time.time()}"
        req_id = "req-" + hashlib.sha256(unique.encode()).hexdigest()[:8]
        await self.task_queue.add_requirement(req_id, requirement)

        # Use source issue if present, otherwise create a new idea issue
        source_issue = self._extract_source_issue(requirement)
        title = self._extract_title(requirement)

        idea_issue_num = 0
        if source_issue:
            # Requirement came from an existing issue — link to it, don't create a new one
            idea_issue_num = source_issue
            log.info("Requirement linked to existing issue #%d", source_issue)
        else:
            idea_issue = await self.github.create_idea_issue(
                title=title,
                body=f"## Idea\n\n{requirement}\n\n---\n*Created by Orchestra*",
            )
            idea_issue_num = idea_issue["number"] if idea_issue else 0
        await self._emit("hl_start", {
            "requirement_id": req_id,
            "requirement": requirement[:200],
            "issue": idea_issue_num,
        })

        system_prompt = self._load_prompt(AgentRole.HEAD_LEADER)
        handle = await self.spawner.spawn(
            role=AgentRole.HEAD_LEADER,
            system_prompt=system_prompt,
            task_prompt=requirement,
            cwd=self.config.project_dir,
            log_path=self.context.get_log_path("hl-latest"),
            add_dirs=[self.context.orchestra_dir],
        )

        result = await self.spawner.wait(handle)
        parsed = _parse_agent_result(result.stdout)

        if not parsed or "features" not in parsed:
            await self._emit("hl_failed", {"error": "No structured output", "stderr": result.stderr[:500]})
            log.error("HL failed to produce structured output.\nstdout: %s\nstderr: %s",
                      result.stdout[-500:], result.stderr[-500:])
            return ""

        # Store as a proposal awaiting human review — NOT directly as tasks
        proposal_id = f"prop-{req_id.split('-')[1]}"
        summary = parsed.get("summary", requirement[:40])

        # Attach idea issue number to each feature for later linking
        for feat in parsed["features"]:
            feat["_idea_issue"] = idea_issue_num

        await self.task_queue.add_proposal(proposal_id, req_id, parsed["features"], summary=summary)

        await self._emit("hl_done", {
            "proposal_id": proposal_id,
            "count": len(parsed["features"]),
            "features": [f["id"] for f in parsed["features"]],
            "issue": idea_issue_num,
        })
        return proposal_id

    async def approve_proposal(self, proposal_id: str,
                               approved_feature_ids: list[str] | None = None) -> list[Task]:
        """Human approves a proposal (or a subset of its features) → creates IDEA tasks."""
        proposal = await self.task_queue.get_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")

        features = proposal.features
        if approved_feature_ids is not None:
            features = [f for f in features if f["id"] in approved_feature_ids]

        tasks = []
        idea_issue_num = features[0].get("_idea_issue", 0) if features else 0

        for feat in features:
            # Ensure spec file exists
            spec_path = self.context.get_spec_path(feat["id"])
            if not spec_path.exists():
                spec_text = feat.get("spec", "")
                deps_str = ", ".join(feat.get("depends_on", [])) or "None"
                full_spec = (
                    f"# {feat['id']}: {feat['title']}\n\n"
                    f"## Dependencies\n{deps_str}\n\n"
                    f"## Requirements & Acceptance Criteria\n{spec_text or 'No detailed spec.'}\n"
                )
                self.context.write_spec(feat["id"], full_spec)

            # Create GitHub issue for this feature, linked to the idea issue
            feat_issue_num = 0
            if idea_issue_num:
                feat_issue = await self.github.create_feat_issue(
                    title=f"{feat['id']}: {feat['title']}",
                    body=self.context.read_spec(feat["id"]) or "",
                    parent_number=idea_issue_num,
                )
                if feat_issue:
                    feat_issue_num = feat_issue["number"]

            task = await self.task_queue.add_task(
                task_id=feat["id"],
                title=feat["title"],
                priority=feat.get("priority", 0),
                depends_on=feat.get("depends_on", []),
                requirement_id=proposal.requirement_id,
                spec_path=str(spec_path),
                source_issue=feat_issue_num or idea_issue_num or None,
            )
            tasks.append(task)

            # Store issue number for branch linking
            if feat_issue_num:
                feat["_issue_number"] = feat_issue_num

        await self.task_queue.update_proposal_status(proposal_id, "approved")
        await self._emit("proposal_approved", {
            "proposal_id": proposal_id,
            "count": len(tasks),
            "features": [t.id for t in tasks],
        })
        return tasks

    async def reject_proposal(self, proposal_id: str) -> None:
        """Human rejects an entire proposal."""
        await self.task_queue.update_proposal_status(proposal_id, "rejected")
        await self._emit("proposal_rejected", {"proposal_id": proposal_id})

    # ── Feature Realizer ────────────────────────────────────────────

    async def _run_fr(self, task: Task) -> None:
        """Run a Feature Realizer for a single task."""
        # Create worktree
        wt_path = await self.worktree.create_worktree(
            task.id, title=task.title, source_issue=task.source_issue,
        )
        branch = self.worktree.get_branch_name(task.id)

        # Transition to IN_PROGRESS
        await self.task_queue.transition(
            task.id, TaskStatus.IN_PROGRESS,
            worktree_path=str(wt_path),
            branch=branch,
        )
        await self._emit("fr_start", {"task_id": task.id, "title": task.title})

        system_prompt = self._load_prompt(AgentRole.FEATURE_REALIZER, task.id)

        task_prompt = f"Implement feature {task.id}: {task.title}\n\nThe full spec, architecture, conventions, and API contracts are in the system prompt above. Start implementing."

        if task.reject_reason:
            task_prompt += f"\n\n## Previous Review Feedback\nThis feature was previously rejected. Reason:\n{task.reject_reason}\n\nPlease address this feedback."

        handle = await self.spawner.spawn(
            role=AgentRole.FEATURE_REALIZER,
            system_prompt=system_prompt,
            task_prompt=task_prompt,
            cwd=wt_path,
            task_id=task.id,
            log_path=self.context.get_log_path(f"fr-{task.id}"),
            add_dirs=[wt_path, self.context.orchestra_dir],
        )
        self._running_tasks[task.id] = handle

        result = await self.spawner.wait(handle)
        del self._running_tasks[task.id]

        parsed = _parse_agent_result(result.stdout)

        if parsed and parsed.get("status") == "blocked":
            reason = parsed.get("reason", "Unknown blocker")
            log.error("FR blocked for %s: %s", task.id, reason)
            await self._emit("fr_failed", {"task_id": task.id, "reason": reason})
        elif result.exit_code == 0:
            # Success — even if no structured output, exit 0 means it completed
            await self.task_queue.transition(task.id, TaskStatus.IMPLEMENTED)
            notes = parsed.get("notes", "") if parsed else "no structured output"

            # Do NOT auto-push. User decides at review time whether to push
            # and whether to create a PR.
            await self._emit("fr_done", {"task_id": task.id, "notes": notes})
            log.info("FR completed %s (structured=%s)", task.id, parsed is not None)
        else:
            reason = f"Exit code {result.exit_code}"
            if parsed:
                reason = parsed.get("reason", reason)
            log.error("FR failed for %s: %s", task.id, reason)
            await self._emit("fr_failed", {"task_id": task.id, "reason": reason})

    # ── Feature Interpreter ─────────────────────────────────────────

    async def _run_fi(self, task: Task) -> None:
        """Run a Feature Interpreter for a single task."""
        await self.task_queue.transition(task.id, TaskStatus.TESTING)
        await self._emit("fi_start", {"task_id": task.id})

        system_prompt = self._load_prompt(AgentRole.FEATURE_INTERPRETER, task.id)

        # Inject CLAUDE.md into the worktree so FI has review guidelines
        wt_path = self.context.get_worktree_path(task.id)
        fi_claude_md = Path(__file__).parent.parent / "fi_workspace_claude.md"
        if fi_claude_md.exists():
            target = wt_path / "CLAUDE.md"
            # Only write if not already present (don't overwrite project's own CLAUDE.md)
            if not target.exists():
                target.write_text(fi_claude_md.read_text())

        task_prompt = (
            f"Verify the implementation of feature {task.id}: {task.title}\n\n"
            f"The full spec, architecture, and conventions are in the system prompt above.\n\n"
            f"IMPORTANT: Start by running `git diff --stat main..HEAD` to see what changed, "
            f"then `git diff main..HEAD` to read the actual code changes. "
            f"Run all automated checks (tsc, lint, tests, merge markers) BEFORE writing the report."
        )

        handle = await self.spawner.spawn(
            role=AgentRole.FEATURE_INTERPRETER,
            system_prompt=system_prompt,
            task_prompt=task_prompt,
            cwd=wt_path,
            task_id=task.id,
            log_path=self.context.get_log_path(f"fi-{task.id}"),
            add_dirs=[wt_path, self.context.orchestra_dir],
        )
        self._running_tasks[task.id] = handle

        result = await self.spawner.wait(handle)
        del self._running_tasks[task.id]

        parsed = _parse_agent_result(result.stdout)

        # Move to REVIEW
        await self.task_queue.transition(task.id, TaskStatus.REVIEW)
        recommendation = parsed.get("recommendation", "unknown") if parsed else "unknown"
        await self._emit("fi_done", {
            "task_id": task.id,
            "recommendation": recommendation,
            "report": str(self.context.get_report_path(task.id)),
        })

        # Auto-accept if pass_whatever mode is on
        if self.config.auto_accept:
            log.info("Auto-accepting %s (pass_whatever mode)", task.id)
            await self.accept_task(task.id)

    # ── Human Review Actions ────────────────────────────────────────

    async def accept_task(self, task_id: str, push: bool = True,
                          create_pr: bool = True,
                          merge_local: bool = True) -> dict:
        """Human accepts a task. Returns a result dict.

        merge_local → merge feature branch into main locally (default on)
        push        → push branch (and merged main) to remote
        create_pr   → create GitHub PR (requires push)
        """
        await self.task_queue.transition(task_id, TaskStatus.ACCEPTED)

        task = await self.task_queue.get_task(task_id)
        branch = self.worktree.get_branch_name(task_id)

        # 1. Local merge
        merged = False
        merge_msg = ""
        if merge_local:
            merged, merge_msg = await self.worktree.merge_to_main(task_id)
            if merged:
                log.info("Merged %s locally: %s", branch, merge_msg)
            else:
                log.warning("Local merge failed for %s: %s", branch, merge_msg)

        # 2. Push
        pushed_branch = False
        pushed_main = False
        if push:
            pushed_branch = await self.worktree.push_branch(task_id)
            if merged:
                pushed_main = await self.worktree.push_main()

        # 3. Create PR (only if NOT merged locally — if merged, code is on main already)
        pr_ok = False
        pr_url = ""
        if push and create_pr and not merged:
            spec = self.context.read_spec(task_id) or ""
            report = self.context.read_report(task_id) or ""
            pr_body = f"## Feature: {task.title}\n\n"
            if task.source_issue:
                pr_body += f"Implements #{task.source_issue}\n\n"
            if spec:
                pr_body += f"### Spec\n{spec}\n\n"
            if report:
                pr_body += f"### Verification Report\n{report}\n\n"
            pr_body += "---\n*Generated by [Orchestra](https://github.com/Jerrybery/orchestra-multiagent)*"

            base = await self.github.get_main_branch()
            pr_ok, pr_url = await self.github.create_pr(
                branch=branch, base=base,
                title=f"{task_id}: {task.title}",
                body=pr_body,
            )

        await self.task_queue.transition(task_id, TaskStatus.DONE)

        result = {
            "task_id": task_id,
            "merged": merged,
            "merge_message": merge_msg,
            "pushed_branch": pushed_branch,
            "pushed_main": pushed_main,
            "pr_created": pr_ok,
            "pr_url": pr_url if pr_ok else None,
        }
        await self._emit("task_done", result)
        log.info("Task %s done: %s", task_id, result)

        promoted = await self.task_queue.promote_ready_tasks()
        if promoted:
            await self._emit("tasks_promoted", {"task_ids": [t.id for t in promoted]})

    async def rename_branch(self, task_id: str, new_name: str) -> tuple[bool, str]:
        """Rename a task's branch. Returns (success, message)."""
        task = await self.task_queue.get_task(task_id)
        if not task:
            return False, f"Task {task_id} not found"

        old_name = self.worktree.get_branch_name(task_id)
        if old_name == new_name:
            return True, "unchanged"

        wt_path = self.worktree.worktrees_dir / task_id

        # Rename the branch. If worktree exists, rename within it.
        if wt_path.exists():
            rc, _, err = await self.worktree._run(
                "git", "branch", "-m", old_name, new_name, cwd=wt_path,
            )
        else:
            rc, _, err = await self.worktree._run(
                "git", "branch", "-m", old_name, new_name,
            )
        if rc != 0:
            return False, f"rename failed: {err[:200]}"

        self.worktree._branch_cache[task_id] = new_name
        await self.task_queue.update_task_fields(task_id, branch=new_name)
        await self._emit("branch_renamed", {
            "task_id": task_id, "old": old_name, "new": new_name,
        })
        return True, f"{old_name} → {new_name}"

    async def _maybe_create_idea_branch(self, requirement_id: str) -> None:
        """If all tasks for a requirement are DONE, create a combined idea branch."""
        all_tasks = await self.task_queue.get_tasks()
        req_tasks = [t for t in all_tasks if t.requirement_id == requirement_id]

        if not req_tasks or not all(t.status == TaskStatus.DONE for t in req_tasks):
            return  # not all done yet

        # Get the requirement to build the branch name
        req = await self.task_queue.get_requirement(requirement_id)
        if not req:
            return

        # Build idea branch name from requirement content
        import re as _re
        slug = _re.sub(r'[^a-z0-9]+', '-', req.content[:60].lower()).strip('-')
        idea_branch = f"feat/realize-{slug}"

        # Create a merge branch that combines all feature branches
        log.info("All tasks for %s done — creating idea branch %s", requirement_id, idea_branch)

        # Create the idea branch from current HEAD
        rc, _, err = await self.worktree._run("git", "branch", idea_branch)
        if rc != 0 and "already exists" not in err:
            log.warning("Failed to create idea branch: %s", err)
            return

        # Merge each feature branch into the idea branch
        for task in req_tasks:
            feat_branch = self.worktree.get_branch_name(task.id)
            rc, _, err = await self.worktree._run(
                "git", "checkout", idea_branch)
            if rc != 0:
                continue
            rc, _, err = await self.worktree._run(
                "git", "merge", feat_branch, "--no-ff",
                "-m", f"Merge {feat_branch} into {idea_branch}")
            if rc != 0:
                log.warning("Merge %s into idea branch failed: %s", feat_branch, err)
                await self.worktree._run("git", "merge", "--abort")

        # Push the idea branch
        await self.worktree._run("git", "push", "-u", "origin", idea_branch)

        # Switch back to main
        rc, main_branch, _ = await self.worktree._run(
            "git", "symbolic-ref", "--short", "HEAD")
        if idea_branch == main_branch:
            # We're on the idea branch, switch to the default
            default = await self.github.get_main_branch()
            await self.worktree._run("git", "checkout", default)

        await self._emit("idea_branch_created", {
            "requirement_id": requirement_id,
            "branch": idea_branch,
        })

    async def reject_task(self, task_id: str, reason: str) -> None:
        """Human rejects a task: send back to ASSIGNED with feedback."""
        await self.task_queue.transition(task_id, TaskStatus.REJECTED, reject_reason=reason)
        await self.task_queue.transition(task_id, TaskStatus.ASSIGNED)
        await self._emit("task_rejected", {"task_id": task_id, "reason": reason})

    # ── Main Loop ───────────────────────────────────────────────────

    async def run_loop(self) -> None:
        """Main orchestration loop. Runs until stopped."""
        self._stop = False
        log.info("Orchestrator loop started")

        while not self._stop:
            try:
                await self._tick()
            except Exception:
                log.exception("Error in orchestrator tick")

            await asyncio.sleep(2)

        log.info("Orchestrator loop stopped")

    async def _tick(self) -> None:
        """Single iteration of the orchestration loop."""
        # 1. Promote IDEA → ASSIGNED where dependencies are satisfied
        promoted = await self.task_queue.promote_ready_tasks()
        for t in promoted:
            await self._emit("task_promoted", {"task_id": t.id, "title": t.title})

        # 2. Assign ASSIGNED tasks to available FRs
        assigned = await self.task_queue.get_tasks(TaskStatus.ASSIGNED)
        fr_running = self.spawner.running_count(AgentRole.FEATURE_REALIZER)
        for task in assigned:
            if fr_running >= self.config.max_fr:
                break
            if task.id in self._running_tasks:
                continue
            log.info("Dispatching FR for %s (%s)", task.id, task.title)
            asyncio.create_task(self._run_fr(task))
            fr_running += 1

        # 3. Assign IMPLEMENTED tasks to available FIs
        implemented = await self.task_queue.get_tasks(TaskStatus.IMPLEMENTED)
        fi_running = self.spawner.running_count(AgentRole.FEATURE_INTERPRETER)
        for task in implemented:
            if fi_running >= self.config.max_fi:
                break
            if task.id in self._running_tasks:
                continue
            log.info("Dispatching FI for %s (%s)", task.id, task.title)
            asyncio.create_task(self._run_fi(task))
            fi_running += 1

    def stop(self) -> None:
        self._stop = True
