"""
API key authentication middleware.
Checks the X-API-Key header against the ORG_API_KEY environment variable.
Replace this middleware with an SSO/OIDC middleware when the org moves to SSO.
"""

from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings

# Routes that bypass API key checks (health probes, static assets, etc.)
EXEMPT_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that enforces X-API-Key header authentication.

    Design notes:
    - The secret is read from settings.org_api_key (injected via ORG_API_KEY env var).
    - All routes except EXEMPT_PATHS require the header.
    - When replacing with SSO: swap this class for an OIDC middleware that validates
      a Bearer JWT. The FastAPI app registration call stays the same.
    """

    async def dispatch(self, request: Request, call_next):
        """Validate the API key before forwarding the request."""
        if request.url.path in EXEMPT_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        # Header takes priority; query param is the fallback for anchor-href downloads.
        api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")

        if not api_key:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing X-API-Key header"},
            )

        if api_key != settings.org_api_key:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Invalid API key"},
            )

        return await call_next(request)


# TODO: Future SSO replacement — implement an OIDCMiddleware class here that:
#   1. Reads Authorization: Bearer <token> header
#   2. Validates the JWT against the org's JWKS endpoint
#   3. Attaches decoded claims to request.state.user
#   Then swap APIKeyMiddleware for OIDCMiddleware in main.py without changing routes.
