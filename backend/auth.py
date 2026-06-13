"""
API key authentication middleware.
Checks the X-API-Key header against the ORG_API_KEY environment variable.
Replace this middleware with an SSO/OIDC middleware when the org moves to SSO.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings

# Routes that bypass API key checks (health probes, static assets, etc.)
EXEMPT_PATHS = {"/health", "/docs", "/openapi.json", "/redoc", "/session", "/webhooks/webex"}

_SESSION_MAX_AGE = 86_400  # 24 hours


def _sign_session(key: str) -> str:
    """Return a signed, timestamped session token (no server-side storage needed)."""
    ts = str(int(time.time()))
    sig = hmac.new(key.encode(), ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def _verify_session(token: str, key: str) -> bool:
    """Return True if *token* is a valid, unexpired session token."""
    try:
        ts_str, sig = token.split(".", 1)
        ts = int(ts_str)
    except (ValueError, AttributeError):
        return False
    if time.time() - ts > _SESSION_MAX_AGE:
        return False
    expected = hmac.new(key.encode(), ts_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


class APIKeyMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that enforces X-API-Key or X-Session-Token authentication.

    Design notes:
    - The secret is read from settings.org_api_key (injected via ORG_API_KEY env var).
    - Raises RuntimeError at startup if ORG_API_KEY is missing or still the default.
    - Browser clients call GET /session to receive a short-lived signed token; that
      token is sent as X-Session-Token and never appears in the JS bundle.
    - Direct API/CLI clients continue to use X-API-Key.
    - When replacing with SSO: swap this class for an OIDC middleware that validates
      a Bearer JWT. The FastAPI app registration call stays the same.
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        key = settings.org_api_key
        if not key or key == "changeme":
            raise RuntimeError(
                "ORG_API_KEY is not configured or is still the default 'changeme' value. "
                "Set a strong secret in the environment before starting the server."
            )

    async def dispatch(self, request: Request, call_next):
        """Validate authentication before forwarding the request."""
        if request.url.path in EXEMPT_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        key = settings.org_api_key

        # Direct API key — header takes priority; query param fallback for anchor-href downloads.
        api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if api_key:
            if api_key == key:
                return await call_next(request)
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Invalid API key"},
            )

        # Session token — issued by GET /session for browser clients.
        session_token = (
            request.headers.get("X-Session-Token")
            or request.query_params.get("session_token")
        )
        if session_token and _verify_session(session_token, key):
            return await call_next(request)

        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Authentication required. Call /api/session first."},
        )


# TODO: Future SSO replacement — implement an OIDCMiddleware class here that:
#   1. Reads Authorization: Bearer <token> header
#   2. Validates the JWT against the org's JWKS endpoint
#   3. Attaches decoded claims to request.state.user
#   Then swap APIKeyMiddleware for OIDCMiddleware in main.py without changing routes.
