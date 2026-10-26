"""Supervisor for the bgutil proof-of-origin (PO) token HTTP server.

YouTube increasingly requires a PO token, which a real browser obtains by running
YouTube's BotGuard challenge. The bgutil-ytdlp-pot-provider yt-dlp plugin fetches these
tokens from a small local HTTP server. In the Docker image the server ships alongside
the API; this module starts it as a child process (argument list, no shell), restarts it
with backoff if it exits, and reports its health.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)


class BgutilSupervisor:
    def __init__(self, argv: list[str], base_url: str, cwd: Path | None = None):
        self.argv = argv
        self.base_url = base_url.rstrip("/")
        self.cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.restarts = 0

    @classmethod
    def for_script(cls, node: str, script: str, base_url: str) -> BgutilSupervisor:
        parts = urlsplit(base_url)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or 4416
        script_path = Path(script)
        # Server options: --port <PORT> --host <HOST> (defaults to localhost only).
        return cls([node, str(script_path), "--port", str(port), "--host", host], base_url,
                   cwd=script_path.parent.parent)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="bgutil-supervisor")

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stopping:
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *self.argv, cwd=self.cwd, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                )
            except OSError as exc:
                log.error("could not start PO token server (%s); retrying in %.0fs", exc, backoff)
            else:
                log.info("PO token server started (pid %s)", self._proc.pid)
                assert self._proc.stdout
                async for raw in self._proc.stdout:
                    log.info("bgutil: %s", raw.decode("utf-8", "replace").rstrip())
                code = await self._proc.wait()
                if self._stopping:
                    return
                log.warning("PO token server exited with code %s; restarting in %.0fs", code, backoff)
                self.restarts += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def stop(self) -> None:
        self._stopping = True
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


async def ping(base_url: str, timeout: float = 3.0) -> dict | None:
    """Return the server's /ping payload, or None when it is unreachable."""
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.get(base_url.rstrip("/") + "/ping")
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, dict) else {}
    except (httpx.HTTPError, ValueError):
        pass
    return None
