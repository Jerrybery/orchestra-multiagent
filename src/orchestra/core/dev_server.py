from __future__ import annotations
import asyncio
import logging
import os
import signal
import time
from pathlib import Path
from typing import Optional

import urllib.request
import urllib.error

log = logging.getLogger(__name__)


class DevServerStartupError(Exception):
    pass


class DevServer:
    def __init__(
        self,
        cwd: Path,
        command: str,
        ready_signal: Optional[str],
        base_url: str,
        timeout: int,
        log_path: Path,
    ):
        self.cwd = Path(cwd)
        self.command = command
        self.ready_signal = ready_signal
        self.base_url = base_url
        self.timeout = timeout
        self.log_path = log_path
        self.process: Optional[asyncio.subprocess.Process] = None
        self._log_writer_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self._spawn()
        try:
            if self.ready_signal:
                await self._wait_ready_signal()
            else:
                await self._wait_ping()
        except Exception:
            await self.stop()
            raise

    async def _spawn(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = open(self.log_path, "w", buffering=1)
        # preexec_fn=os.setsid -> new process group
        self.process = await asyncio.create_subprocess_shell(
            self.command,
            cwd=str(self.cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
        self._stdout_buffer: list[str] = []
        self._log_writer_task = asyncio.create_task(self._tee_stdout())

    async def _tee_stdout(self) -> None:
        assert self.process and self.process.stdout
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            decoded = line.decode(errors="replace")
            self._log_fh.write(decoded)
            self._log_fh.flush()
            self._stdout_buffer.append(decoded)

    async def _wait_ready_signal(self) -> None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            for line in self._stdout_buffer:
                if self.ready_signal in line:
                    log.info("dev_server ready (signal matched)")
                    return
            if self.process.returncode is not None:
                raise DevServerStartupError(
                    f"dev server exited (code {self.process.returncode}) before ready"
                )
            await asyncio.sleep(0.2)
        raise DevServerStartupError(
            f"dev server did not emit '{self.ready_signal}' within {self.timeout}s"
        )

    async def _wait_ping(self) -> None:
        await asyncio.sleep(5)  # Let server come up
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: urllib.request.urlopen(self.base_url, timeout=2)
                )
                log.info("dev_server ready (ping ok)")
                return
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass
            if self.process.returncode is not None:
                raise DevServerStartupError(
                    f"dev server exited before responding to {self.base_url}"
                )
            await asyncio.sleep(1)
        raise DevServerStartupError(
            f"dev server did not respond at {self.base_url} within {self.timeout}s"
        )

    async def stop(self) -> None:
        if self.process and self.process.returncode is None:
            try:
                pgid = os.getpgid(self.process.pid)
                os.killpg(pgid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    os.killpg(pgid, signal.SIGKILL)
                    await self.process.wait()
            except ProcessLookupError:
                pass
        if self._log_writer_task and not self._log_writer_task.done():
            self._log_writer_task.cancel()
        if hasattr(self, "_log_fh"):
            self._log_fh.close()
