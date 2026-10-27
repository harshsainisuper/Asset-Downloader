"""Optional ACCESS_PASSWORD protection (HTTP Basic auth) for internet-exposed installs."""

import base64

import pytest

from .conftest import requires_ffmpeg, wait_for_job

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def basic(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_no_password_means_open(client):
    assert client.get("/api/config").status_code == 200


def test_everything_requires_password(make_client):
    client = make_client(access_password="s3cret")
    for method, path in [("GET", "/api/config"), ("POST", "/api/info"), ("GET", "/"), ("GET", "/api/files/x/y")]:
        resp = client.request(method, path, json={"url": URL} if method == "POST" else None)
        assert resp.status_code == 401, path
        assert resp.headers["www-authenticate"].startswith("Basic realm=")
        assert resp.json()["error"]["code"] == "auth_required"


def test_health_stays_open_for_container_checks(make_client):
    client = make_client(access_password="s3cret")
    assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("user", ["me", "", "anything at all"])
def test_correct_password_any_username(make_client, user):
    client = make_client(access_password="s3cret")
    assert client.get("/api/config", headers=basic(user, "s3cret")).status_code == 200


@pytest.mark.parametrize("header", [
    basic("me", "wrong"), basic("me", "s3cret "), basic("me", "S3CRET"),
    {"Authorization": "Basic not-base64!!"}, {"Authorization": "Bearer s3cret"},
    {"Authorization": "Basic " + base64.b64encode(b"no-colon").decode()},
])
def test_wrong_or_malformed_credentials(make_client, header):
    client = make_client(access_password="s3cret")
    assert client.get("/api/config", headers=header).status_code == 401


def test_repeated_wrong_passwords_are_rate_limited(make_client):
    client = make_client(access_password="s3cret")
    codes = [client.get("/api/config", headers=basic("me", f"guess{i}")).status_code for i in range(22)]
    assert codes[:20] == [401] * 20 and codes[20:] == [429, 429]
    # Requests without credentials (a browser's first visit) are never counted as failures.
    assert client.get("/api/config").status_code == 401


@requires_ffmpeg
def test_full_job_and_download_behind_password(make_client):
    client = make_client(access_password="s3cret")
    client.headers.update(basic("me", "s3cret"))
    resp = client.post("/api/jobs", json={"url": URL, "outputs": {"audio": {"format": "m4a"}}})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    done = wait_for_job(client, body["job_id"], body["token"])
    f = done["tasks"][0]["files"][0]
    assert client.get(f["download_url"]).status_code == 200
    client.headers.pop("Authorization")
    assert client.get(f["download_url"]).status_code == 401  # signed link alone is not enough
