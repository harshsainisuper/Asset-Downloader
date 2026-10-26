"""Configurable remedies for YouTube bot checks: proxy, cookies, PO-token provider."""

from __future__ import annotations

import json
import os
import stat
import sys
import time

import anyio
import pytest

from app.config import Settings
from app.errors import map_provider_error
from app.media.pot_provider import BgutilSupervisor, ping
from app.media.ytdlp_provider import YtDlpProvider

COOKIES = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tPREF\tf6=40000000\n"


def _provider(tmp_path, **kw) -> YtDlpProvider:
    return YtDlpProvider(Settings(_env_file=None, temp_dir=tmp_path / "t", **kw))


def test_defaults_add_nothing(tmp_path):
    opts = _provider(tmp_path)._base_opts()
    assert "proxy" not in opts and "extractor_args" not in opts and "cookiefile" not in opts


def test_proxy_player_clients_and_pot_provider(tmp_path):
    opts = _provider(tmp_path, ytdlp_proxy="socks5://10.0.0.2:1080", ytdlp_player_clients="mweb, web_safari",
                     pot_provider="bgutil", bgutil_base_url="http://127.0.0.1:4416")._base_opts()
    assert opts["proxy"] == "socks5://10.0.0.2:1080"
    assert opts["extractor_args"] == {
        "youtube": {"player_client": ["mweb", "web_safari"]},
        "youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:4416"]},
    }
    # Security restrictions are never relaxed by these options.
    assert opts["allowed_extractors"] == ["youtube"] and opts["noplaylist"] is True


def test_cookies_use_private_copy_and_leave_original_untouched(tmp_path):
    original = tmp_path / "secrets" / "cookies.txt"
    original.parent.mkdir()
    original.write_text(COOKIES)
    original.chmod(stat.S_IRUSR)  # read-only, like Render Secret Files
    provider = _provider(tmp_path, ytdlp_cookies_file=str(original))

    with provider._ydl() as ydl:
        used = ydl.params["cookiefile"]
        assert used != str(original) and os.path.exists(used)
        assert open(used).read() == COOKIES
        assert any(c.name == "PREF" for c in ydl.cookiejar)
    assert not os.path.exists(used)  # private copy removed
    assert original.read_text() == COOKIES


def test_missing_cookie_file_is_ignored_and_reported(tmp_path):
    provider = _provider(tmp_path, ytdlp_cookies_file=str(tmp_path / "nope.txt"))
    with provider._ydl() as ydl:
        assert not ydl.params.get("cookiefile")
    diag = provider.diagnostics()
    assert diag["cookies_configured"] is False and diag["cookies_file_missing"] is True


def test_diagnostics_never_expose_secrets(tmp_path):
    cookies = tmp_path / "c.txt"
    cookies.write_text(COOKIES)
    provider = _provider(tmp_path, ytdlp_cookies_file=str(cookies),
                         ytdlp_proxy="http://user:hunter2@proxy.example:8080")
    diag = json.dumps(provider.diagnostics())
    assert "hunter2" not in diag and "proxy.example" not in diag and "PREF" not in diag
    parsed = json.loads(diag)
    assert parsed["cookies_configured"] is True and parsed["proxy_configured"] is True


def test_blocked_message_points_operator_to_fixes():
    err = map_provider_error(Exception("ERROR: [youtube] x: Sign in to confirm you're not a bot"))
    assert err.code == "provider_blocked"
    assert "cookies" in err.message and "proxy" in err.message


# --------------------------------------------------------------------------- supervisor

FAKE_SERVER = r'''
import argparse, json, http.server, os, time
p = argparse.ArgumentParser(); p.add_argument("--port", type=int); p.add_argument("--host")
a = p.parse_args()
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"version": "test", "pid": os.getpid(), "server_uptime": 1}).encode()
        self.send_response(200 if self.path == "/ping" else 404)
        self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass
print("fake bgutil listening", flush=True)
http.server.HTTPServer((a.host, a.port), H).serve_forever()
'''


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_supervisor_starts_restarts_and_stops(tmp_path):
    script = tmp_path / "server" / "build" / "main.js"  # same layout as the real server
    script.parent.mkdir(parents=True)
    script.write_text(FAKE_SERVER)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    sup = BgutilSupervisor.for_script(sys.executable, str(script), base)
    assert sup.argv[2:] == ["--port", str(port), "--host", "127.0.0.1"]

    async def wait_ping(deadline: float = 10.0):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            pong = await ping(base, timeout=0.5)
            if pong:
                return pong
            await anyio.sleep(0.1)
        raise AssertionError("server never became reachable")

    async def scenario():
        await sup.start()
        first = await wait_ping()
        assert first["version"] == "test" and sup.running
        sup._proc.kill()  # simulate a crash
        await anyio.sleep(0.3)
        second = await wait_ping()
        assert second["pid"] != first["pid"] and sup.restarts == 1
        await sup.stop()
        assert not sup.running
        assert await ping(base, timeout=0.5) is None

    anyio.run(scenario)


def test_config_reports_pot_server_unreachable(make_client):
    client = make_client(pot_provider="bgutil", bgutil_base_url=f"http://127.0.0.1:{_free_port()}")
    data = client.get("/api/config").json()
    assert data["provider"]["pot_server"] == {"reachable": False, "version": None}


@pytest.mark.parametrize("script", ["", "/does/not/exist/main.js"])
def test_app_starts_without_bundled_server(make_client, script):
    client = make_client(pot_provider="bgutil", bgutil_server_script=script)
    assert client.get("/api/health").status_code == 200
    assert client.app.state.pot is None
