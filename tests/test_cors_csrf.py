"""Smoke tests for CSRF / CORS posture.

Asserts the four properties called out in the v2 review:

* ``Origin: null`` is rejected on POSTs (closes the XSS-friendly iframe trick);
* requests from the configured local origin are allowed;
* requests with no Origin header (e.g. server-side, Electron) are allowed;
* ``GET`` requests with ``Origin: null`` are not gated (they're not state-changing).
"""
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from backend.services.csrf_guard import CsrfMiddleware


LOCAL_ORIGIN = "http://127.0.0.1:7860"
FALLBACK_LOCAL_ORIGIN = "http://127.0.0.1:7861"


@pytest.fixture
def app():
    """Build a minimal FastAPI app with the same middleware stack as the
    real ``backend.main`` but no lifespan, DB, or background worker."""
    app = FastAPI()
    # Same CSRF posture as production.
    app.add_middleware(
        CsrfMiddleware,
        allowed_origins={LOCAL_ORIGIN, "http://localhost:7860", FALLBACK_LOCAL_ORIGIN, "http://localhost:7861"},
    )
    # Same CORS posture as production.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[LOCAL_ORIGIN, "http://localhost:7860", FALLBACK_LOCAL_ORIGIN, "http://localhost:7861"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
    )

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/echo")
    def echo(payload: dict):
        return payload

    return app


def _client(app):
    return TestClient(app, raise_server_exceptions=False)


def test_options_preflight_allowed_for_local_origin(app):
    with _client(app) as c:
        res = c.options(
            "/api/echo",
            headers={
                "Origin": LOCAL_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
    assert res.status_code in (200, 204)


def test_post_from_null_origin_is_rejected(app):
    with _client(app) as c:
        res = c.post(
            "/api/echo",
            json={"name": "x"},
            headers={"Origin": "null"},
        )
    assert res.status_code == 403
    assert "origin" in res.text.lower()


def test_post_from_foreign_origin_is_rejected(app):
    with _client(app) as c:
        res = c.post(
            "/api/echo",
            json={"name": "x"},
            headers={"Origin": "http://evil.example.com"},
        )
    assert res.status_code == 403


def test_post_without_origin_is_allowed(app):
    """Same-origin requests from the bundled SPA typically omit Origin in some
    stacks (Electron / Tauri). The middleware only rejects when Origin IS set
    and not in the allow-list."""
    with _client(app) as c:
        res = c.post("/api/echo", json={"name": "no-origin-test"})
    assert res.status_code == 200, res.text
    assert res.json() == {"name": "no-origin-test"}


def test_get_with_null_origin_is_allowed(app):
    """GETs are not state-changing; the CSRF layer must not block them."""
    with _client(app) as c:
        res = c.get("/api/health", headers={"Origin": "null"})
    assert res.status_code == 200


def test_post_from_local_origin_is_allowed(app):
    """Happy path: the bundled SPA on the local dev port must still work."""
    with _client(app) as c:
        res = c.post(
            "/api/echo",
            json={"name": "ok"},
            headers={"Origin": LOCAL_ORIGIN},
        )
    assert res.status_code == 200
    assert res.json() == {"name": "ok"}


def test_post_from_local_fallback_port_is_allowed(app):
    """Packaged app can fall back from 7860 to 7861 when the primary port is busy."""
    with _client(app) as c:
        res = c.post(
            "/api/echo",
            json={"name": "fallback-ok"},
            headers={"Origin": FALLBACK_LOCAL_ORIGIN},
        )
    assert res.status_code == 200
    assert res.json() == {"name": "fallback-ok"}
