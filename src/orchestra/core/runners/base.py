"""Base class for the three agent role runners.

Every Runner shares:
- A unified resume-with-fallback spawn helper
- A cancellation token contract
- A RunContext input + RunResult output type

A Runner is responsible ONLY for: spawning the agent, waiting, parsing the
ORCHESTRA_RESULT, returning a snapshot. It must NOT touch the task state
machine, NOT write to agent_runs (Manager does it), NOT acquire fi_lock
(Manager does it).
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from orchestra.core.task_queue import AgentRun
    from orchestra.core.agent_spawner import AgentSpawner, AgentHandle

log = logging.getLogger(__name__)


class CancelToken:
    """Thin asyncio.Event wrapper for run cancellation."""
    def __init__(self):
        self._evt = asyncio.Event()
    def set(self) -> None:
        self._evt.set()
    def is_set(self) -> bool:
        return self._evt.is_set()
    async def wait(self) -> None:
        await self._evt.wait()


@dataclass
class RunContext:
    role: str
    target_kind: str
    target_id: str
    mode: str
    resume_session_id: Optional[str]
    prev_run: Optional["AgentRun"]
    project_dir: Optional[Path]
    orchestra_dir: Optional[Path]
    log_path: str
    user_message: Optional[str] = None  # for chat-style runs


@dataclass
class RunResult:
    status: str  # 'succeeded' | 'failed' | 'cancelled'
    session_id: Optional[str]
    result_snapshot: dict
    error_message: Optional[str]
    used_resume: bool = False
    fell_back: bool = False


class AgentRunner(ABC):
    """Abstract base for HL / FR / FI runners."""

    def __init__(self, spawner: "AgentSpawner"):
        self.spawner = spawner

    @abstractmethod
    async def run(self, ctx: RunContext, cancel: CancelToken) -> RunResult: ...

    async def _spawn_with_resume_fallback(
        self, *, role, cwd, log_path, task_id,
        resume_system: str, resume_task: str, resume_args: list[str],
        fresh_system: str, fresh_task: str,
        add_dirs: list[Path],
        cancel: CancelToken,
    ) -> tuple["AgentHandle", bool]:
        """Spawn with --resume; if it dies fast, retry fresh.

        Returns (handle, fell_back). The 2s probe is the same race window
        used by the original _spawn_fr_with_resume_fallback.
        """
        handle = await self.spawner.spawn(
            role=role, system_prompt=resume_system, task_prompt=resume_task,
            cwd=cwd, task_id=task_id, log_path=log_path,
            add_dirs=add_dirs, extra_args=resume_args,
        )
        if resume_args and "--resume" in resume_args:
            await asyncio.sleep(2)
            rc = handle.process.returncode
            if rc is not None and rc != 0:
                log.warning("Resume failed (rc=%d), falling back to fresh", rc)
                handle = await self.spawner.spawn(
                    role=role, system_prompt=fresh_system,
                    task_prompt=fresh_task, cwd=cwd, task_id=task_id,
                    log_path=log_path, add_dirs=add_dirs,
                )
                return handle, True
        return handle, False

    async def _wait_or_cancel(self, handle: "AgentHandle",
                              cancel: CancelToken):
        """Race spawner.wait against cancel; kill process if cancelled."""
        wait_task = asyncio.create_task(self.spawner.wait(handle))
        cancel_task = asyncio.create_task(cancel.wait())
        done, pending = await asyncio.wait(
            [wait_task, cancel_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancel_task in done:
            for p in pending:
                p.cancel()
            try:
                handle.process.kill()
            except Exception:
                pass
            return None  # signals cancellation
        for p in pending:
            p.cancel()
        return wait_task.result()
