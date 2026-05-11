"""Head Leader Runner — decomposes a requirement into features."""
from __future__ import annotations

import json
import logging
import re
from typing import Optional, Callable, Awaitable

from orchestra.core.agent_spawner import AgentRole
from .base import AgentRunner, RunContext, RunResult, CancelToken, render_chat_context_block

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


class HLRunner(AgentRunner):
    def __init__(self, spawner,
                 requirement_loader: Callable[[str], Awaitable[str]],
                 prompt_loader: Callable[[], str]):
        super().__init__(spawner)
        self._load_requirement = requirement_loader
        self._load_prompt = prompt_loader

    async def run(self, ctx: RunContext, cancel: CancelToken) -> RunResult:
        requirement_text = await self._load_requirement(ctx.target_id)
        system_prompt = self._load_prompt()
        system_prompt = system_prompt.replace(
            "{chat_context_block}", render_chat_context_block(ctx)
        )
        task_prompt = ctx.user_message or requirement_text

        # Inject previous run's features as context
        chat_block = ""
        if ctx.prev_run and ctx.prev_run.result_snapshot:
            features = ctx.prev_run.result_snapshot.get("features", [])
            if features:
                chat_block = (
                    "\n\n## Previously you produced this decomposition\n"
                    + json.dumps(features, indent=2, ensure_ascii=False)
                    + "\n\n(You may keep, refine, or rewrite it.)"
                )
        task_prompt = task_prompt + chat_block

        resume_args = []
        if ctx.resume_session_id:
            resume_args = ["--resume", ctx.resume_session_id]

        try:
            handle, fell_back = await self._spawn_with_resume_fallback(
                role=AgentRole.HEAD_LEADER, cwd=ctx.project_dir,
                log_path=ctx.log_path, task_id=None,
                resume_system=system_prompt, resume_task=task_prompt,
                resume_args=resume_args,
                fresh_system=system_prompt, fresh_task=task_prompt,
                add_dirs=[ctx.orchestra_dir] if ctx.orchestra_dir else [],
                cancel=cancel,
            )
            result = await self._wait_or_cancel(handle, cancel)
            if result is None:
                return RunResult(status="cancelled", session_id=None,
                                 result_snapshot={}, error_message="cancelled",
                                 used_resume=bool(resume_args), fell_back=fell_back)
            parsed = _parse_result(result.stdout)
            if ctx.user_message and not parsed:
                text = result.stdout.strip()
                return RunResult(
                    status="succeeded",
                    session_id=handle.session_id,
                    result_snapshot={"kind": "chat", "reply": text[-2000:]},
                    error_message=None,
                    used_resume=bool(resume_args), fell_back=fell_back,
                )
            if not parsed or "features" not in parsed:
                return RunResult(
                    status="failed", session_id=handle.session_id,
                    result_snapshot={"raw_stdout_tail": result.stdout[-500:]},
                    error_message="HL produced no structured output",
                    used_resume=bool(resume_args), fell_back=fell_back,
                )
            return RunResult(
                status="succeeded",
                session_id=handle.session_id,
                result_snapshot={
                    "summary": parsed.get("summary", ""),
                    "features": parsed["features"],
                },
                error_message=None,
                used_resume=bool(resume_args), fell_back=fell_back,
            )
        except Exception as e:
            log.exception("HLRunner crashed")
            return RunResult(status="failed", session_id=None,
                             result_snapshot={}, error_message=str(e),
                             used_resume=bool(resume_args), fell_back=False)
