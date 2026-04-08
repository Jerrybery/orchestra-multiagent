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
    auto_accept: bool = False  # pass_whatever mode: auto-accept after FI


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
        await self.task_queue.init()
        log.info("Orchestra initialized at %s", self.config.orchestra_dir)

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
        """Stop the issue tracker."""
        if self.tracker:
            self.tracker.stop()
            self.tracker = None

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

        # Build requirement from last analysis
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

    async def submit_requirement(self, requirement: str) -> str:
        """Send a requirement to a Head Leader. Returns proposal_id for human review."""
        import hashlib
        import time as _time
        unique = f"{requirement}:{_time.time()}"
        req_id = "req-" + hashlib.sha256(unique.encode()).hexdigest()[:8]
        await self.task_queue.add_requirement(req_id, requirement)

        # Create GitHub idea issue
        idea_issue = await self.github.create_idea_issue(
            title=requirement[:80],
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
        wt_path = await self.worktree.create_worktree(task.id, title=task.title)
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

            # Push feature branch to remote for visibility / PR
            pushed = await self.worktree.push_branch(task.id)
            await self._emit("fr_done", {"task_id": task.id, "notes": notes, "pushed": pushed})
            log.info("FR completed %s (structured=%s, pushed=%s)", task.id, parsed is not None, pushed)
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

        task_prompt = f"Verify the implementation of feature {task.id}: {task.title}\n\nThe full spec, architecture, and conventions are in the system prompt above. Review the code in the current directory, run tests, and write the verification report."

        wt_path = self.context.get_worktree_path(task.id)
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

    async def accept_task(self, task_id: str) -> None:
        """Human accepts a task: push branch, create PR, mark DONE."""
        await self.task_queue.transition(task_id, TaskStatus.ACCEPTED)

        task = await self.task_queue.get_task(task_id)
        branch = self.worktree.get_branch_name(task_id)

        # Push branch to remote
        await self.worktree.push_branch(task_id)

        # Build PR body from spec + FI report
        spec = self.context.read_spec(task_id) or ""
        report = self.context.read_report(task_id) or ""
        pr_body = f"## Feature: {task.title}\n\n"
        if spec:
            pr_body += f"### Spec\n{spec}\n\n"
        if report:
            pr_body += f"### Verification Report\n{report}\n\n"
        pr_body += "---\n*Generated by [Orchestra](https://github.com/Jerrybery/orchestra-multiagent)*"

        # Get the target base branch
        base = await self.github.get_main_branch()

        # Create PR via GitHub
        pr_ok, pr_url = await self.github.create_pr(
            branch=branch,
            base=base,
            title=f"{task_id}: {task.title}",
            body=pr_body,
        )

        await self.task_queue.transition(task_id, TaskStatus.DONE)

        if pr_ok:
            await self._emit("task_done", {"task_id": task_id, "pr_url": pr_url})
            log.info("PR created for %s: %s", task_id, pr_url)
        else:
            await self._emit("task_done", {"task_id": task_id, "pr_failed": pr_url})
            log.warning("PR creation failed for %s: %s", task_id, pr_url)

        # Promote downstream tasks
        promoted = await self.task_queue.promote_ready_tasks()
        if promoted:
            await self._emit("tasks_promoted", {"task_ids": [t.id for t in promoted]})

        # Check if all tasks for this requirement are DONE → create idea merge branch
        if task.requirement_id:
            await self._maybe_create_idea_branch(task.requirement_id)

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
