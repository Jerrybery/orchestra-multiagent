"""Feature Interpreter Runner — reviews implementation in a sandboxed dev server."""
from __future__ import annotations

import json
import logging
import re
from typing import Optional, Callable, Awaitable

from orchestra.core.agent_spawner import AgentRole
from .base import AgentRunner, RunContext, RunResult, CancelToken, render_chat_context_block

log = logging.getLogger(__name__)
_RESULT_PATTERN = re.compile(r"ORCHESTRA_RESULT:({.*})")

_DEV_ARTIFACT_PATTERNS = (
    ".next/", "node_modules/", "dist/", "build/", ".vite/", ".cache/",
    ".turbo/", ".parcel-cache/", "next-env.d.ts", ".DS_Store",
    "__pycache__/", ".pytest_cache/", ".pyc",
)


def _filter_dev_artifacts(status_output: str) -> str:
    kept = []
    for line in status_output.splitlines():
        path = line[3:] if len(line) > 3 else line
        if any(p in path for p in _DEV_ARTIFACT_PATTERNS):
            continue
        kept.append(line)
    return "\n".join(kept)


def _parse_result(output: str) -> Optional[dict]:
    for line in reversed(output.splitlines()):
        m = _RESULT_PATTERN.search(line)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
    return None


class FIRunner(AgentRunner):
    def __init__(self, spawner, task_loader, prompt_loader,
                 run_config_loader, dev_server_factory,
                 worktree_path_fn, dev_log_path_fn,
                 head_fn, status_fn, reset_fn, report_parser):
        super().__init__(spawner)
        self._load_task = task_loader
        self._load_prompt = prompt_loader
        self._load_run_config = run_config_loader
        self._dev_server = dev_server_factory
        self._wt_path = worktree_path_fn
        self._dev_log_path = dev_log_path_fn
        self._git_head = head_fn
        self._git_status = status_fn
        self._git_reset = reset_fn
        self._parse_report = report_parser

    async def run(self, ctx: RunContext, cancel: CancelToken) -> RunResult:
        try:
            return await self._run_inner(ctx, cancel)
        except Exception as e:
            log.exception("FIRunner crashed")
            return RunResult(status="failed", session_id=None,
                             result_snapshot={}, error_message=str(e))

    async def _run_inner(self, ctx: RunContext, cancel: CancelToken) -> RunResult:
        task = await self._load_task(ctx.target_id)
        wt_path = self._wt_path(task.id)

        run_cfg = await self._load_run_config()

        dev_log = self._dev_log_path(task.id)
        server = None
        if run_cfg:
            server = self._dev_server(
                cwd=wt_path, command=run_cfg.command,
                ready_signal=run_cfg.ready_signal, base_url=run_cfg.base_url,
                timeout=run_cfg.startup_timeout, log_path=dev_log,
            )
            try:
                await server.start()
            except Exception as e:
                return RunResult(status="failed", session_id=None,
                                 result_snapshot={},
                                 error_message=f"dev server failed to start: {e}")

        base_url = run_cfg.base_url if run_cfg else "(no dev server)"
        dev_log_str = str(dev_log) if run_cfg else "(no dev server)"

        baseline_head = await self._git_head(wt_path)
        baseline_status = await self._git_status(wt_path)
        current_head = baseline_head
        current_status = baseline_status

        try:
            system_prompt = self._load_prompt(task.id)
            system_prompt = system_prompt.replace("{base_url}", base_url)
            system_prompt = system_prompt.replace("{dev_server_log_path}", dev_log_str)
            system_prompt = system_prompt.replace(
                "{chat_context_block}", render_chat_context_block(ctx)
            )

            findings_block = ""
            if ctx.prev_run and ctx.prev_run.result_snapshot:
                snap = ctx.prev_run.result_snapshot
                crit = snap.get("critical") or []
                imp = snap.get("important") or []
                if crit or imp:
                    findings_block = "\n## Previous Review Findings\n"
                    if crit:
                        findings_block += "Critical:\n" + "".join(
                            f"- {c.get('file','?')}:{c.get('line','?')} — {c.get('desc','')}\n"
                            for c in crit)
                    if imp:
                        findings_block += "Important:\n" + "".join(
                            f"- {i.get('file','?')}:{i.get('line','?')} — {i.get('desc','')}\n"
                            for i in imp)

            task_prompt = ctx.user_message or (
                f"Verify the implementation of feature {task.id}: {task.title}\n\n"
                + (f"The dev server is running at {base_url}.\n"
                   f"Dev server log: {dev_log_str}\n\n"
                   if run_cfg else
                   "No dev server is available. Focus on static code review and running tests.\n\n")
                + f"Run `git diff --stat main..HEAD` and `git diff main..HEAD` to see the changes.\n"
                f"Then follow Step 3a/3b/3c in the system prompt above.\n"
                f"{findings_block}"
            )

            resume_args = ["--resume", ctx.resume_session_id] if ctx.resume_session_id else []
            handle, fell_back = await self._spawn_with_resume_fallback(
                role=AgentRole.FEATURE_INTERPRETER, cwd=wt_path,
                log_path=ctx.log_path, task_id=task.id,
                resume_system=system_prompt, resume_task=task_prompt,
                resume_args=resume_args,
                fresh_system=system_prompt, fresh_task=task_prompt,
                add_dirs=[wt_path, ctx.orchestra_dir] if ctx.orchestra_dir else [wt_path],
                cancel=cancel,
            )
            result = await self._wait_or_cancel(handle, cancel)
            if result is None:
                return RunResult(status="cancelled", session_id=None,
                                 result_snapshot={}, error_message="cancelled",
                                 used_resume=bool(resume_args), fell_back=fell_back)

            current_head = await self._git_head(wt_path)
            current_status = await self._git_status(wt_path)
        finally:
            if server:
                await server.stop()

        violated = (current_head != baseline_head) or (
            _filter_dev_artifacts(current_status)
            != _filter_dev_artifacts(baseline_status)
        )
        if violated:
            await self._git_reset(wt_path, baseline_head)
            return RunResult(
                status="succeeded",
                session_id=handle.session_id,
                result_snapshot={
                    "recommendation": "reject",
                    "reason": "FI modified the worktree, which is forbidden.",
                    "critical": [], "important": [],
                },
                error_message=None,
                used_resume=bool(resume_args), fell_back=fell_back,
            )

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
        recommendation = parsed.get("recommendation", "unknown") if parsed else "unknown"
        critical, important = self._parse_report(task.id)
        return RunResult(
            status="succeeded",
            session_id=handle.session_id,
            result_snapshot={
                "recommendation": recommendation,
                "critical": critical, "important": important,
            },
            error_message=None,
            used_resume=bool(resume_args), fell_back=fell_back,
        )
