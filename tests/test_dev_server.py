import asyncio
import pytest
import time
from pathlib import Path
from orchestra.core.dev_server import DevServer, DevServerStartupError


@pytest.mark.asyncio
async def test_start_waits_for_ready_signal(tmp_path: Path):
    """Server must wait for ready_signal in stdout before returning."""
    log = tmp_path / "log.txt"
    cmd = (
        "python -c \"import time; time.sleep(0.5); "
        "print('Ready in 1.2s', flush=True); time.sleep(30)\""
    )
    server = DevServer(
        cwd=tmp_path, command=cmd,
        ready_signal="Ready in",
        base_url="http://localhost:9999",
        timeout=10, log_path=log,
    )
    t0 = time.monotonic()
    await server.start()
    elapsed = time.monotonic() - t0
    assert 0.4 < elapsed < 3, f"Should return ~0.5s after start, got {elapsed}"
    await server.stop()


@pytest.mark.asyncio
async def test_start_times_out(tmp_path: Path):
    cmd = "python -c \"import time; time.sleep(30)\""
    server = DevServer(
        cwd=tmp_path, command=cmd,
        ready_signal="NEVER_APPEARS",
        base_url="http://localhost:9999",
        timeout=1, log_path=tmp_path / "l.txt",
    )
    with pytest.raises(DevServerStartupError):
        await server.start()
    assert server.process is None or server.process.returncode is not None


@pytest.mark.asyncio
async def test_stop_kills_process_group(tmp_path: Path):
    cmd = "sh -c 'sleep 60 & wait'"
    server = DevServer(
        cwd=tmp_path, command=cmd,
        ready_signal=None, base_url="http://localhost:9999",
        timeout=5, log_path=tmp_path / "l.txt",
    )
    await server._spawn()  # internal helper
    pid = server.process.pid
    await server.stop()
    import os
    try:
        os.kill(pid, 0)
        assert False, "Process group still alive"
    except ProcessLookupError:
        pass
