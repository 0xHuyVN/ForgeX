"""CSRF / same-origin protection middleware.

The FastAPI server is bound to 127.0.0.1 by default and is intended to be
consumed by the bundled SPA only. We still add a defensive check on
state-changing requests because:

* the CORS configuration now uses ``allow_credentials=True`` (required for
  cookies / auth headers that some routers accept);
* a malicious origin could otherwise ride credentials if a user is tricked
  into visiting an attacker-controlled page while the server is running;
* Electron / Tauri hosts the SPA via a custom protocol that does not always
  send a clean ``Origin`` header, so we accept the local dev origins too.

Rule: any ``POST/PUT/PATCH/DELETE`` request whose ``Origin`` header is set
and not in the allow-list is rejected with 403. ``GET`` / ``HEAD`` /
``OPTIONS`` requests are not gated because they are supposed to be safe
under the HTTP spec and the CORS layer still controls cross-origin reads.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}
_SIDE_EFFECT_GET_PREFIXES = ("/api/system/browse", "/api/system/open-folder")


class CsrfMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, allowed_origins: set[str] | None = None,
                 exempt_paths: tuple[str, ...] = ()):
        super().__init__(app)
        self.allowed_origins = {o.rstrip("/") for o in (allowed_origins or set())}
        self.exempt_paths = tuple(exempt_paths)

    async def dispatch(self, request: Request, call_next):
        if (
            request.method in _STATE_CHANGING
            or (request.method == "GET" and self._is_side_effect_get(request.url.path))
        ) and not self._is_exempt(request.url.path):
            origin = request.headers.get("origin")
            referer = request.headers.get("referer")
            if origin is not None:
                normalised = origin.rstrip("/")
            elif referer:
                normalised = referer.split("/", 3)[:3]
                normalised = "/".join(normalised).rstrip("/")
            else:
                normalised = ""
            if normalised and normalised in self.allowed_origins:
                return await call_next(request)
            if request.method == "GET" and request.client and request.client.host in {"127.0.0.1", "::1", "localhost"} and not origin and not referer:
                return await call_next(request)
            if origin is not None or request.method == "GET":
                return JSONResponse(
                    {"detail": "CSRF: origin not allowed"},
                    status_code=403,
                )
        return await call_next(request)

    def _is_exempt(self, path: str) -> bool:
        return any(path == p or path.startswith(p.rstrip("/") + "/")
                   for p in self.exempt_paths)

    def _is_side_effect_get(self, path: str) -> bool:
        return any(path == p or path.startswith(p.rstrip("/") + "/")
                   for p in _SIDE_EFFECT_GET_PREFIXES)
