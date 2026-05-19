"""CLI entry point for Orchestra."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import yaml

from .core.orchestrator import Orchestrator, OrchestraConfig
from .core.task_queue import TaskStatus
from .core.issue_tracker import WatchConfig


def load_config(config_path: Path, project_dir: Path) -> OrchestraConfig:
    """Load config.yaml and build OrchestraConfig."""
    from .core.claude_config import ClaudeConfigManager
    from .core.vault import Vault

    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    conc = raw.get("concurrency", {})
    orchestra_dir = project_dir / ".orchestra"

    config_mgr = ClaudeConfigManager(config_path)
    vault = Vault(
        vault_path=orchestra_dir / "vault.enc",
        key_path=Path.home() / ".orchestra-vault-key",
    )

    profile = config_mgr.active_profile()

    database_config = raw.get("database", "sqlite")

    return OrchestraConfig(
        project_dir=project_dir,
        orchestra_dir=orchestra_dir,
        max_fr=conc.get("feature_realizer", 2),
        max_fi=conc.get("feature_interpreter", 1),
        max_hl=conc.get("head_leader", 1),
        claude_cmd=config_mgr.config.command,
        max_turns=profile.max_turns,
        model=profile.model,
        claude_config_mgr=config_mgr,
        vault=vault,
        database_config=database_config,
    )


def load_watch_config(config_path: Path) -> WatchConfig:
    """Load watch configuration from orchestra.yaml."""
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    watch = raw.get("watch", {})
    return WatchConfig(
        watch_labels=watch.get("labels", ["discuss"]),
        poll_interval=watch.get("poll_interval", 120),
        auto_submit=watch.get("auto_submit", False),
        max_depth=watch.get("max_depth", 3),
        max_issues_per_tree=watch.get("max_issues_per_tree", 15),
        ready_label=watch.get("ready_label", "orchestra-ready"),
    )


async def cmd_init(args: argparse.Namespace) -> None:
    """Initialize orchestra in a project directory."""
    project_dir = Path(args.project).resolve()
    project_dir.mkdir(parents=True, exist_ok=True)

    config_path = Path(args.config) if args.config else project_dir / "orchestra.yaml"
    config = load_config(config_path, project_dir)

    orch = Orchestrator(config)
    await orch.init()
    await orch.close()

    # Copy default config if none exists
    default_config = project_dir / "orchestra.yaml"
    if not default_config.exists():
        default_config.write_text(yaml.dump({
            "concurrency": {
                "head_leader": config.max_hl,
                "feature_realizer": config.max_fr,
                "feature_interpreter": config.max_fi,
            },
            "claude": {
                "command": config.claude_cmd,
                "max_turns": config.max_turns,
            },
        }, default_flow_style=False))

    print(f"Orchestra initialized at {project_dir}")
    print(f"  .orchestra/context/   — shared context")
    print(f"  .orchestra/worktrees/ — feature worktrees")
    print(f"  .orchestra/reports/   — verification reports")
    print(f"  .orchestra/tasks.db   — task queue")
    print(f"  orchestra.yaml        — configuration")


async def cmd_submit(args: argparse.Namespace) -> None:
    """Submit a requirement to Head Leader for decomposition."""
    project_dir = Path(args.project).resolve()
    config_path = Path(args.config) if args.config else project_dir / "orchestra.yaml"
    config = load_config(config_path, project_dir)

    orch = Orchestrator(config)
    await orch.init()

    requirement = args.requirement
    if requirement == "-":
        requirement = sys.stdin.read()

    print(f"Submitting requirement to Head Leader...")
    tasks = await orch.submit_requirement(requirement)

    if tasks:
        print(f"\nHead Leader created {len(tasks)} features:")
        for t in tasks:
            deps = f" (depends: {', '.join(t.depends_on)})" if t.depends_on else ""
            print(f"  [{t.id}] {t.title}{deps}")
    else:
        print("Head Leader did not produce any features. Check logs.")

    await orch.close()


async def cmd_run(args: argparse.Namespace) -> None:
    """Run the orchestrator loop — assigns FRs and FIs automatically."""
    project_dir = Path(args.project).resolve()
    config_path = Path(args.config) if args.config else project_dir / "orchestra.yaml"
    config = load_config(config_path, project_dir)

    orch = Orchestrator(config)
    await orch.init()

    async def log_event(event: str, data: dict) -> None:
        print(f"[{event}] {json.dumps(data, default=str)}")

    orch.on_event(log_event)

    print("Orchestrator running. Press Ctrl+C to stop.")
    try:
        await orch.run_loop()
    except KeyboardInterrupt:
        orch.stop()
        print("\nStopped.")
    finally:
        await orch.close()


async def cmd_status(args: argparse.Namespace) -> None:
    """Show current task status."""
    project_dir = Path(args.project).resolve()
    config_path = Path(args.config) if args.config else project_dir / "orchestra.yaml"
    config = load_config(config_path, project_dir)

    orch = Orchestrator(config)
    await orch.init()

    tasks = await orch.task_queue.get_tasks()
    if not tasks:
        print("No tasks.")
    else:
        # Group by status
        by_status: dict[str, list] = {}
        for t in tasks:
            by_status.setdefault(t.status.value, []).append(t)

        for status, group in by_status.items():
            print(f"\n  {status.upper()} ({len(group)})")
            for t in group:
                extra = ""
                if t.assigned_to:
                    extra += f" [agent: {t.assigned_to}]"
                if t.depends_on:
                    extra += f" (deps: {', '.join(t.depends_on)})"
                print(f"    {t.id}: {t.title}{extra}")

    summary = await orch.task_queue.all_tasks_summary()
    total = sum(summary.values())
    done = summary.get("done", 0)
    print(f"\n  Total: {total} | Done: {done} | Remaining: {total - done}")

    await orch.close()


async def cmd_review(args: argparse.Namespace) -> None:
    """Show tasks awaiting review and accept/reject them."""
    project_dir = Path(args.project).resolve()
    config_path = Path(args.config) if args.config else project_dir / "orchestra.yaml"
    config = load_config(config_path, project_dir)

    orch = Orchestrator(config)
    await orch.init()

    review_tasks = await orch.task_queue.get_tasks(TaskStatus.REVIEW)
    if not review_tasks:
        print("No tasks awaiting review.")
        await orch.close()
        return

    for task in review_tasks:
        print(f"\n{'='*60}")
        print(f"  Task: {task.id} — {task.title}")
        print(f"  Branch: {task.branch}")
        print(f"  Worktree: {task.worktree_path}")

        # Show report if available
        report = orch.context.read_report(task.id)
        if report:
            print(f"\n--- Verification Report ---")
            print(report)
            print(f"--- End Report ---\n")
        else:
            print("  (No verification report found)")

        if args.task_id and args.task_id != task.id:
            continue

        action = input(f"  Action for {task.id} [a]ccept / [r]eject / [s]kip: ").strip().lower()
        if action in ("a", "accept"):
            await orch.accept_task(task.id)
            print(f"  ✓ {task.id} accepted and merged.")
        elif action in ("r", "reject"):
            reason = input("  Rejection reason: ").strip()
            await orch.reject_task(task.id, reason)
            print(f"  ✗ {task.id} rejected → back to ASSIGNED.")
        else:
            print(f"  — Skipped.")

    await orch.close()


async def cmd_run_hl(args: argparse.Namespace) -> None:
    """Run Head Leader standalone on a requirement file."""
    from .core.standalone import StandaloneSession

    project_dir = Path(args.project).resolve()
    config_path = Path(args.config) if args.config else project_dir / "orchestra.yaml"
    config = load_config(config_path, project_dir)

    session = StandaloneSession(config, quiet=args.quiet)
    await session.init()
    try:
        input_path = Path(args.input) if args.input != "-" else Path("-")
        result = await session.run_hl(input_path)
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result["status"] == "succeeded" else 1)
    finally:
        await session.close()


async def cmd_run_fr(args: argparse.Namespace) -> None:
    """Run Feature Realizer standalone on a spec file."""
    from .core.standalone import StandaloneSession

    project_dir = Path(args.project).resolve()
    config_path = Path(args.config) if args.config else project_dir / "orchestra.yaml"
    config = load_config(config_path, project_dir)

    session = StandaloneSession(config, quiet=args.quiet)
    await session.init()
    try:
        result = await session.run_fr(
            Path(args.spec),
            task_id=args.task_id,
        )
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result["status"] == "succeeded" else 1)
    finally:
        await session.close()


async def cmd_run_fi(args: argparse.Namespace) -> None:
    """Run Feature Interpreter standalone on a branch or PR."""
    from .core.standalone import StandaloneSession

    project_dir = Path(args.project).resolve()
    config_path = Path(args.config) if args.config else project_dir / "orchestra.yaml"
    config = load_config(config_path, project_dir)

    session = StandaloneSession(config, quiet=args.quiet)
    await session.init()
    try:
        result = await session.run_fi(
            branch=args.branch,
            pr_number=args.pr,
            task_id=args.task_id,
            dev_cmd=getattr(args, "dev_cmd", None),
            dev_ready=getattr(args, "dev_ready", None),
            base_url=getattr(args, "base_url", None),
        )
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result["status"] == "succeeded" else 1)
    finally:
        await session.close()


async def cmd_watch(args: argparse.Namespace) -> None:
    """Watch GitHub issues and participate in discussions."""
    project_dir = Path(args.project).resolve()
    config_path = Path(args.config) if args.config else project_dir / "orchestra.yaml"
    config = load_config(config_path, project_dir)
    watch_config = load_watch_config(config_path)

    # Override labels from CLI if provided
    if args.labels:
        watch_config.watch_labels = args.labels

    orch = Orchestrator(config)
    await orch.init()

    async def log_event(event: str, data: dict) -> None:
        print(f"[{event}] {json.dumps(data, default=str)}")

    orch.on_event(log_event)

    print(f"Watching issues with labels: {watch_config.watch_labels}")
    print(f"Poll interval: {watch_config.poll_interval}s")
    print(f"Auto-submit: {watch_config.auto_submit}")
    print("Press Ctrl+C to stop.\n")

    await orch.start_tracking(watch_config)

    # Also run the orchestrator loop so that submitted discussions get implemented
    try:
        await orch.run_loop()
    except KeyboardInterrupt:
        orch.stop()
        orch.stop_tracking()
        print("\nStopped.")
    finally:
        await orch.close()


async def cmd_web(args: argparse.Namespace) -> None:
    """Start the web dashboard — optionally with a pre-configured project."""
    import uvicorn
    from .web.api import app as web_app, set_orchestrator

    host = args.host
    port = args.port

    # If a project is specified and already initialized, connect immediately
    project_arg = args.project
    if project_arg != ".":
        project_dir = Path(project_arg).resolve()
        if (project_dir / ".orchestra").exists():
            config_path = Path(args.config) if args.config else project_dir / "orchestra.yaml"
            config = load_config(config_path, project_dir)
            orch = Orchestrator(config)
            await orch.init()
            set_orchestrator(orch)
            # Start orchestrator loop in background
            asyncio.create_task(orch.run_loop())
            print(f"Connected to project: {project_dir}")
        else:
            print(f"Project not initialized at {project_dir} — use the web UI to set up.")
    else:
        print("No project specified — use the web UI to select and initialize a project.")

    uvi_config = uvicorn.Config(web_app, host=host, port=port, log_level="info")
    server = uvicorn.Server(uvi_config)

    print(f"Orchestra dashboard: http://{host}:{port}")

    try:
        await server.serve()
    except KeyboardInterrupt:
        pass
    finally:
        if _orchestrator := globals().get('_orchestrator'):
            pass  # cleanup handled by the server shutdown


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-20s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(prog="orchestra", description="Multi-agent orchestration system")
    parser.add_argument("--project", "-p", default=".", help="Project directory (default: cwd)")
    parser.add_argument("--config", "-c", help="Config file path (default: <project>/orchestra.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    sub.add_parser("init", help="Initialize orchestra in a project directory")

    # submit
    p_submit = sub.add_parser("submit", help="Submit a requirement to Head Leader")
    p_submit.add_argument("requirement", help="The requirement text (use '-' for stdin)")

    # run
    sub.add_parser("run", help="Run the orchestrator loop")

    # watch
    p_watch = sub.add_parser("watch", help="Watch GitHub issues and participate in discussions")
    p_watch.add_argument("--labels", nargs="+", help="Labels to watch (default: from config or 'discuss')")

    # web
    p_web = sub.add_parser("web", help="Start web dashboard + orchestrator")
    p_web.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    p_web.add_argument("--port", type=int, default=8420, help="Port (default: 8420)")

    # status
    sub.add_parser("status", help="Show task status")

    # review
    p_review = sub.add_parser("review", help="Review tasks awaiting approval")
    p_review.add_argument("task_id", nargs="?", help="Specific task to review")

    # run-hl
    p_run_hl = sub.add_parser("run-hl", help="Run Head Leader standalone")
    p_run_hl.add_argument("--input", required=True,
                          help="Requirement/roadmap file (- for stdin)")
    p_run_hl.add_argument("--quiet", "-q", action="store_true",
                          help="Suppress agent real-time output")

    # run-fr
    p_run_fr = sub.add_parser("run-fr", help="Run Feature Realizer standalone")
    p_run_fr.add_argument("--spec", required=True, help="Spec file path")
    p_run_fr.add_argument("--task-id", help="Task ID (default: derived from filename)")
    p_run_fr.add_argument("--quiet", "-q", action="store_true")

    # run-fi
    p_run_fi = sub.add_parser("run-fi", help="Run Feature Interpreter standalone")
    p_run_fi_input = p_run_fi.add_mutually_exclusive_group(required=True)
    p_run_fi_input.add_argument("--branch", help="Branch to review")
    p_run_fi_input.add_argument("--pr", type=int, help="PR number to review")
    p_run_fi.add_argument("--task-id", help="Task ID (default: derived from branch)")
    p_run_fi.add_argument("--dev-cmd", help="Dev server command")
    p_run_fi.add_argument("--dev-ready", help="Dev server ready signal")
    p_run_fi.add_argument("--base-url", help="Dev server base URL")
    p_run_fi.add_argument("--quiet", "-q", action="store_true")

    args = parser.parse_args()

    cmd_map = {
        "init": cmd_init,
        "submit": cmd_submit,
        "run": cmd_run,
        "watch": cmd_watch,
        "web": cmd_web,
        "status": cmd_status,
        "review": cmd_review,
        "run-hl": cmd_run_hl,
        "run-fr": cmd_run_fr,
        "run-fi": cmd_run_fi,
    }

    asyncio.run(cmd_map[args.command](args))


if __name__ == "__main__":
    main()
