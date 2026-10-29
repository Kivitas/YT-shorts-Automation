from pathlib import Path
from types import SimpleNamespace

import pytest

import autoshorts.longform.publish as pub
from autoshorts.longform.types import LongformResult


def _result(tmp_path: Path) -> LongformResult:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    return LongformResult(
        topic="discipline",
        title="Discipline: small habits",
        video_path=video,
        thumbnail_path=tmp_path / "t.png",
        duration=120.0,
        description="desc",
        hashtags=["discipline"],
        provider="api",
    )


@pytest.fixture
def counter(tmp_path, monkeypatch):
    path = tmp_path / "longform_uploads.json"
    monkeypatch.setattr(pub, "_counter_path", lambda: path)
    return path


def test_cap_is_four_per_day(counter):
    assert pub.longform_remaining_today() == 4
    for _ in range(4):
        pub._record_upload()
    assert pub.longform_uploads_today() == 4
    assert pub.longform_remaining_today() == 0


def test_publish_refused_at_cap_without_uploading(counter, tmp_path, monkeypatch):
    for _ in range(4):
        pub._record_upload()
    import autoshorts.uploader.youtube_api as yt

    called = []
    monkeypatch.setattr(yt, "YouTubeApiUploader", lambda **kw: called.append(kw))
    with pytest.raises(RuntimeError, match="cap reached"):
        pub.publish_longform(_result(tmp_path), config=SimpleNamespace(), state_manager=object())
    assert called == []


def test_publish_records_upload_and_returns_url(counter, tmp_path, monkeypatch):
    import autoshorts.quota.quota_tracker as qt
    import autoshorts.uploader.youtube_api as yt

    class FakeUploader:
        def __init__(self, **kw):
            pass

        def upload(self, **kw):
            assert kw["title"] == "Discipline: small habits"
            return SimpleNamespace(url="https://youtube.com/watch?v=abc")

    cfg = SimpleNamespace(
        upload=SimpleNamespace(default_privacy_status="unlisted", category_id="27")
    )
    monkeypatch.setattr(yt, "YouTubeApiUploader", FakeUploader)
    monkeypatch.setattr(qt, "QuotaTracker", lambda **kw: object())
    url = pub.publish_longform(_result(tmp_path), config=cfg, state_manager=object())
    assert url == "https://youtube.com/watch?v=abc"
    assert pub.longform_uploads_today() == 1


def test_publish_passes_thumbnail_and_survives_corrupt_counter(counter, tmp_path, monkeypatch):
    import autoshorts.quota.quota_tracker as qt
    import autoshorts.uploader.youtube_api as yt

    seen = {}

    class FakeUploader:
        def __init__(self, **kw):
            pass

        def upload(self, **kw):
            seen.update(kw)
            return SimpleNamespace(url="https://youtube.com/watch?v=abc")

    counter.write_text('{"2020-01-01": "oops", "x": null}', encoding="utf-8")
    cfg = SimpleNamespace(
        upload=SimpleNamespace(default_privacy_status="unlisted", category_id="27")
    )
    monkeypatch.setattr(yt, "YouTubeApiUploader", FakeUploader)
    monkeypatch.setattr(qt, "QuotaTracker", lambda **kw: object())
    res = _result(tmp_path)
    pub.publish_longform(res, config=cfg, state_manager=object())
    assert seen["thumbnail_path"] == res.thumbnail_path
    assert pub.longform_uploads_today() == 1
