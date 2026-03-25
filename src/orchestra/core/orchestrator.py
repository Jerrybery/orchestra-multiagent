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
from .agent_spawner import AgentSpawner, AgentRole, AgentHandle, AgentResult

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
    model: Optional[str] = None


class Orchestrator:
    """Coordinates Head Leaders, Feature Realizers, and Feature Interpreters."""

    def __init__(self, config: OrchestraConfig):
        self.config = config
        self.task_queue = TaskQueue(config.orchestra_dir / "tasks.db")
        self.context = ContextManager(config.orchestra_dir)
        self.worktree = WorktreeManager(config.project_dir, config.orchestra_dir / "worktrees")
        self.spawner = AgentSpawner(
            claude_cmd=config.claude_cmd,
            max_turns=config.max_turns,
            model=config.model,
            on_output=self._on_agent_output,
        )
        self._running_tasks: dict[str, AgentHandle] = {}  # task_id -> handle
        self._on_event: Optional[Callable[[str, dict], Awaitable[None]]] = None
        self._stop = False

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
        await self._emit("hl_start", {"requirement_id": req_id, "requirement": requirement[:200]})

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
        await self.task_queue.add_proposal(proposal_id, req_id, parsed["features"], summary=summary)

        await self._emit("hl_done", {
            "proposal_id": proposal_id,
            "count": len(parsed["features"]),
            "features": [f["id"] for f in parsed["features"]],
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
        for feat in features:
            # Ensure spec file exists — if HL didn't write one, generate from proposal data
            spec_path = self.context.get_spec_path(feat["id"])
            if not spec_path.exists():
                spec_text = feat.get("spec", "")
                if spec_text:
                    # Generate a proper spec file from the inline spec
                    deps_str = ", ".join(feat.get("depends_on", [])) or "None"
                    full_spec = (
                        f"# {feat['id']}: {feat['title']}\n\n"
                        f"## Dependencies\n{deps_str}\n\n"
                        f"## Requirements & Acceptance Criteria\n{spec_text}\n"
                    )
                    self.context.write_spec(feat["id"], full_spec)
                    log.info("Generated spec file for %s from proposal data", feat["id"])
                else:
                    # Minimal fallback
                    self.context.write_spec(feat["id"],
                        f"# {feat['id']}: {feat['title']}\n\nNo detailed spec provided by Head Leader.\n")

            task = await self.task_queue.add_task(
                task_id=feat["id"],
                title=feat["title"],
                priority=feat.get("priority", 0),
                depends_on=feat.get("depends_on", []),
                requirement_id=proposal.requirement_id,
                spec_path=str(spec_path),
            )
            tasks.append(task)

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
        wt_path = await self.worktree.create_worktree(task.id)

        # Transition to IN_PROGRESS
        await self.task_queue.transition(
            task.id, TaskStatus.IN_PROGRESS,
            worktree_path=str(wt_path),
            branch=f"feat/{task.id}",
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

        # Move to REVIEW regardless of output — human decides
        await self.task_queue.transition(task.id, TaskStatus.REVIEW)
        await self._emit("fi_done", {
            "task_id": task.id,
            "recommendation": parsed.get("recommendation", "unknown") if parsed else "unknown",
            "report": str(self.context.get_report_path(task.id)),
        })

    # ── Human Review Actions ────────────────────────────────────────

    async def accept_task(self, task_id: str) -> None:
        """Human accepts a task: merge to main and mark DONE."""
        await self.task_queue.transition(task_id, TaskStatus.ACCEPTED)

        merged = await self.worktree.merge_to_main(task_id)
        if merged:
            await self.worktree.cleanup_worktree(task_id)
            await self.task_queue.transition(task_id, TaskStatus.DONE)
            await self._emit("task_done", {"task_id": task_id})

            # Promote any tasks that were blocked on this one
            promoted = await self.task_queue.promote_ready_tasks()
            if promoted:
                await self._emit("tasks_promoted", {"task_ids": [t.id for t in promoted]})
        else:
            log.error("Merge failed for %s — task stays ACCEPTED, needs manual resolution", task_id)
            await self._emit("merge_failed", {"task_id": task_id})

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
