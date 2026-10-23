"""Security tests for the dashboard API: CORS, Host/Origin guard, upload cap, and .env escaping."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from autoshorts.core.config import load_config
from autoshorts.core.settings_manager import read_env_file, update_env_file
from autoshorts.core.state_manager import StateManager
from autoshorts.dashboard import app as dashboard_app
from autoshorts.dashboard.app import create_app
from autoshorts.quota.quota_tracker import QuotaTracker

LOCAL_ORIGIN = "http://localhost:8765"
EVIL_ORIGIN = "http://evil.example"


@pytest.fixture
def secure_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Build a dashboard app with mocked pipeline and loopback-based test client."""
    state_mgr = StateManager(db_path=tmp_path / "security.db")

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    cfg_file = cfg_dir / "config.yaml"
    cfg_file.write_text(
        """
app:
  name: "AutoShorts-OnDevice"
  version: "1.0.0"
llm:
  provider: "local"
  model: null
scheduler:
  enabled: true
upload:
  mode: "api"
  privacy_status: "unlisted"
""",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    assets_dir = tmp_path / "assets"

    monkeypatch.setattr("autoshorts.core.paths.get_data_dir", lambda: data_dir)
    monkeypatch.setattr("autoshorts.core.settings_manager.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("autoshorts.core.settings_manager.get_config_dir", lambda: cfg_dir)
    monkeypatch.setattr("autoshorts.core.settings_manager.get_data_dir", lambda: data_dir)
    monkeypatch.setattr("autoshorts.core.config.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("autoshorts.core.config.get_config_dir", lambda: cfg_dir)
    monkeypatch.setattr(dashboard_app, "get_assets_dir", lambda: assets_dir)

    cfg = load_config(cfg_file)
    pipeline = MagicMock()
    pipeline.config = cfg
    pipeline.state_manager = state_mgr
    pipeline.quota_tracker = QuotaTracker(state_manager=state_mgr)
    pipeline.scheduler = MagicMock()
    pipeline.scheduler.is_paused.return_value = False
    pipeline.cleaner = MagicMock()

    app = create_app(
        pipeline=pipeline,
        state_manager=state_mgr,
        config=cfg,
        run_scheduler_worker=False,
    )
    client = TestClient(app, base_url=LOCAL_ORIGIN)
    return {
        "client": client,
        "env_file": env_file,
        "assets_dir": assets_dir,
    }


# -----------------------------------------------------------------------------
# CORS
# -----------------------------------------------------------------------------


def test_cors_rejects_evil_origin_echo(secure_client: dict) -> None:
    """A foreign Origin must not be echoed back in Access-Control-Allow-Origin."""
    client: TestClient = secure_client["client"]
    resp = client.get("/api/settings", headers={"Origin": EVIL_ORIGIN})
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}
    assert "access-control-allow-credentials" not in {k.lower() for k in resp.headers}


def test_cors_rejects_evil_origin_preflight(secure_client: dict) -> None:
    """Preflight from a foreign origin gets no CORS allowance."""
    client: TestClient = secure_client["client"]
    resp = client.options(
        "/api/settings",
        headers={
            "Origin": EVIL_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.headers.get("access-control-allow-origin") != EVIL_ORIGIN


def test_cors_allows_local_origin(secure_client: dict) -> None:
    """Loopback origins are still allowed (without credentials)."""
    client: TestClient = secure_client["client"]
    resp = client.get("/api/settings", headers={"Origin": "http://127.0.0.1:8765"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:8765"
    assert "access-control-allow-credentials" not in {k.lower() for k in resp.headers}


# -----------------------------------------------------------------------------
# Host and Origin guard
# -----------------------------------------------------------------------------


def test_host_header_rejection_blocks_dns_rebinding(secure_client: dict) -> None:
    """A non-loopback Host header is rejected with 403 JSON."""
    client: TestClient = secure_client["client"]
    resp = client.get("/api/settings", headers={"Host": "evil.example:8765"})
    assert resp.status_code == 403
    assert "detail" in resp.json()


def test_host_header_loopback_variants_allowed(secure_client: dict) -> None:
    """localhost, 127.0.0.1 and [::1] with any port pass the Host check."""
    client: TestClient = secure_client["client"]
    for host in ("localhost", "127.0.0.1:8765", "[::1]:8765", "localhost:5173"):
        resp = client.get("/api/settings", headers={"Host": host})
        assert resp.status_code == 200, host


def test_cross_site_post_rejected(secure_client: dict) -> None:
    """A state-changing request from a foreign Origin is refused."""
    client: TestClient = secure_client["client"]
    resp = client.post("/api/logs/clear", headers={"Origin": EVIL_ORIGIN})
    assert resp.status_code == 403


def test_same_origin_post_allowed(secure_client: dict) -> None:
    """The dashboard's own same-origin POST (Origin = local dashboard) still works."""
    client: TestClient = secure_client["client"]
    resp = client.post("/api/logs/clear", headers={"Origin": LOCAL_ORIGIN})
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_post_without_origin_allowed_for_local_scripts(secure_client: dict) -> None:
    """Requests with no Origin (curl, scripts) remain allowed for the local user."""
    client: TestClient = secure_client["client"]
    resp = client.post("/api/logs/clear")
    assert resp.status_code == 200


def test_app_version_uses_package_version(secure_client: dict) -> None:
    """FastAPI version is taken from the package __version__."""
    from autoshorts import __version__

    assert secure_client["client"].app.version == __version__


# -----------------------------------------------------------------------------
# Upload size cap
# -----------------------------------------------------------------------------


def test_background_upload_over_cap_returns_413(
    secure_client: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oversized uploads return 413 and leave no partial file behind."""
    client: TestClient = secure_client["client"]
    monkeypatch.setattr(dashboard_app, "MAX_BACKGROUND_UPLOAD_BYTES", 1024)

    resp = client.post(
        "/api/backgrounds/upload",
        data={"category": "minecraft"},
        files={"file": ("big.mp4", b"x" * 4096, "video/mp4")},
    )
    assert resp.status_code == 413
    target = secure_client["assets_dir"] / "backgrounds" / "minecraft" / "big.mp4"
    assert not target.exists()


# -----------------------------------------------------------------------------
# .env escaping and newline rejection
# -----------------------------------------------------------------------------


def test_env_value_with_newline_rejected(tmp_path: Path) -> None:
    """Values containing newlines cannot inject extra variables."""
    env_file = tmp_path / ".env"
    env_file.write_text('EXISTING="ok"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="newline"):
        update_env_file(env_file, {"GEMINI_API_KEY": "abc\nINJECTED=1"})
    with pytest.raises(ValueError, match="newline"):
        update_env_file(env_file, {"GEMINI_API_KEY": "abc\rINJECTED=1"})

    assert env_file.read_text(encoding="utf-8") == 'EXISTING="ok"\n'
    assert "INJECTED" not in read_env_file(env_file)


def test_env_quote_and_backslash_round_trip(tmp_path: Path) -> None:
    """Quotes and backslashes survive a write followed by a read."""
    env_file = tmp_path / ".env"
    value = 'C:\\m\\a"b'
    update_env_file(env_file, {"LOCAL_GGUF_PATH": value})
    assert read_env_file(env_file)["LOCAL_GGUF_PATH"] == value

    trailing = "D:\\models\\"
    update_env_file(env_file, {"LOCAL_GGUF_PATH": trailing})
    assert read_env_file(env_file)["LOCAL_GGUF_PATH"] == trailing


def test_settings_post_with_newline_secret_returns_400(secure_client: dict) -> None:
    """POST /api/settings turns the ValueError from a newline secret into HTTP 400."""
    client: TestClient = secure_client["client"]
    resp = client.post(
        "/api/settings",
        json={"secrets": {"gemini_api_key": "AIzaSyGood\nEVIL=1"}},
    )
    assert resp.status_code == 400
    assert not secure_client["env_file"].exists() or "EVIL" not in (
        secure_client["env_file"].read_text(encoding="utf-8")
    )
