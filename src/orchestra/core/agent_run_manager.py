"""AgentRunManager — single source of truth for orchestrated agent execution.

Responsibilities:
- Create agent_runs records (one per submit)
- Acquire fi_lock for FI runs (global serialization)
- Inject prev_run + resume_session_id from history
- Drive Runner.run() in a background task
- On finish: write result_snapshot, advance state machine if mode=auto,
  add auto_pause on auto failures, propagate FR sibling cascade
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional, Awaitable

from .runners.base import RunContext, RunResult, CancelToken
from .task_queue import TaskStatus, TaskQueue

log = logging.getLogger(__name__)


class AgentRunManager:
    """Drives all agent runs (auto + manual) through a single execution path."""

    def __init__(self, task_queue: TaskQueue, runners: dict, context: dict,
                 log_path_fn: Callable[[str, str], str],
                 emit: Optional[Callable[[str, dict], Awaitable[None]]] = None,
                 hl_done_hook: Optional[Callable] = None,
                 fr_failed_hook: Optional[Callable] = None):
        self.task_queue = task_queue
        self.runners = runners  # role → AgentRunner instance
        self.context = context  # passthrough fields for RunContext
        self._log_path_fn = log_path_fn
        self._emit = emit
        self._hl_done_hook = hl_done_hook
        self._fr_failed_hook = fr_failed_hook
        self._fi_lock = asyncio.Lock()
        self._running: dict[int, tuple[asyncio.Task, CancelToken, str]] = {}
        self._finished_events: dict[int, asyncio.Event] = {}

    async def submit(self, role: str, target_kind: str, target_id: str,
                     mode: str = "manual",
                     resume: bool = True,
                     user_message: Optional[str] = None,
                     resumed_from_run_id: Optional[int] = None) -> int:
        """Create a run, start it in background, return run_id immediately."""
        log_path = self._log_path_fn(role, target_id)
        run = await self.task_queue.add_agent_run(
            role=role, target_kind=target_kind, target_id=target_id,
            mode=mode, log_path=log_path,
            resumed_from_run_id=resumed_from_run_id,
        )
        # Resolve resume_session_id from previous run if requested
        resume_session_id = None
        prev_run = None
        if run.previous_run_id:
            prev_run = await self.task_queue.get_agent_run(run.previous_run_id)
            if resume and prev_run and prev_run.session_id:
                resume_session_id = prev_run.session_id

        ctx = RunContext(
            role=role, target_kind=target_kind, target_id=target_id,
            mode=mode, resume_session_id=resume_session_id, prev_run=prev_run,
            project_dir=self.context.get("project_dir"),
            orchestra_dir=self.context.get("orchestra_dir"),
            log_path=log_path, user_message=user_message,
        )
        cancel = CancelToken()
        evt = asyncio.Event()
        self._finished_events[run.id] = evt
        task = asyncio.create_task(self._drive(run.id, ctx, cancel, evt))
        self._running[run.id] = (task, cancel, role)
        await self._emit_evt("run_created", {
            "run_id": run.id, "role": role, "target_id": target_id, "mode": mode,
        })
        return run.id

    async def chat(self, origin_run_id: int, message: str) -> int:
        """Continue an existing run with a user message via --resume."""
        origin = await self.task_queue.get_agent_run(origin_run_id)
        if not origin:
            raise ValueError(f"Run {origin_run_id} not found")
        run_id = await self.submit(
            role=origin.role, target_kind=origin.target_kind,
            target_id=origin.target_id, mode="manual",
            resume=True, user_message=message,
            resumed_from_run_id=origin_run_id,
        )
        await self.task_queue.add_run_message(run_id, "user", message)
        evt = self._finished_events.get(run_id)
        if evt:
            asyncio.create_task(self._record_assistant_reply(run_id, evt))
        return run_id

    async def _record_assistant_reply(self, run_id: int,
                                       evt: asyncio.Event) -> None:
        await evt.wait()
        run = await self.task_queue.get_agent_run(run_id)
        if run and run.result_snapshot:
            reply = (run.result_snapshot.get("reply")
                     or run.result_snapshot.get("notes") or "")
            if reply:
                await self.task_queue.add_run_message(run_id, "assistant", reply)

    async def _drive(self, run_id: int, ctx: RunContext,
                     cancel: CancelToken, evt: asyncio.Event) -> None:
        runner = self.runners[ctx.role]
        # FI global lock
        try:
            if ctx.role == "fi":
                async with self._fi_lock:
                    result = await runner.run(ctx, cancel)
            else:
                result = await runner.run(ctx, cancel)
        except Exception as e:
            log.exception("Runner crashed for run %d", run_id)
            result = RunResult(status="failed", session_id=None,
                               result_snapshot={}, error_message=str(e))
        finally:
            self._running.pop(run_id, None)

        await self._finish(run_id, ctx, result)
        evt.set()

    async def _finish(self, run_id: int, ctx: RunContext,
                      result: RunResult) -> None:
        await self.task_queue.finish_agent_run(
            run_id, status=result.status,
            result_snapshot=result.result_snapshot,
            session_id=result.session_id,
            error_message=result.error_message,
        )
        await self._emit_evt("run_finished", {
            "run_id": run_id, "status": result.status,
            "result_snapshot": result.result_snapshot,
        })

        # Apply state machine + auto_pause rules
        if result.status == "succeeded":
            await self._apply_success(ctx, result)
            # any successful manual run clears auto_pause on that target
            if ctx.mode == "manual":
                await self.task_queue.remove_auto_pause(
                    ctx.target_kind, ctx.target_id,
                )
        elif result.status == "failed":
            if ctx.mode == "auto":
                await self.task_queue.add_auto_pause(
                    ctx.target_kind, ctx.target_id,
                    caused_by_run_id=run_id,
                    reason=result.error_message,
                )
                await self._emit_evt("auto_pause_added", {
                    "target_kind": ctx.target_kind, "target_id": ctx.target_id,
                    "reason": result.error_message,
                })
                # FR-specific sibling cascade
                if ctx.role == "fr" and self._fr_failed_hook:
                    await self._fr_failed_hook(ctx.target_id, run_id,
                                                result.error_message)
        # 'cancelled' → no state changes

    async def _apply_success(self, ctx: RunContext, result: RunResult) -> None:
        snap = result.result_snapshot or {}
        if ctx.role == "hl":
            # Product data: persist new features regardless of mode
            # (per design §5: manual HL success "更新 features").
            # Hook impl is free to gate on proposal status (e.g. skip
            # replace when proposal is already approved per §6 (c)).
            if self._hl_done_hook and snap.get("features"):
                await self._hl_done_hook(ctx.target_id, snap)
            # State machine: only auto-mode flips requirement.status
            if ctx.mode == "auto":
                await self.task_queue.update_requirement_status(
                    ctx.target_id, "processed",
                )
            return

        if ctx.mode != "auto":
            return  # FR/FI manual: don't touch state machine

        if ctx.role == "fr":
            await self.task_queue.transition(
                ctx.target_id, TaskStatus.IMPLEMENTED,
            )
        elif ctx.role == "fi":
            await self.task_queue.transition(
                ctx.target_id, TaskStatus.REVIEW,
            )

    async def wait_for_finish(self, run_id: int) -> None:
        evt = self._finished_events.get(run_id)
        if evt:
            await evt.wait()
            # First caller cleans up; subsequent callers find no evt and
            # return immediately (the run is already finished).
            self._finished_events.pop(run_id, None)

    async def cancel(self, run_id: int) -> bool:
        """Signal cancellation for a running run."""
        entry = self._running.get(run_id)
        if not entry:
            return False
        _, cancel, _ = entry
        cancel.set()
        return True

    def running_count(self, role: str) -> int:
        return sum(1 for (t, _, r) in self._running.values()
                   if r == role and not t.done())

    async def _emit_evt(self, event: str, data: dict) -> None:
        if self._emit:
            await self._emit(event, data)


class AutoDriver:
    """Background loop that submits auto-mode runs based on task state."""

    def __init__(self, task_queue: TaskQueue, manager: AgentRunManager,
                 config, poll_interval: float = 2.0):
        self.task_queue = task_queue
        self.manager = manager
        self.config = config
        self._poll_interval = poll_interval
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run_loop(self) -> None:
        self._stop = False
        log.info("AutoDriver loop started")
        while not self._stop:
            try:
                await self._tick()
            except Exception:
                log.exception("AutoDriver tick error")
            await asyncio.sleep(self._poll_interval)
        log.info("AutoDriver loop stopped")

    async def _tick(self) -> None:
        await self._tick_hl()
        await self.task_queue.promote_ready_tasks()
        await self._tick_fr()
        await self._tick_fi()

    async def _tick_hl(self) -> None:
        reqs = await self.task_queue.get_all_requirements()
        running = await self.task_queue.list_agent_runs(role="hl", status="running")
        running_targets = {r.target_id for r in running}
        for req in reqs:
            if getattr(req, "status", "pending") != "pending":
                continue
            if req.id in running_targets:
                continue
            if await self.task_queue.is_auto_paused("requirement", req.id):
                continue
            if self.manager.running_count("hl") >= self.config.max_hl:
                break
            await self.manager.submit(
                role="hl", target_kind="requirement",
                target_id=req.id, mode="auto",
            )

    async def _tick_fr(self) -> None:
        assigned = await self.task_queue.get_tasks(TaskStatus.ASSIGNED)
        running = await self.task_queue.list_agent_runs(role="fr", status="running")
        running_targets = {r.target_id for r in running}
        for task in assigned:
            if task.id in running_targets:
                continue
            if await self.task_queue.is_auto_paused("task", task.id):
                continue
            if self.manager.running_count("fr") >= self.config.max_fr:
                break
            await self.manager.submit(
                role="fr", target_kind="task",
                target_id=task.id, mode="auto",
            )

    async def _tick_fi(self) -> None:
        implemented = await self.task_queue.get_tasks(TaskStatus.IMPLEMENTED)
        running = await self.task_queue.list_agent_runs(role="fi", status="running")
        running_targets = {r.target_id for r in running}
        for task in implemented:
            if task.id in running_targets:
                continue
            if await self.task_queue.is_auto_paused("task", task.id):
                continue
            if self.manager.running_count("fi") >= self.config.max_fi:
                break
            await self.manager.submit(
                role="fi", target_kind="task",
                target_id=task.id, mode="auto",
            )
