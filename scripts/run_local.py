#!/usr/bin/env python3
"""Run YT Media Toolkit on this computer without Docker (Windows, macOS, Linux).

    python scripts/run_local.py              # http://localhost:8000
    python scripts/run_local.py --tunnel     # + public https://….trycloudflare.com URL

Needs Python 3.11+, FFmpeg/FFprobe and (to build the UI once) Node.js 22.12+. On Windows,
start-windows.bat installs those first. This script then creates backend/.venv, installs
the Python packages, builds the UI, creates .env, starts the server and opens the browser.
Re-running it only redoes the steps whose inputs changed. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = BACKEND / ".venv"
IS_WINDOWS = os.name == "nt"


def say(msg: str) -> None:
    print(f"[yt-media-toolkit] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"\n[yt-media-toolkit] ERROR: {msg}\n", file=sys.stderr, flush=True)
    sys.exit(1)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    say("> " + " ".join([Path(cmd[0]).name, *cmd[1:]]))
    try:
        subprocess.run(cmd, cwd=cwd, check=True)
    except subprocess.CalledProcessError as exc:
        fail(f"command failed with exit code {exc.returncode}: {' '.join(cmd)}")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not m:
            continue
        value = m.group(2).strip()
        if value[:1] in "\"'" and value.endswith(value[:1]) and len(value) >= 2:
            value = value[1:-1]
        else:
            value = re.sub(r"\s+#.*$", "", value)  # inline comment
        values[m.group(1)] = value
    return values


def digest(*paths: Path) -> str:
    h = hashlib.sha256()
    for p in paths:
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


# --------------------------------------------------------------------------- steps


def check_python() -> None:
    if sys.version_info < (3, 11):  # noqa: UP036 - users may launch this with an old Python
        fail(f"Python 3.11 or newer is required (this is {sys.version.split()[0]}).")


def ensure_env_file() -> dict[str, str]:
    env_file = ROOT / ".env"
    if not env_file.exists():
        shutil.copyfile(ROOT / ".env.example", env_file)
        say("Created .env from .env.example (edit it to set ACCESS_PASSWORD etc.).")
    return read_env(env_file)


def check_ffmpeg(env: dict[str, str]) -> None:
    for tool, key in (("ffmpeg", "FFMPEG_PATH"), ("ffprobe", "FFPROBE_PATH")):
        configured = env.get(key)
        if configured and Path(configured).is_file():
            continue
        if not shutil.which(tool):
            hint = ("run start-windows.bat, or: winget install Gyan.FFmpeg" if IS_WINDOWS
                    else "macOS: brew install ffmpeg   Debian/Ubuntu: sudo apt install ffmpeg")
            fail(f"{tool} was not found on PATH. Install FFmpeg ({hint}), then open a NEW terminal.")


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def ensure_venv() -> Path:
    py = venv_python()
    if not py.exists():
        say("Creating Python environment in backend/.venv ...")
        run([sys.executable, "-m", "venv", str(VENV)])
    requirements = BACKEND / "requirements.txt"
    stamp = VENV / ".requirements.sha256"
    wanted = digest(requirements)
    if not stamp.exists() or stamp.read_text().strip() != wanted:
        say("Installing Python packages (first time takes a few minutes) ...")
        run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"])
        run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)])
        stamp.write_text(wanted)
    return py


def frontend_is_current() -> bool:
    index = FRONTEND / "dist" / "index.html"
    if not index.exists():
        return False
    built = index.stat().st_mtime
    sources = [FRONTEND / "index.html", FRONTEND / "package-lock.json", FRONTEND / "vite.config.ts",
               *(p for p in (FRONTEND / "src").rglob("*") if p.is_file())]
    return all(p.stat().st_mtime <= built for p in sources if p.exists())


def ensure_frontend() -> None:
    if frontend_is_current():
        return
    npm = shutil.which("npm")
    if not npm:
        hint = "run start-windows.bat, or: winget install OpenJS.NodeJS.LTS" if IS_WINDOWS else "https://nodejs.org"
        fail(f"Node.js (npm) is needed once to build the web interface ({hint}).")
    lock_stamp = FRONTEND / "node_modules" / ".lock.sha256"
    wanted = digest(FRONTEND / "package-lock.json")
    if not lock_stamp.exists() or lock_stamp.read_text().strip() != wanted:
        say("Installing web interface dependencies ...")
        run([npm, "ci", "--no-audit", "--no-fund"], cwd=FRONTEND)
        lock_stamp.write_text(wanted)
    say("Building the web interface ...")
    run([npm, "run", "build"], cwd=FRONTEND)


def wait_healthy(url: str, proc: subprocess.Popen, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a system proxy
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            fail("the server stopped during startup; see the messages above.")
        try:
            with opener.open(url + "/api/health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.5)
    fail("the server did not become ready within 60 seconds.")


def start_tunnel(port: int, env: dict[str, str]) -> subprocess.Popen:
    if not env.get("ACCESS_PASSWORD"):
        fail("set ACCESS_PASSWORD in .env before using --tunnel: the tunnel URL is public.")
    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        hint = "winget install Cloudflare.cloudflared" if IS_WINDOWS else "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
        fail(f"cloudflared was not found ({hint}).")
    proc = subprocess.Popen(
        [cloudflared, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )

    def watch() -> None:
        assert proc.stdout
        for line in proc.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m:
                say("")
                say(f"Public URL (works from any device): {m.group(0)}")
                say("It changes every time you restart. Log in with your ACCESS_PASSWORD (any username).")
                say("")

    threading.Thread(target=watch, daemon=True).start()
    return proc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--tunnel", action="store_true", help="also open a free Cloudflare quick tunnel")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--setup-only", action="store_true", help="install/build, then exit")
    args = parser.parse_args()

    check_python()
    env = ensure_env_file()
    check_ffmpeg(env)
    py = ensure_venv()
    ensure_frontend()
    if args.setup_only:
        say("Setup complete.")
        return

    url = f"http://localhost:{args.port}"
    say(f"Starting the server on {url} ...")
    server = subprocess.Popen(
        [str(py), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.port), "--workers", "1"],
        cwd=BACKEND,
    )
    children = [server]
    try:
        wait_healthy(f"http://127.0.0.1:{args.port}", server)
        if args.tunnel:
            children.append(start_tunnel(args.port, env))
        say(f"Ready: {url}   (press Ctrl+C in this window to stop)")
        if not args.no_browser:
            webbrowser.open(url)
        server.wait()
    except KeyboardInterrupt:
        say("Stopping ...")
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == "__main__":
    main()
