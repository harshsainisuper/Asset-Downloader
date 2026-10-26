"""Behaviour needed when the UI (Vercel) and API (container host) are on different origins."""

from starlette.requests import Request

from app.security import client_ip

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _request(xff: str | None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": ("10.0.0.1", 1234)})


def test_client_ip_uses_entry_added_by_trusted_proxy():
    # A client can prepend fake entries; only the rightmost one comes from our proxy.
    assert client_ip(_request("6.6.6.6, 203.0.113.9"), trust_proxy=True) == "203.0.113.9"
    assert client_ip(_request("6.6.6.6"), trust_proxy=False) == "10.0.0.1"
    assert client_ip(_request(None), trust_proxy=True) == "10.0.0.1"


def test_cors_allows_configured_origin_and_job_token_header(make_client):
    client = make_client(cors_origins=["https://ytmt.vercel.app"])
    resp = client.options("/api/jobs/x", headers={
        "Origin": "https://ytmt.vercel.app",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-job-token",
    })
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://ytmt.vercel.app"
    assert "x-job-token" in resp.headers["access-control-allow-headers"].lower()
    r = client.post("/api/info", json={"url": URL}, headers={"Origin": "https://ytmt.vercel.app"})
    assert r.headers["access-control-allow-origin"] == "https://ytmt.vercel.app"


def test_cors_rejects_other_origins_and_supports_preview_regex(make_client):
    client = make_client(cors_origins=["https://ytmt.vercel.app"],
                         cors_origin_regex=r"^https://ytmt(-[a-z0-9-]+)?\.vercel\.app$")
    ok = client.post("/api/info", json={"url": URL}, headers={"Origin": "https://ytmt-git-feature-me.vercel.app"})
    assert ok.headers.get("access-control-allow-origin") == "https://ytmt-git-feature-me.vercel.app"
    bad = client.post("/api/info", json={"url": URL}, headers={"Origin": "https://evil.vercel.app"})
    assert "access-control-allow-origin" not in bad.headers
