"""Deployment behaviour: proxy-aware client IPs, optional cross-origin UI, same-origin UI serving."""

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


def test_backend_serves_built_frontend_same_origin(make_client, tmp_path):
    """Single-service deployment (Render/Docker): the API also serves the UI."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    client = make_client(serve_frontend=True, frontend_dist=dist)

    index = client.get("/")
    assert index.status_code == 200 and "id=root" in index.text
    assert "default-src 'self'" in index.headers["content-security-policy"]
    assert index.headers["cache-control"] == "no-cache"

    asset = client.get("/assets/index-abc123.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]

    assert client.get("/some/client/route").text == index.text  # SPA fallback
    assert client.get("/../../etc/passwd").text == index.text  # never escapes dist
    assert client.get("/api/health").json()["status"] == "ok"  # API still wins
    assert client.get("/api/nope").status_code == 404
