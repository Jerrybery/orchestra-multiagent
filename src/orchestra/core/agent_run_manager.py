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
                 emit: Optional[Callable[[str, dict], Awaitable[None]]] = None):
        self.task_queue = task_queue
        self.runners = runners  # role → AgentRunner instance
        self.context = context  # passthrough fields for RunContext
        self._log_path_fn = log_path_fn
        self._emit = emit
        self._fi_lock = asyncio.Lock()
        self._running: dict[int, tuple[asyncio.Task, CancelToken]] = {}
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
        self._running[run.id] = (task, cancel)
        await self._emit_evt("run_created", {
            "run_id": run.id, "role": role, "target_id": target_id, "mode": mode,
        })
        return run.id

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
        # 'cancelled' → no state changes

    async def _apply_success(self, ctx: RunContext, result: RunResult) -> None:
        """Hook for state machine advancement.

        Filled in Task 3.3 (per-role behavior). For now stubbed; tests only
        verify the no-op path doesn't crash.
        """
        pass

    async def wait_for_finish(self, run_id: int) -> None:
        evt = self._finished_events.get(run_id)
        if evt:
            await evt.wait()

    async def cancel(self, run_id: int) -> bool:
        """Signal cancellation for a running run."""
        entry = self._running.get(run_id)
        if not entry:
            return False
        _, cancel = entry
        cancel.set()
        return True

    def running_count(self, role: str) -> int:
        return sum(1 for (t, _) in self._running.values() if not t.done())

    async def _emit_evt(self, event: str, data: dict) -> None:
        if self._emit:
            await self._emit(event, data)
