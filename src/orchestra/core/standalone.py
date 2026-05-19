"""StandaloneSession — independent Agent invocation with full DB traceability."""

from __future__ import annotations

import hashlib
import logging
import sys
import time
from pathlib import Path
from typing import Optional

from .bootstrap import build_core, CoreServices
from .task_queue import TaskStatus

log = logging.getLogger(__name__)


class StandaloneSession:
    """Invoke HL/FR/FI independently while reusing pipeline infrastructure."""

    def __init__(self, config, quiet: bool = False):
        self.config = config
        self.core: Optional[CoreServices] = None
        self._quiet = quiet
        self._last_proposal_id: Optional[str] = None
        self._last_proposal_features: list = []

    async def init(self) -> None:
        self.core = build_core(
            self.config,
            emit=self._emit,
            on_output=None if self._quiet else self._on_agent_output,
            hl_done_hook=self._on_hl_done,
        )
        self.core.context.init()
        await self.core.worktree.ensure_repo()
        await self.core.worktree.ensure_orchestra_gitignored()
        from .db.engine import init_db
        await init_db(self.core.engine)
        await self.core.task_queue.init()
        self.core.context.attach_db(self.core.task_queue)

    async def close(self) -> None:
        if self.core:
            await self.core.task_queue.close()
            await self.core.engine.dispose()

    # ── Public API ────────────────────────────────────────────────

    async def run_hl(self, input_path: Path) -> dict:
        """Run Head Leader on a requirement file."""
        if str(input_path) == "-":
            content = sys.stdin.read()
        else:
            content = input_path.read_text()

        content = f"[standalone]\n{content}"
        req_id = self._make_id("req")
        await self.core.task_queue.add_requirement(req_id, content)

        run_id = await self.core.manager.submit(
            role="hl", target_kind="requirement",
            target_id=req_id, mode="standalone",
        )
        await self.core.manager.wait_for_finish(run_id)

        agent_run = await self.core.task_queue.get_agent_run(run_id)
        if agent_run.status != "succeeded":
            return {
                "status": "failed",
                "error": agent_run.error_message or "HL failed",
                "run_id": run_id,
            }
        return {
            "status": "succeeded",
            "requirement_id": req_id,
            "proposal_id": self._last_proposal_id,
            "features": self._last_proposal_features,
            "run_id": run_id,
        }

    async def run_fr(self, spec_path: Path,
                     task_id: Optional[str] = None) -> dict:
        """Run Feature Realizer on a spec file."""
        spec_text = spec_path.read_text()
        if not task_id:
            task_id = self._derive_task_id(spec_path.name)
            existing = await self.core.task_queue.get_task(task_id)
            if existing:
                task_id = f"{task_id}-{self._hex_suffix()}"

        title = self._extract_title(spec_text) or task_id
        req_id = self._make_id("req")
        await self.core.task_queue.add_requirement(req_id, f"[standalone] {title}")
        await self.core.task_queue.update_requirement_status(req_id, "processed")

        await self.core.task_queue.add_task(
            task_id, title=title, requirement_id=req_id,
        )
        await self.core.task_queue.update_task_spec(task_id, spec_text)
        self.core.context.write_spec(task_id, spec_text)

        for status in [TaskStatus.PLANNING, TaskStatus.PLANNED, TaskStatus.ASSIGNED]:
            await self.core.task_queue.transition(task_id, status)

        run_id = await self.core.manager.submit(
            role="fr", target_kind="task",
            target_id=task_id, mode="standalone",
        )
        await self.core.manager.wait_for_finish(run_id)

        agent_run = await self.core.task_queue.get_agent_run(run_id)
        snap = agent_run.result_snapshot or {}
        if agent_run.status != "succeeded":
            return {
                "status": "failed",
                "task_id": task_id,
                "error": agent_run.error_message or "FR failed",
                "run_id": run_id,
            }
        return {
            "status": "succeeded",
            "task_id": task_id,
            "branch": snap.get("branch", ""),
            "worktree": str(self.core.context.get_worktree_path(task_id)),
            "files_changed": snap.get("files_changed", []),
            "run_id": run_id,
        }

    async def run_fi(self, branch: Optional[str] = None,
                     pr_number: Optional[int] = None,
                     task_id: Optional[str] = None,
                     dev_cmd: Optional[str] = None,
                     dev_ready: Optional[str] = None,
                     base_url: Optional[str] = None) -> dict:
        """Run Feature Interpreter on a branch or PR."""
        if pr_number and not branch:
            branch = await self._resolve_pr_branch(pr_number)
        if not branch:
            return {"status": "failed", "error": "No branch specified"}

        if not task_id:
            task_id = self._derive_task_id_from_branch(branch)

        existing = await self.core.task_queue.get_task(task_id)
        if not existing:
            req_id = self._make_id("req")
            await self.core.task_queue.add_requirement(
                req_id, f"[standalone] Review {branch}",
            )
            await self.core.task_queue.update_requirement_status(req_id, "processed")
            await self.core.task_queue.add_task(
                task_id, title=f"Review {branch}", requirement_id=req_id,
            )
            for status in [
                TaskStatus.PLANNING, TaskStatus.PLANNED,
                TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS,
                TaskStatus.IMPLEMENTED,
            ]:
                await self.core.task_queue.transition(task_id, status)

        await self.core.worktree.ensure_worktree_from_branch(task_id, branch)

        if dev_cmd:
            from .run_config import RunConfig, save_run_config
            tmp_cfg = RunConfig(
                command=dev_cmd,
                ready_signal=dev_ready,
                base_url=base_url or "http://localhost:3000",
            )
            save_run_config(tmp_cfg, self.core.context.get_run_config_path())

        run_id = await self.core.manager.submit(
            role="fi", target_kind="task",
            target_id=task_id, mode="standalone",
        )
        await self.core.manager.wait_for_finish(run_id)

        agent_run = await self.core.task_queue.get_agent_run(run_id)
        snap = agent_run.result_snapshot or {}
        if agent_run.status != "succeeded":
            return {
                "status": "failed",
                "task_id": task_id,
                "error": agent_run.error_message or "FI failed",
                "run_id": run_id,
            }
        return {
            "status": "succeeded",
            "task_id": task_id,
            "recommendation": snap.get("recommendation", "unknown"),
            "critical": snap.get("critical", []),
            "important": snap.get("important", []),
            "report_path": str(self.core.context.get_report_path(task_id)),
            "run_id": run_id,
        }

    # ── Hooks ─────────────────────────────────────────────────────

    async def _on_hl_done(self, requirement_id: str, snapshot: dict) -> None:
        unique = f"{requirement_id}:{time.time()}"
        proposal_id = "prop-" + hashlib.sha256(unique.encode()).hexdigest()[:8]
        features = snapshot.get("features", [])
        for f in features:
            f.setdefault("_idea_issue", 0)
        await self.core.task_queue.add_proposal(
            proposal_id, requirement_id, features,
            summary=snapshot.get("summary", ""),
        )
        self._last_proposal_id = proposal_id
        self._last_proposal_features = [
            {"id": f["id"], "title": f["title"]} for f in features
        ]

    async def _emit(self, event: str, data: dict) -> None:
        await self.core.task_queue.add_event(event, data)

    async def _on_agent_output(self, agent_id: str, stream: str, line: str) -> None:
        print(line, file=sys.stderr)

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _make_id(prefix: str) -> str:
        unique = f"{prefix}:{time.time()}"
        return f"{prefix}-{hashlib.sha256(unique.encode()).hexdigest()[:8]}"

    @staticmethod
    def _hex_suffix() -> str:
        return hashlib.sha256(str(time.time()).encode()).hexdigest()[:6]

    @staticmethod
    def _derive_task_id(filename: str) -> str:
        stem = Path(filename).stem
        for prefix in ("feature-", "spec-", "feat-"):
            if stem.startswith(prefix):
                stem = stem[len(prefix):]
        stem = stem.strip("-_ ")
        if not stem:
            return f"task-{hashlib.sha256(str(time.time()).encode()).hexdigest()[:6]}"
        return stem

    @staticmethod
    def _derive_task_id_from_branch(branch: str) -> str:
        for prefix in ("feat/", "bugfix/", "feature/", "fix/"):
            if branch.startswith(prefix):
                return branch[len(prefix):]
        return branch

    @staticmethod
    def _extract_title(spec_text: str) -> str:
        for line in spec_text.splitlines():
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()[:80]
        return ""

    async def _resolve_pr_branch(self, pr_number: int) -> str:
        import asyncio
        proc = await asyncio.create_subprocess_exec(
            "gh", "pr", "view", str(pr_number),
            "--json", "headRefName", "-q", ".headRefName",
            cwd=str(self.config.project_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"gh pr view failed: {err.decode()}")
        branch = out.decode().strip()
        await self.core.worktree._run(
            "git", "fetch", "origin", branch,
        )
        return branch
