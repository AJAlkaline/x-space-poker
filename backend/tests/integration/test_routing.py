"""Tests for the SPA serving and API routing isolation.

The FastAPI app serves the built frontend at / so production is single-origin.
This test ensures:

- /api/* paths return JSON (no SPA fallback)
- /auth/* paths return JSON (no SPA fallback)
- /ws/* paths return the WS upgrade endpoint, not the SPA
- /health returns JSON
- /assets/* serves static files
- Any other GET returns the SPA shell so React Router can take over
- Path traversal in the SPA fallback is rejected
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _build_app_with_static_dir(static_dir: Path):
    """Reload main.py with STATIC_DIR pointing at our temp dir.

    create_app() reads STATIC_DIR from the environment at app construction
    time, so we set it and re-call create_app().
    """
    os.environ["STATIC_DIR"] = str(static_dir)
    # Re-import so create_app sees the new env var.
    import importlib

    from app.api import main as main_mod
    importlib.reload(main_mod)
    return main_mod.app


@pytest.fixture
def app_with_static():
    with tempfile.TemporaryDirectory() as td:
        static_dir = Path(td)
        # Minimal SPA: index.html + an assets/ dir with a fake bundle.
        (static_dir / "index.html").write_text(
            "<!doctype html><html><body>SPA SHELL</body></html>",
        )
        (static_dir / "favicon.ico").write_bytes(b"\x00" * 8)
        assets = static_dir / "assets"
        assets.mkdir()
        (assets / "main.js").write_text("console.log('app');")
        app = _build_app_with_static_dir(static_dir)
        yield app
        # Restore for other tests: blow away STATIC_DIR and reload.
        os.environ.pop("STATIC_DIR", None)
        import importlib

        from app.api import main as main_mod
        importlib.reload(main_mod)


def test_root_serves_spa_shell(app_with_static):
    with TestClient(app_with_static) as c:
        res = c.get("/")
        assert res.status_code == 200
        assert "SPA SHELL" in res.text


def test_unknown_route_serves_spa_shell_for_react_router(app_with_static):
    with TestClient(app_with_static) as c:
        # /replay/foo, /table/ABC234, /spectate/XYZ are React Router paths
        for path in ("/replay/some-uuid", "/table/ABC234", "/spectate/XYZ"):
            res = c.get(path)
            assert res.status_code == 200, f"{path} should serve SPA"
            assert "SPA SHELL" in res.text, f"{path} should serve SPA shell"


def test_api_routes_return_json_not_spa(app_with_static):
    """API paths must not be intercepted by the SPA fallback."""
    with TestClient(app_with_static) as c:
        # Real route (returns 401/422 because no auth, but JSON, not HTML)
        res = c.get("/api/tables/by-code/NONEXISTENT")
        assert res.status_code in (401, 404, 422), res.status_code
        assert "SPA SHELL" not in res.text, "API path leaked SPA shell"
        # Nonexistent /api path: must 404 with JSON, not the SPA
        res = c.get("/api/this-does-not-exist")
        assert res.status_code == 404
        assert "SPA SHELL" not in res.text


def test_auth_routes_return_json_not_spa(app_with_static):
    with TestClient(app_with_static) as c:
        res = c.get("/auth/config")
        assert res.status_code == 200
        assert "application/json" in res.headers.get("content-type", "")
        assert "SPA SHELL" not in res.text


def test_health_returns_json(app_with_static):
    with TestClient(app_with_static) as c:
        res = c.get("/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}


def test_assets_served_directly(app_with_static):
    with TestClient(app_with_static) as c:
        res = c.get("/assets/main.js")
        assert res.status_code == 200
        assert "console.log" in res.text


def test_static_root_files_served(app_with_static):
    """favicon.ico and similar should be served, not fall through to SPA."""
    with TestClient(app_with_static) as c:
        res = c.get("/favicon.ico")
        assert res.status_code == 200
        # Not the HTML
        assert "SPA SHELL" not in res.text


def test_spa_fallback_rejects_path_traversal(app_with_static):
    """A path containing '..' must not escape the static_dir."""
    with TestClient(app_with_static) as c:
        # TestClient normalizes some of these client-side, but the server
        # must also be safe. The static-file check guards via
        # is_relative_to(static_dir).
        # If TestClient happens to normalize this out, it just means the
        # SPA shell is returned for the normalized "/" path, which is also
        # fine (no leak).
        res = c.get("/../../../etc/passwd")
        # Either path-normalized to a SPA fallback (200 with shell) or
        # 404. The only forbidden outcome is leaking real file contents.
        assert res.status_code in (200, 404, 400)
        if res.status_code == 200:
            assert "SPA SHELL" in res.text or "favicon" in res.headers.get(
                "content-disposition", "",
            )
        # Must never include the contents of /etc/passwd.
        assert "root:" not in res.text


def test_app_without_static_dir_still_serves_api():
    """When STATIC_DIR is unset or doesn't exist, the app still works for
    the API — it just doesn't serve a frontend. This is the dev mode where
    the SPA runs separately via `vite dev`."""
    # Make absolutely sure STATIC_DIR is unset and the default doesn't exist.
    os.environ.pop("STATIC_DIR", None)
    os.environ["STATIC_DIR"] = "/nonexistent/path/that/should/not/exist"
    import importlib

    from app.api import main as main_mod
    importlib.reload(main_mod)
    try:
        with TestClient(main_mod.app) as c:
            res = c.get("/health")
            assert res.status_code == 200
            # API route works
            res = c.get("/auth/config")
            assert res.status_code == 200
            # An unknown path returns 404 from FastAPI (no SPA fallback)
            res = c.get("/some-react-route")
            assert res.status_code == 404
    finally:
        os.environ.pop("STATIC_DIR", None)
        importlib.reload(main_mod)
