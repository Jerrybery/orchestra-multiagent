"""Shared bootstrap for Orchestrator and StandaloneSession."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, Awaitable

from .task_queue import TaskQueue
from .context_manager import ContextManager
from .worktree_manager import WorktreeManager
from .github_manager import GitHubManager
from .agent_spawner import AgentSpawner, AgentRole
from .runners.base import AgentRunner

log = logging.getLogger(__name__)


def _prompts_dir() -> Path:
    candidate = Path(__file__).resolve().parents[3] / "prompts"
    if not candidate.is_dir():
        raise FileNotFoundError(
            f"prompts/ not found at expected location: {candidate}"
        )
    return candidate


def _load_prompt(context: ContextManager, role_value: str,
                 task_id: Optional[str] = None) -> str:
    """Load prompt template and inject paths and file contents."""
    prompt_file = _prompts_dir() / f"{role_value}.md"
    template = prompt_file.read_text()

    if task_id:
        env = context.get_agent_env(task_id, role_value)
    else:
        env = context.get_agent_env("__global__", role_value)

    for key, value in env.items():
        template = template.replace(f"{{{key}}}", value)

    def _read_safe(path: str) -> str:
        try:
            p = Path(path)
            return p.read_text() if p.exists() else "(not yet created)"
        except Exception:
            return "(unreadable)"

    arch_content = _read_safe(env.get("architecture", ""))
    conv_content = _read_safe(env.get("conventions", ""))
    template = template.replace("{architecture_content}", arch_content)
    template = template.replace("{conventions_content}", conv_content)

    if "spec_file" in env:
        spec = context.read_spec(task_id) if task_id else None
        template = template.replace("{spec_content}", spec or "(no spec found)")

    contracts = []
    if context.contracts_dir.is_dir():
        for f in sorted(context.contracts_dir.iterdir()):
            if f.is_file():
                contracts.append(f"### {f.name}\n{f.read_text()}")
    template = template.replace(
        "{contracts_content}",
        "\n\n".join(contracts) if contracts else "(no contracts defined yet)",
    )

    return template


@dataclass
class CoreServices:
    """Pipeline and Standalone shared infrastructure."""
    engine: object  # AsyncEngine
    session_factory: object  # async_sessionmaker
    task_queue: TaskQueue
    context: ContextManager
    worktree: WorktreeManager
    spawner: AgentSpawner
    github: GitHubManager
    runners: dict[str, AgentRunner]
    manager: object  # AgentRunManager
    prompt_loader: Callable  # (role_value, task_id?) -> str


def build_core(
    config,
    emit: Optional[Callable[[str, dict], Awaitable[None]]] = None,
    on_output: Optional[Callable] = None,
    hl_done_hook: Optional[Callable] = None,
    fr_failed_hook: Optional[Callable] = None,
) -> CoreServices:
    """Build all core services without starting background loops."""
    from .db.engine import create_db_engine
    from .runners.hl import HLRunner
    from .runners.fr import FRRunner
    from .runners.fi import FIRunner
    from .runners.pl import PLRunner
    from .agent_run_manager import AgentRunManager
    from .dev_server import DevServer

    engine, session_factory = create_db_engine(
        database_config=getattr(config, "database_config", None),
        orchestra_dir=config.orchestra_dir,
    )
    task_queue = TaskQueue(session_factory)
    context = ContextManager(config.orchestra_dir)
    worktree = WorktreeManager(
        config.project_dir, config.orchestra_dir / "worktrees",
    )
    github = GitHubManager(config.project_dir)
    spawner = AgentSpawner(
        claude_cmd=config.claude_cmd,
        max_turns=config.max_turns,
        model=config.model,
        on_output=on_output,
        claude_config_mgr=getattr(config, "claude_config_mgr", None),
        vault=getattr(config, "vault", None),
    )

    prompt_loader = lambda role_value, task_id=None: _load_prompt(
        context, role_value, task_id,
    )

    async def _load_requirement_text(req_id: str) -> str:
        req = await task_queue.get_requirement(req_id)
        return req.content if req else ""

    async def _git_files_changed(cwd, base_branch) -> list:
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--name-only", f"{base_branch}..HEAD",
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return [l for l in out.decode().splitlines() if l]

    from .orchestrator import _git_rev_parse_head, _git_status_porcelain, _git_reset_to

    hl_runner = HLRunner(
        spawner,
        requirement_loader=_load_requirement_text,
        prompt_loader=lambda: prompt_loader(AgentRole.HEAD_LEADER.value),
    )
    fr_runner = FRRunner(
        spawner, worktree,
        task_loader=task_queue.get_task,
        prompt_loader=lambda tid: prompt_loader(
            AgentRole.FEATURE_REALIZER.value, tid,
        ),
        head_fn=_git_rev_parse_head,
        files_changed_fn=_git_files_changed,
    )
    pl_runner = PLRunner(
        spawner,
        task_loader=task_queue.get_task,
        prompt_loader=lambda tid: prompt_loader(
            AgentRole.PLANNER.value, tid,
        ),
    )

    def _parse_findings_from_report(task_id: str) -> tuple[list, list]:
        from .orchestrator import _extract_findings_section
        report_path = context.get_report_path(task_id)
        if not report_path.exists():
            return [], []
        try:
            text = report_path.read_text()
        except OSError:
            return [], []
        critical = _extract_findings_section(text, "Critical")
        important = _extract_findings_section(text, "Important")
        return critical, important

    fi_runner = FIRunner(
        spawner,
        task_loader=task_queue.get_task,
        prompt_loader=lambda tid: prompt_loader(
            AgentRole.FEATURE_INTERPRETER.value, tid,
        ),
        run_config_loader=context.get_run_config,
        dev_server_factory=DevServer,
        worktree_path_fn=lambda tid: context.get_worktree_path(tid),
        dev_log_path_fn=lambda tid: context.get_dev_server_log_path(tid),
        head_fn=_git_rev_parse_head,
        status_fn=_git_status_porcelain,
        reset_fn=_git_reset_to,
        report_parser=_parse_findings_from_report,
    )

    manager = AgentRunManager(
        task_queue=task_queue,
        runners={"hl": hl_runner, "fr": fr_runner, "fi": fi_runner, "pl": pl_runner},
        context={"project_dir": config.project_dir,
                 "orchestra_dir": config.orchestra_dir},
        log_path_fn=lambda role, tid: str(
            context.get_log_path(f"{role}-{tid}")),
        emit=emit,
        hl_done_hook=hl_done_hook,
        fr_failed_hook=fr_failed_hook,
    )

    return CoreServices(
        engine=engine,
        session_factory=session_factory,
        task_queue=task_queue,
        context=context,
        worktree=worktree,
        spawner=spawner,
        github=github,
        runners={"hl": hl_runner, "fr": fr_runner, "fi": fi_runner, "pl": pl_runner},
        manager=manager,
        prompt_loader=prompt_loader,
    )
