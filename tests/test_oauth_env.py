"""OAuth client comes from .env, and the token file is never left half-written."""

from __future__ import annotations

import json

from autoshorts.core import settings_manager
from autoshorts.core.paths import atomic_write


def _isolate_env(monkeypatch, tmp_path, values: dict[str, str]):
    env_file = tmp_path / ".env"
    env_file.write_text("".join(f'{k}="{v}"\n' for k, v in values.items()), encoding="utf-8")
    monkeypatch.setattr(settings_manager, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    return env_file


def test_client_file_is_built_from_env_when_missing(monkeypatch, tmp_path):
    _isolate_env(
        monkeypatch,
        tmp_path,
        {
            "GOOGLE_OAUTH_CLIENT_ID": "id-123.apps.googleusercontent.com",
            "GOOGLE_OAUTH_CLIENT_SECRET": "GOCSPX-test",
        },
    )
    target = tmp_path / "data" / "client_secret.json"
    assert settings_manager.ensure_client_secret_file(target) is True
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["installed"]["client_id"] == "id-123.apps.googleusercontent.com"
    assert payload["installed"]["client_secret"] == "GOCSPX-test"


def test_missing_env_credentials_are_reported(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path, {"GOOGLE_OAUTH_CLIENT_ID": "only-id"})
    assert settings_manager.get_google_oauth_client() is None
    assert (
        settings_manager.ensure_client_secret_file(tmp_path / "data" / "client_secret.json")
        is False
    )


def test_existing_client_file_is_left_alone(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path, {})
    target = tmp_path / "client_secret.json"
    target.write_text('{"installed": {}}', encoding="utf-8")
    assert settings_manager.ensure_client_secret_file(target) is True
    assert target.read_text(encoding="utf-8") == '{"installed": {}}'


def test_save_then_read_round_trips_through_env(monkeypatch, tmp_path):
    env_file = _isolate_env(monkeypatch, tmp_path, {})
    settings_manager.save_google_oauth_client("id-9.apps.googleusercontent.com", "GOCSPX-9")
    assert "GOOGLE_OAUTH_CLIENT_SECRET" in env_file.read_text(encoding="utf-8")
    assert settings_manager.get_google_oauth_client() == (
        "id-9.apps.googleusercontent.com",
        "GOCSPX-9",
    )


def test_atomic_token_write_leaves_no_partial_file(tmp_path):
    token = tmp_path / "token.json"
    token.write_text('{"refresh_token": "old"}', encoding="utf-8")
    atomic_write(token, '{"refresh_token": "new"}', encoding="utf-8")
    assert json.loads(token.read_text(encoding="utf-8")) == {"refresh_token": "new"}
    assert not list(tmp_path.glob("*.tmp"))
