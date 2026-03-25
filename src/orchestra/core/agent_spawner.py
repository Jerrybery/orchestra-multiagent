"""Spawn and manage Claude Code CLI subprocesses for each agent role."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, Awaitable

log = logging.getLogger(__name__)

# Type for the line callback: (agent_id, stream, line) -> None
LineCallback = Callable[[str, str, str], Awaitable[None]]


class AgentRole(str, Enum):
    HEAD_LEADER = "head_leader"
    FEATURE_REALIZER = "feature_realizer"
    FEATURE_INTERPRETER = "feature_interpreter"


class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"


@dataclass
class AgentResult:
    agent_id: str
    role: AgentRole
    task_id: Optional[str]
    exit_code: int
    stdout: str
    stderr: str
    elapsed: float


@dataclass
class AgentHandle:
    agent_id: str
    role: AgentRole
    task_id: Optional[str]
    state: AgentState = AgentState.IDLE
    process: Optional[asyncio.subprocess.Process] = None
    started_at: float = 0.0
    log_path: Optional[Path] = None
    # Live log buffer — most recent lines kept in memory for web UI
    log_lines: deque = field(default_factory=lambda: deque(maxlen=500))
    stdout_buf: list = field(default_factory=list)
    stderr_buf: list = field(default_factory=list)
    # Final result text extracted from stream-json
    result_text: str = ""
    # Temp files to clean up after process ends
    _temp_files: list = field(default_factory=list)


def _summarize_stream_event(data: dict) -> Optional[str]:
    """Extract a human-readable summary from a stream-json event."""
    etype = data.get("type")

    if etype == "system":
        model = data.get("model", "")
        return f"Session started (model: {model})"

    if etype == "assistant":
        msg = data.get("message", {})
        content = msg.get("content", [])
        parts = []
        for block in content:
            if block.get("type") == "text":
                text = block.get("text", "")
                if text.strip():
                    # Show first 200 chars of text
                    parts.append(text[:200] + ("..." if len(text) > 200 else ""))
            elif block.get("type") == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                # Summarize common tools
                if name == "Read":
                    parts.append(f"[Read] {inp.get('file_path', '?')}")
                elif name == "Write":
                    parts.append(f"[Write] {inp.get('file_path', '?')}")
                elif name == "Edit":
                    parts.append(f"[Edit] {inp.get('file_path', '?')}")
                elif name == "Bash":
                    cmd = inp.get("command", "?")
                    parts.append(f"[Bash] {cmd[:120]}")
                elif name == "Glob":
                    parts.append(f"[Glob] {inp.get('pattern', '?')}")
                elif name == "Grep":
                    parts.append(f"[Grep] {inp.get('pattern', '?')}")
                else:
                    parts.append(f"[{name}]")
        return " | ".join(parts) if parts else None

    if etype == "result":
        status = "success" if not data.get("is_error") else "error"
        duration = data.get("duration_ms", 0) / 1000
        turns = data.get("num_turns", 0)
        return f"Completed ({status}, {turns} turns, {duration:.1f}s)"

    return None


class AgentSpawner:
    """Manages Claude Code CLI subprocesses with real-time output streaming."""

    def __init__(
        self,
        claude_cmd: str = "claude",
        max_turns: int = 50,
        model: Optional[str] = None,
        on_output: Optional[LineCallback] = None,
    ):
        self.claude_cmd = claude_cmd
        self.max_turns = max_turns
        self.model = model
        self.on_output = on_output
        self._agents: dict[str, AgentHandle] = {}
        self._counter = 0

    def _next_id(self, role: AgentRole) -> str:
        self._counter += 1
        return f"{role.value}-{self._counter:03d}"

    async def spawn(
        self,
        role: AgentRole,
        system_prompt: str,
        task_prompt: str,
        cwd: str | Path,
        task_id: Optional[str] = None,
        log_path: Optional[Path] = None,
        add_dirs: list[str | Path] | None = None,
    ) -> AgentHandle:
        """Spawn a Claude Code subprocess with stream-json output."""
        agent_id = self._next_id(role)

        cmd = [self.claude_cmd, "-p", "--verbose",
               "--output-format", "stream-json",
               "--permission-mode", "bypassPermissions"]

        # All long text goes to temp files to avoid ARG_MAX / shell escaping issues
        temp_files = []

        if system_prompt:
            sp_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", prefix="orch-sys-", delete=False)
            sp_file.write(system_prompt)
            sp_file.close()
            temp_files.append(sp_file.name)
            cmd.extend(["--system-prompt-file", sp_file.name])

        if self.model:
            cmd.extend(["--model", self.model])

        if add_dirs:
            for d in add_dirs:
                cmd.extend(["--add-dir", str(d)])

        # Task prompt via stdin (not positional arg) — avoids length limits and escaping issues
        log.info("[%s] Spawning in %s: %s", agent_id, cwd, task_prompt[:80])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Write task prompt to stdin and close it
        proc.stdin.write(task_prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        await proc.stdin.wait_closed()

        handle = AgentHandle(
            agent_id=agent_id,
            role=role,
            task_id=task_id,
            state=AgentState.RUNNING,
            process=proc,
            started_at=time.time(),
            log_path=log_path,
            _temp_files=temp_files,
        )
        self._agents[agent_id] = handle
        return handle

    async def _read_stdout_stream_json(self, handle: AgentHandle):
        """Read stream-json lines from stdout, parse and emit summaries."""
        while True:
            line_bytes = await handle.process.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode(errors="replace").rstrip("\n")
            handle.stdout_buf.append(line)

            # Try to parse as JSON and extract summary
            try:
                data = _json.loads(line)
            except _json.JSONDecodeError:
                # Not JSON — raw text line (fallback)
                if line.strip():
                    handle.log_lines.append(line)
                    if self.on_output:
                        await self._safe_callback(handle.agent_id, "stdout", line)
                continue

            # Extract the final result text
            if data.get("type") == "result":
                handle.result_text = data.get("result", "")

            summary = _summarize_stream_event(data)
            if summary:
                handle.log_lines.append(summary)
                if self.on_output:
                    await self._safe_callback(handle.agent_id, "stdout", summary)

    async def _read_stderr(self, handle: AgentHandle):
        """Read stderr lines (debug/error output)."""
        while True:
            line_bytes = await handle.process.stderr.readline()
            if not line_bytes:
                break
            line = line_bytes.decode(errors="replace").rstrip("\n")
            handle.stderr_buf.append(line)
            if line.strip():
                handle.log_lines.append(f"[stderr] {line}")
                if self.on_output:
                    await self._safe_callback(handle.agent_id, "stderr", line)

    async def _safe_callback(self, agent_id: str, stream: str, line: str):
        try:
            await self.on_output(agent_id, stream, line)
        except Exception:
            pass

    async def wait(self, handle: AgentHandle) -> AgentResult:
        """Wait for an agent to finish, streaming output in real-time."""
        proc = handle.process

        await asyncio.gather(
            self._read_stdout_stream_json(handle),
            self._read_stderr(handle),
        )

        await proc.wait()
        elapsed = time.time() - handle.started_at

        # For ORCHESTRA_RESULT parsing, use result_text (from stream-json "result" event)
        # Fall back to joining raw stdout lines if no result_text
        stdout = handle.result_text or "\n".join(handle.stdout_buf)
        stderr = "\n".join(handle.stderr_buf)

        handle.state = AgentState.FINISHED if proc.returncode == 0 else AgentState.FAILED

        # Write log file
        if handle.log_path:
            handle.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(handle.log_path, "w") as f:
                f.write(f"=== {handle.agent_id} ({handle.role.value}) ===\n")
                f.write(f"Task: {handle.task_id}\n")
                f.write(f"Exit: {proc.returncode} | Elapsed: {elapsed:.1f}s\n")
                f.write(f"\n--- LOG ---\n")
                for line in handle.log_lines:
                    f.write(line + "\n")
                f.write(f"\n--- RESULT TEXT ---\n{handle.result_text}\n")
                if stderr:
                    f.write(f"\n--- STDERR ---\n{stderr}\n")

        result = AgentResult(
            agent_id=handle.agent_id,
            role=handle.role,
            task_id=handle.task_id,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            elapsed=elapsed,
        )

        # Clean up temp files
        import os
        for tf in handle._temp_files:
            try:
                os.unlink(tf)
            except OSError:
                pass

        log.info("[%s] Finished (exit=%d, %.1fs)", handle.agent_id, proc.returncode, elapsed)
        return result

    async def kill(self, agent_id: str) -> None:
        """Kill a running agent."""
        handle = self._agents.get(agent_id)
        if handle and handle.process and handle.state == AgentState.RUNNING:
            try:
                handle.process.terminate()
                await asyncio.wait_for(handle.process.wait(), timeout=5)
            except ProcessLookupError:
                pass
            except asyncio.TimeoutError:
                try:
                    handle.process.kill()
                except ProcessLookupError:
                    pass
            handle.state = AgentState.FAILED
            log.info("[%s] Killed", agent_id)

    def get_running(self, role: Optional[AgentRole] = None) -> list[AgentHandle]:
        return [
            h for h in self._agents.values()
            if h.state == AgentState.RUNNING
            and (role is None or h.role == role)
        ]

    def get_all(self) -> list[AgentHandle]:
        """Return all agents (running or finished)."""
        return list(self._agents.values())

    def get_agent(self, agent_id: str) -> Optional[AgentHandle]:
        return self._agents.get(agent_id)

    def running_count(self, role: AgentRole) -> int:
        return len(self.get_running(role))
