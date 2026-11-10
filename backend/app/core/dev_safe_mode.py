"""Fail-closed request policy for the local no-op dataplane profile.

Only reviewed database-backed API operations are allowed while
``DEV_SAFE_MODE`` is enabled. The allowlist deliberately excludes host and
remote networking, diagnostics, Xray lifecycle, DNS, router mode, backups,
Docker sidecars, SSH deployment, and update operations.
"""
import re

from app.config import settings
from starlette.responses import JSONResponse


SAFE_MODE_DETAIL = (
    "TEMPEST development safe mode is active. Network and dataplane operations "
    "are disabled; this instance does not provide VPN traffic."
)

_SAFE_METHODS_BY_PATH = {
    "/health": {"GET"},
    "/api/dev/safe-mode": {"GET"},
    "/api/auth/login": {"POST"},
    "/api/auth/me": {"GET"},
    "/api/auth/change-password": {"POST"},
    "/api/system/status": {"GET"},
    "/api/system/settings": {"GET"},
    "/api/nodes": {"GET", "POST"},
    "/api/nodes/page": {"GET"},
    "/api/nodes/export-json": {"GET"},
    "/api/nodes/export-uris": {"GET"},
    "/api/nodes/reorder": {"POST"},
    "/api/routing/rules": {"GET", "POST", "DELETE"},
    "/api/routing/rules/reorder": {"POST"},
    "/api/routing/auto-disabled": {"GET"},
    "/api/user-agents": {"GET", "POST"},
    "/api/user-agents/export-json": {"GET"},
    "/api/user-agents/import-json": {"POST"},
    "/api/subscriptions": {"GET"},
    "/api/routing-sets": {"GET"},
    "/api/routing-sets/capacity": {"GET"},
    "/api/balancers": {"GET"},
    "/api/nodecircle": {"GET"},
    "/api/events": {"GET"},
}

_SAFE_PATH_PATTERNS = (
    (re.compile(r"^/api/nodes/\d+$"), {"GET", "PATCH", "DELETE"}),
    (re.compile(r"^/api/routing/rules/\d+$"), {"GET", "PATCH", "DELETE"}),
    (re.compile(r"^/api/user-agents/\d+$"), {"GET", "PATCH", "DELETE"}),
    (re.compile(r"^/api/subscriptions/\d+$"), {"GET"}),
    (re.compile(r"^/api/routing-sets/\d+$"), {"GET"}),
    (re.compile(r"^/api/balancers/\d+$"), {"GET"}),
    (re.compile(r"^/api/nodecircle/\d+$"), {"GET"}),
)


def is_dev_safe_mode() -> bool:
    return settings.dev_safe_mode


def request_is_allowed(method: str, path: str) -> bool:
    """Return whether a request is explicitly safe in the dev profile."""
    allowed_methods = _SAFE_METHODS_BY_PATH.get(path)
    if allowed_methods and method in allowed_methods:
        return True
    return any(
        method in methods and pattern.fullmatch(path)
        for pattern, methods in _SAFE_PATH_PATTERNS
    )


class DevSafeModeMiddleware:
    """Apply the safe-mode allowlist to HTTP and reject all WebSockets."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if not settings.dev_safe_mode:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send({
                "type": "websocket.close",
                "code": 1008,
                "reason": SAFE_MODE_DETAIL,
            })
            return

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        if not request_is_allowed(method, path):
            response = JSONResponse(
                status_code=423,
                content={
                    "detail": SAFE_MODE_DETAIL,
                    "code": "DEV_SAFE_MODE_NETWORK_OPERATION_DISABLED",
                    "dataplane": "mock-noop",
                },
                headers={"X-TEMPEST-Dataplane": "mock-noop"},
            )
            await response(scope, receive, send)
            return

        async def add_mode_header(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-tempest-dataplane", b"mock-noop"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, add_mode_header)
