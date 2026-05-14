"""Plan Runner — reads spec + codebase, produces implementation plan."""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Awaitable, Optional

from orchestra.core.agent_spawner import AgentRole
from .base import AgentRunner, RunContext, RunResult, CancelToken

log = logging.getLogger(__name__)
_RESULT_PATTERN = re.compile(r"ORCHESTRA_RESULT:({.*})")


def _parse_result(output: str) -> Optional[dict]:
    for line in reversed(output.splitlines()):
        m = _RESULT_PATTERN.search(line)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
    return None


class PLRunner(AgentRunner):
    def __init__(self, spawner,
                 task_loader: Callable[[str], Awaitable],
                 prompt_loader: Callable[[str], str]):
        super().__init__(spawner)
        self._load_task = task_loader
        self._load_prompt = prompt_loader

    async def run(self, ctx: RunContext, cancel: CancelToken) -> RunResult:
        task = await self._load_task(ctx.target_id)
        system_prompt = self._load_prompt(ctx.target_id)

        task_prompt = f"Write an implementation plan for feature {task.id}: {task.title}"
        if ctx.prev_run and ctx.prev_run.result_snapshot:
            fail_reason = ctx.prev_run.result_snapshot.get("fail_reason")
            if fail_reason:
                task_prompt += f"\n\nPrevious attempt failed: {fail_reason}"

        try:
            handle = await self.spawner.spawn(
                role=AgentRole.PLANNER,
                system_prompt=system_prompt,
                task_prompt=task_prompt,
                cwd=ctx.project_dir,
                task_id=ctx.target_id,
                log_path=ctx.log_path,
                add_dirs=[ctx.orchestra_dir] if ctx.orchestra_dir else [],
            )
            result = await self._wait_or_cancel(handle, cancel)
            if result is None:
                return RunResult(status="cancelled", session_id=None,
                                 result_snapshot={}, error_message="cancelled")

            parsed = _parse_result(result.stdout)
            if not parsed or "plan" not in parsed:
                return RunResult(
                    status="failed", session_id=handle.session_id,
                    result_snapshot={"raw_stdout_tail": result.stdout[-500:]},
                    error_message="Planner produced no structured output",
                )
            return RunResult(
                status="succeeded",
                session_id=handle.session_id,
                result_snapshot=parsed,
                error_message=None,
            )
        except Exception as e:
            log.exception("PLRunner crashed")
            return RunResult(status="failed", session_id=None,
                             result_snapshot={}, error_message=str(e))
