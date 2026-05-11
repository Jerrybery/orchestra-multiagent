"""Feature Realizer Runner — implements a single feature in a worktree."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional, Callable, Awaitable

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


class FRRunner(AgentRunner):
    def __init__(self, spawner, worktree_mgr,
                 task_loader: Callable[[str], Awaitable],
                 prompt_loader: Callable[[str], str],
                 head_fn: Callable[..., Awaitable[str]],
                 files_changed_fn: Callable[..., Awaitable[list]]):
        super().__init__(spawner)
        self._wt = worktree_mgr
        self._load_task = task_loader
        self._load_prompt = prompt_loader
        self._git_head = head_fn
        self._git_files_changed = files_changed_fn

    async def run(self, ctx: RunContext, cancel: CancelToken) -> RunResult:
        task = await self._load_task(ctx.target_id)
        wt_path = await self._wt.create_worktree(
            task.id, title=task.title, source_issue=task.source_issue,
        )
        branch = self._wt.get_branch_name(task.id)

        system_prompt = self._load_prompt(task.id)

        if ctx.user_message:
            task_prompt = ctx.user_message
        elif ctx.resume_session_id and task.reject_reason:
            task_prompt = (
                f"This implementation was rejected. Address the feedback:\n\n"
                f"{task.reject_reason}\n\nMake the changes and report back."
            )
            system_prompt = ""  # ignored on --resume
        else:
            task_prompt = (
                f"Implement feature {task.id}: {task.title}\n\n"
                f"The full spec is in the system prompt above. Start implementing."
            )
            if task.reject_reason:
                task_prompt += (
                    f"\n\n## Previous Review Feedback\nPreviously rejected: "
                    f"{task.reject_reason}\nPlease address."
                )

        # Inject prev_run notes when fresh + prev exists (fallback context)
        if ctx.prev_run and not ctx.resume_session_id:
            snap = ctx.prev_run.result_snapshot or {}
            if snap.get("notes") or snap.get("files_changed"):
                task_prompt += (
                    f"\n\n## Your previous attempt\n"
                    f"Notes: {snap.get('notes','')}\n"
                    f"Files touched: {snap.get('files_changed', [])}\n"
                )

        resume_args = ["--resume", ctx.resume_session_id] if ctx.resume_session_id else []
        # Resume mode passes the short feedback prompt; fresh fallback rebuilds full
        fresh_system = self._load_prompt(task.id)
        fresh_task = (
            f"Implement feature {task.id}: {task.title}\n\n"
            f"The full spec is in the system prompt above. Start implementing."
        )

        try:
            handle, fell_back = await self._spawn_with_resume_fallback(
                role=AgentRole.FEATURE_REALIZER, cwd=wt_path,
                log_path=ctx.log_path, task_id=task.id,
                resume_system=system_prompt, resume_task=task_prompt,
                resume_args=resume_args,
                fresh_system=fresh_system, fresh_task=fresh_task,
                add_dirs=[wt_path, ctx.orchestra_dir] if ctx.orchestra_dir else [wt_path],
                cancel=cancel,
            )
            result = await self._wait_or_cancel(handle, cancel)
            if result is None:
                return RunResult(status="cancelled", session_id=None,
                                 result_snapshot={"branch": branch},
                                 error_message="cancelled",
                                 used_resume=bool(resume_args), fell_back=fell_back)
            parsed = _parse_result(result.stdout)
            head = await self._git_head(wt_path)
            files = await self._git_files_changed(wt_path, "main")
            if parsed and parsed.get("status") == "blocked":
                return RunResult(
                    status="failed",
                    session_id=handle.session_id,
                    result_snapshot={"branch": branch, "head_commit": head,
                                     "files_changed": files,
                                     "notes": parsed.get("notes", "")},
                    error_message=parsed.get("reason", "FR blocked"),
                    used_resume=bool(resume_args), fell_back=fell_back,
                )
            if result.exit_code != 0:
                return RunResult(
                    status="failed",
                    session_id=handle.session_id,
                    result_snapshot={"branch": branch, "head_commit": head,
                                     "files_changed": files},
                    error_message=(parsed.get("reason") if parsed
                                   else f"exit {result.exit_code}"),
                    used_resume=bool(resume_args), fell_back=fell_back,
                )
            return RunResult(
                status="succeeded",
                session_id=handle.session_id,
                result_snapshot={
                    "branch": branch, "head_commit": head,
                    "files_changed": files,
                    "notes": (parsed.get("notes") if parsed else "") or "",
                },
                error_message=None,
                used_resume=bool(resume_args), fell_back=fell_back,
            )
        except Exception as e:
            log.exception("FRRunner crashed")
            return RunResult(status="failed", session_id=None,
                             result_snapshot={"branch": branch},
                             error_message=str(e),
                             used_resume=bool(resume_args), fell_back=False)
