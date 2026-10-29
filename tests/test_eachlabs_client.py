"""Offline tests for the each::labs client. All HTTP goes through httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from autoshorts.research_agent import eachlabs_client as ec
from autoshorts.research_agent.eachlabs_client import EachLabsError

SECRET = "test-key-do-not-leak-12345"
OK_URL = "https://storage.eachlabs.ai/tracks/demo.mp3"


@pytest.fixture(autouse=True)
def _isolate_key_sources(monkeypatch: pytest.MonkeyPatch):
    """Never read the real .env or an ambient key during these tests."""
    monkeypatch.setattr("autoshorts.core.settings_manager.read_env_file", lambda *a, **k: {})
    monkeypatch.delenv(ec.API_KEY_ENV, raising=False)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError("no HTTP call expected")


def test_is_enabled_false_when_flag_off_even_with_key(monkeypatch):
    monkeypatch.setenv(ec.API_KEY_ENV, SECRET)
    assert ec.is_enabled(False) is False


def test_is_enabled_true_only_when_flag_and_key_present(monkeypatch):
    monkeypatch.setenv(ec.API_KEY_ENV, SECRET)
    assert ec.is_enabled(True) is True


def test_is_enabled_false_when_key_missing_even_with_flag_on(monkeypatch):
    monkeypatch.delenv(ec.API_KEY_ENV, raising=False)
    monkeypatch.setattr("autoshorts.core.settings_manager.read_env_file", lambda *a, **k: {})
    assert ec.is_enabled(True) is False


def test_generate_music_rejects_empty_and_too_short_requests(monkeypatch):
    monkeypatch.setenv(ec.API_KEY_ENV, SECRET)
    with _client(_no_network) as client:
        with pytest.raises(EachLabsError):
            ec.generate_music("   ", client=client)
        with pytest.raises(EachLabsError):
            ec.generate_music("lofi beat", duration_sec=1, client=client)
        with pytest.raises(EachLabsError):
            ec.generate_music("x" * (ec.MAX_PROMPT_CHARS + 1), client=client)


def test_generate_music_returns_url_from_successful_response(monkeypatch):
    monkeypatch.setenv(ec.API_KEY_ENV, SECRET)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        content = f"Your instrumental is ready: {OK_URL}."
        return httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]},
        )

    with _client(handler) as client:
        url = ec.generate_music("warm lo-fi piano", duration_sec=45, client=client)

    assert url == OK_URL
    request = seen["request"]
    assert request.method == "POST"
    assert str(request.url) == ec.API_URL
    assert request.headers["X-API-Key"] == SECRET
    body = json.loads(request.content)
    assert body["stream"] is False
    assert body["messages"][0]["role"] == "user"
    assert "warm lo-fi piano" in body["messages"][0]["content"]


def test_generate_music_raises_on_non_200_without_leaking_key(monkeypatch):
    monkeypatch.setenv(ec.API_KEY_ENV, SECRET)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with _client(handler) as client, pytest.raises(EachLabsError) as info:
        ec.generate_music("lofi beat", client=client)

    assert "401" in str(info.value)
    assert SECRET not in str(info.value)


def test_generate_music_raises_when_response_has_no_url(monkeypatch):
    monkeypatch.setenv(ec.API_KEY_ENV, SECRET)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "no link here"}}]})

    with _client(handler) as client, pytest.raises(EachLabsError):
        ec.generate_music("lofi beat", client=client)


def test_download_rejects_non_eachlabs_host(tmp_path):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"data")

    dest = tmp_path / "track.mp3"
    with _client(handler) as client:
        with pytest.raises(EachLabsError):
            ec.download_asset("https://evil.example.com/track.mp3", dest, client=client)
        with pytest.raises(EachLabsError):
            ec.download_asset("https://eachlabs.ai.evil.com/track.mp3", dest, client=client)
        with pytest.raises(EachLabsError):
            ec.download_asset("http://storage.eachlabs.ai/track.mp3", dest, client=client)

    assert calls == []
    assert not dest.exists()


def test_download_writes_file_atomically(tmp_path):
    payload = b"ID3" + b"\x00" * 4096

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    dest = tmp_path / "music" / "track.mp3"
    with _client(handler) as client:
        result = ec.download_asset(OK_URL, dest, client=client)

    assert result == dest
    assert dest.read_bytes() == payload
    assert list(dest.parent.glob("*.part")) == []


def test_failed_download_leaves_no_partial_files(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"oops")

    dest = tmp_path / "track.mp3"
    with _client(handler) as client, pytest.raises(EachLabsError):
        ec.download_asset(OK_URL, dest, client=client)

    assert not dest.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_generate_sound_effects_disabled_raises():
    with _client(_no_network) as client, pytest.raises(EachLabsError) as exc:
        ec.generate_sound_effects("heavy wooden door creaking", client=client)
    assert "disabled" in str(exc.value)


def test_generate_sound_effects_success(monkeypatch):
    monkeypatch.setenv(ec.API_KEY_ENV, SECRET)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": f"Sound effect: {OK_URL}"}}]},
        )

    with _client(handler) as client:
        asset = ec.generate_sound_effects(
            "heavy wooden door creaking",
            duration_seconds=3,
            enabled=True,
            client=client,
        )

    assert isinstance(asset, ec.EachLabsAsset)
    assert asset.url == OK_URL
    assert asset.media_type == "audio"
    assert asset.metadata["duration_seconds"] == 3
    body = json.loads(seen["request"].content)
    assert "heavy wooden door creaking" in body["messages"][0]["content"]
    assert "3 seconds long" in body["messages"][0]["content"]


def test_generate_sound_effects_validation(monkeypatch):
    monkeypatch.setenv(ec.API_KEY_ENV, SECRET)
    with _client(_no_network) as client:
        with pytest.raises(EachLabsError):
            ec.generate_sound_effects("", enabled=True, client=client)
        with pytest.raises(EachLabsError):
            ec.generate_sound_effects("door", duration_seconds=0, enabled=True, client=client)


def test_generate_tts_success(monkeypatch):
    monkeypatch.setenv(ec.API_KEY_ENV, SECRET)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": f"Speech audio: {OK_URL}"}}]},
        )

    with _client(handler) as client:
        asset = ec.generate_tts(
            "Welcome to AutoShorts studio.",
            voice="Rachel",
            language="en",
            enabled=True,
            client=client,
        )

    assert asset.url == OK_URL
    assert asset.media_type == "audio"
    assert asset.metadata["voice"] == "Rachel"
    body = json.loads(seen["request"].content)
    assert "Welcome to AutoShorts studio." in body["messages"][0]["content"]
    assert "Rachel" in body["messages"][0]["content"]


def test_generate_song_with_lyrics(monkeypatch):
    monkeypatch.setenv(ec.API_KEY_ENV, SECRET)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": f"Song ready: {OK_URL}"}}]},
        )

    with _client(handler) as client:
        asset = ec.generate_song(
            "Indie pop summer beat",
            lyrics="Walking in the sun, summer has begun",
            duration_seconds=30,
            enabled=True,
            client=client,
        )

    assert asset.url == OK_URL
    assert asset.metadata["lyrics"] == "Walking in the sun, summer has begun"
    body = json.loads(seen["request"].content)
    assert "Lyrics:\nWalking in the sun" in body["messages"][0]["content"]


def test_generate_song_validation():
    with _client(_no_network) as client, pytest.raises(EachLabsError):
        ec.generate_song("beat", duration_seconds=2, enabled=True, client=client)


def test_edit_video_multimodal_request(monkeypatch):
    monkeypatch.setenv(ec.API_KEY_ENV, SECRET)
    video_url = "https://storage.eachlabs.ai/raw.mp4"
    edited_url = "https://storage.eachlabs.ai/output.mp4"
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": f"Edited video: {edited_url}"}}]},
        )

    with _client(handler) as client:
        asset = ec.edit_video(
            video_url,
            "Apply cinematic teal and orange color grade",
            enabled=True,
            client=client,
        )

    assert asset.url == edited_url
    assert asset.media_type == "video"
    body = json.loads(seen["request"].content)
    msg_content = body["messages"][0]["content"]
    assert isinstance(msg_content, list)
    assert msg_content[0]["text"] == "Apply cinematic teal and orange color grade"
    assert msg_content[1]["image_url"]["url"] == video_url


def test_edit_video_validation():
    with _client(_no_network) as client, pytest.raises(EachLabsError):
        ec.edit_video("", "grade video", enabled=True, client=client)
    with _client(_no_network) as client, pytest.raises(EachLabsError):
        ec.edit_video("https://example.com/vid.mp4", "", enabled=True, client=client)
