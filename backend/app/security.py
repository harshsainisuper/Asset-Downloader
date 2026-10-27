"""Job access tokens, signed download URLs, rate limiting and request size limits."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def new_job_id() -> str:
    return secrets.token_urlsafe(12)


def new_file_id() -> str:
    return secrets.token_urlsafe(9)


def new_access_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str | None, token_hash: str) -> bool:
    if not token:
        return False
    return hmac.compare_digest(hash_token(token), token_hash)


def sign_download(key: bytes, job_id: str, file_id: str, expires: int) -> str:
    msg = f"{job_id}:{file_id}:{expires}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_download(key: bytes, job_id: str, file_id: str, expires: int, signature: str) -> bool:
    if expires < int(time.time()):
        return False
    expected = sign_download(key, job_id, file_id, expires)
    return hmac.compare_digest(expected, signature)


class RateLimiter:
    """Simple in-memory sliding-window limiter keyed by (bucket, client)."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, bucket: str, client: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[(bucket, client)]
            while hits and now - hits[0] > window:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # The rightmost entry is the one added by our trusted proxy; entries to its
            # left are client-supplied and can be spoofed.
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


class BodySizeLimitMiddleware:
    """Reject request bodies larger than ``max_bytes`` (declared or streamed)."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        too_large = JSONResponse(
            {"error": {"code": "request_too_large", "message": "Request body is too large."}},
            status_code=413,
        )
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        await too_large(scope, receive, send)
                        return
                except ValueError:
                    await too_large(scope, receive, send)
                    return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await too_large(scope, receive, send)


class _BodyTooLarge(Exception):
    pass


class BasicAuthMiddleware:
    """Optional password protection for the whole app: UI, API and downloads.

    Uses HTTP Basic auth (any username), which browsers prompt for natively and then send
    with every same-origin request, including signed download links. Repeated wrong
    passwords are rate-limited per client. Health checks and CORS preflights stay open.
    """

    EXEMPT_PATHS = frozenset({"/api/health"})
    REALM = 'Basic realm="YT Media Toolkit", charset="UTF-8"'

    def __init__(self, app: ASGIApp, password: str, trust_proxy: bool = False,
                 max_failures_per_minute: int = 20) -> None:
        self.app = app
        self._password = password.encode("utf-8")
        self.trust_proxy = trust_proxy
        self.max_failures = max_failures_per_minute
        self.limiter = RateLimiter()

    def _check(self, header: str | None) -> bool | None:
        """True: correct password. False: wrong credentials. None: no credentials sent."""
        if not header or not header[:6].lower() == "basic ":
            return None
        try:
            decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        _, sep, password = decoded.partition(":")
        return bool(sep) and hmac.compare_digest(password.encode("utf-8"), self._password)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope["type"] != "http" or scope.get("method") == "OPTIONS"
                or scope.get("path") in self.EXEMPT_PATHS):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        result = self._check(request.headers.get("authorization"))
        if result is True:
            await self.app(scope, receive, send)
            return
        if result is False and not self.limiter.allow(
                "auth-fail", client_ip(request, self.trust_proxy), self.max_failures):
            response = JSONResponse(
                {"error": {"code": "rate_limited", "message": "Too many wrong passwords. Try again in a minute."}},
                status_code=429,
            )
        else:
            response = JSONResponse(
                {"error": {"code": "auth_required", "message": "Password required."}},
                status_code=401,
                headers={"WWW-Authenticate": self.REALM},
            )
        await response(scope, receive, send)
