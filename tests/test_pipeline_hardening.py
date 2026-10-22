"""Tests for the download/edit/upload hardening pass.

Covers the behaviours that previously let bad output pass silently:

- section downloads that resolve to the wrong stream class are rejected
- the local-source shortcut requires an explicit origin and is recorded
- an empty edit plan is reported as a placeholder instead of a success
- subtitle timing provenance is recorded
- re-render refuses to proceed on missing/empty artifacts
- "verified" means positively confirmed, and Studio DOM is explicitly weaker
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoshorts.core.exceptions import AutoShortsError
from autoshorts.core.models import (
    CONFIRMING_VERIFICATION_KINDS,
    UploadResult,
    resolve_verification,
)
from autoshorts.edit_planner.edl_schema import clamp_edl, create_empty_edl
from autoshorts.media.downloader import (
    SECTION_NO_AUDIO,
    SECTION_NO_VIDEO,
    SECTION_OK,
    Downloader,
    SectionValidation,
    validate_section_output,
)

# =============================================================================
# 1. Section output validation
# =============================================================================


def _probe(video: bool, audio: bool, duration: float = 40.0):
    return {"has_video": video, "has_audio": audio, "duration": duration}


def test_validate_section_accepts_video_and_audio(tmp_path: Path):
    f = tmp_path / "cut.mp4"
    f.write_bytes(b"payload")
    with patch(
        "autoshorts.media.downloader.probe_video_stream_info",
        return_value=_probe(True, True),
    ):
        result = validate_section_output(f)

    assert result.ok is True
    assert result.reason == SECTION_OK
    assert result.has_video and result.has_audio
    assert result.size_bytes == len(b"payload")


def test_validate_section_rejects_audio_only(tmp_path: Path):
    """A successful yt-dlp exit that yields audio only must not be accepted."""
    f = tmp_path / "cut.mp4"
    f.write_bytes(b"audio only")
    with patch(
        "autoshorts.media.downloader.probe_video_stream_info",
        return_value=_probe(False, True),
    ):
        result = validate_section_output(f)

    assert result.ok is False
    assert result.reason == SECTION_NO_VIDEO
    assert "no video stream" in result.detail


def test_validate_section_rejects_missing_audio(tmp_path: Path):
    f = tmp_path / "cut.mp4"
    f.write_bytes(b"video only")
    with patch(
        "autoshorts.media.downloader.probe_video_stream_info",
        return_value=_probe(True, False),
    ):
        result = validate_section_output(f)

    assert result.ok is False
    assert result.reason == SECTION_NO_AUDIO


def test_validate_section_rejects_missing_and_empty_files(tmp_path: Path):
    assert validate_section_output(tmp_path / "nope.mp4").ok is False

    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    assert validate_section_output(empty).ok is False


def test_validate_section_rejects_absurdly_short_output(tmp_path: Path):
    f = tmp_path / "cut.mp4"
    f.write_bytes(b"payload")
    with patch(
        "autoshorts.media.downloader.probe_video_stream_info",
        return_value=_probe(True, True, duration=0.05),
    ):
        result = validate_section_output(f)

    assert result.ok is False
    assert "shorter than" in result.detail


def test_download_section_retries_then_fails_on_persistent_audio_only(tmp_path: Path):
    """The final attempt must raise, never silently accept a wrong output class."""
    target = tmp_path / "cut.mp4"

    def fake_run(cmd, **kwargs):
        import subprocess

        o_idx = cmd.index("-o")
        Path(cmd[o_idx + 1].replace("%(ext)s", "mp4")).write_bytes(b"audio only")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    bad = SectionValidation(ok=False, reason=SECTION_NO_VIDEO, detail="audio only")
    dl = Downloader(max_retries=2, retry_delay_sec=0.01)
    with (
        patch("subprocess.run", side_effect=fake_run),
        patch("autoshorts.media.downloader.validate_section_output", return_value=bad),
        pytest.raises(Exception) as exc_info,
    ):
        dl.download_section("https://youtu.be/dQw4w9WgXcQ", 0.0, 30.0, target)

    message = str(exc_info.value)
    assert "after 2 attempt(s)" in message
    assert "missing_video" in message
    assert "Attempt trace" in message
    # The rejected output must be cleaned up, not left behind as a usable cut.
    assert not target.exists()
    assert dl.last_download_diagnostics["attempt_count"] == 2


def test_download_section_succeeds_after_retry_and_records_diagnostics(tmp_path: Path):
    target = tmp_path / "cut.mp4"
    attempts: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        import subprocess

        if "yt-dlp" in str(cmd[0]):
            attempts.append(list(cmd))
        o_idx = cmd.index("-o")
        Path(cmd[o_idx + 1].replace("%(ext)s", "mp4")).write_bytes(b"payload")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    good = SectionValidation(
        ok=True, reason=SECTION_OK, has_video=True, has_audio=True, duration=30.0
    )
    dl = Downloader(max_retries=3, retry_delay_sec=0.01)
    diagnostics: dict = {}
    with (
        patch("subprocess.run", side_effect=fake_run),
        patch("autoshorts.media.downloader.validate_section_output", return_value=good),
    ):
        result = dl.download_section(
            "https://youtu.be/dQw4w9WgXcQ", 0.0, 30.0, target, diagnostics=diagnostics
        )

    assert result == target
    assert diagnostics["attempt_count"] == 1
    assert diagnostics["validation"]["has_audio"] is True
    assert attempts[0].count("--download-sections") == 1
    # First attempt must not pre-emptively override the player client.
    assert "--extractor-args" not in attempts[0]


def test_default_format_spec_always_pairs_video_with_audio():
    """Every fallback tier must request a video+audio pair (or progressive muxed)."""
    from autoshorts.media.downloader import DEFAULT_FORMAT_SPEC

    for branch in DEFAULT_FORMAT_SPEC.split("/"):
        if not branch:
            continue
        has_video = "video" in branch or branch.lstrip("best").startswith(
            ("[", "best")
        ) or branch.startswith("best[")
        if "bestvideo" in branch:
            assert "+bestaudio" in branch, f"unpaired video branch: {branch}"
        if "bestvideo" not in branch and "+" in branch:
            pytest.fail(f"unexpected unpaired pair branch: {branch}")
        assert has_video or branch.startswith("best")


# =============================================================================
# 2. Origin explicit-ness
# =============================================================================


def test_job_origin_kind_controls_local_source_detection(tmp_path: Path):
    from autoshorts.pipeline import ShortsPipeline

    local_file = tmp_path / "clip.mp4"
    local_file.write_bytes(b"video")

    # A YouTube job must never be reinterpreted as local, even if a same-named file exists.
    yt_job = MagicMock()
    yt_job.origin_kind = "youtube"
    yt_job.raw_video_path = None
    yt_job.source_url = str(local_file)
    assert ShortsPipeline._resolve_local_source(yt_job) is None

    # Explicit local origin resolves the file.
    local_job = MagicMock()
    local_job.origin_kind = "local_file"
    local_job.raw_video_path = str(local_file)
    local_job.source_url = ""
    assert ShortsPipeline._resolve_local_source(local_job) == local_file.resolve()

    # A URL-shaped source is never treated as a path.
    url_job = MagicMock()
    url_job.origin_kind = None
    url_job.raw_video_path = None
    url_job.source_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert ShortsPipeline._resolve_local_source(url_job) is None


# =============================================================================
# 3. Editing provenance
# =============================================================================


def test_empty_edl_is_tagged_as_placeholder():
    edl = create_empty_edl(clip_duration=40.0, fallback_reason="no provider")
    assert edl.source == "empty_fallback"
    assert edl.fallback_reason == "no provider"
    assert edl.is_placeholder is True
    assert edl.has_micro_edits is False


def test_clamp_edl_preserves_provenance():
    edl = create_empty_edl(clip_duration=40.0, fallback_reason="schema failed")
    clamped = clamp_edl(edl, clip_duration=40.0)
    assert clamped.source == "empty_fallback"
    assert clamped.fallback_reason == "schema failed"


def test_planner_records_why_it_degraded():
    from autoshorts.edit_planner.multimodal_planner import MultimodalEditPlanner

    planner = MultimodalEditPlanner(provider=None)
    edl = planner.generate_edl(Path("missing.mp4"), transcript_text="hello there")
    assert edl.source == "empty_fallback"
    assert planner.last_source == "empty_fallback"
    assert planner.last_fallback_reason


def test_planner_marks_real_plan_source():
    from autoshorts.edit_planner.multimodal_planner import MultimodalEditPlanner

    payload = (
        '{"hook_in_sec": 0.0, "hook_out_sec": 20.0, "jump_cuts": [], "zoom_punch_ins": [],'
        ' "caption_corrections": [], "recommended_thumbnail_timestamp": 1.0,'
        ' "recommended_title": "A Title #Shorts", "recommended_hook": "Watch this",'
        ' "background_style_recommendation": "minecraft"}'
    )
    class _TranscriptOnlyProvider:
        """Provider exposing only complete(), i.e. no multimodal video analysis."""

        def complete(self, prompt: str, **kwargs: object) -> str:
            return payload

    provider = _TranscriptOnlyProvider()
    planner = MultimodalEditPlanner(provider=provider)
    with patch(
        "autoshorts.edit_planner.multimodal_planner.probe_clip_duration",
        return_value=30.0,
    ):
        edl = planner.generate_edl(Path("clip.mp4"), transcript_text="text")

    assert edl.source == "transcript"
    assert edl.is_placeholder is False


# =============================================================================
# 4. Verification semantics
# =============================================================================


@pytest.mark.parametrize(
    "declared,verified,expected_kind,expected_confirmed",
    [
        ("api_confirmed", True, "api_confirmed", True),
        ("local_export", True, "local_export", True),
        ("studio_dom", True, "studio_dom", False),
        ("studio_dom", False, "unverified", False),
        ("api_confirmed", False, "unverified", False),
        (None, True, "api_confirmed", True),
        (None, False, "unverified", False),
        ("garbage", True, "api_confirmed", True),
    ],
)
def test_resolve_verification_matrix(declared, verified, expected_kind, expected_confirmed):
    kind, confirmed = resolve_verification(declared, verified)
    assert kind == expected_kind
    assert confirmed is expected_confirmed


def test_studio_dom_is_never_confirming():
    assert "studio_dom" not in CONFIRMING_VERIFICATION_KINDS


def test_upload_result_defaults_to_unverified():
    result = UploadResult(video_id="abc", url="https://youtube.com/shorts/abc")
    assert result.verified is False
    assert result.verification_kind == "unverified"


# =============================================================================
# 5. Re-render preflight
# =============================================================================


def _make_pipeline(tmp_path: Path):
    from autoshorts.core.state_manager import StateManager
    from autoshorts.pipeline import ShortsPipeline

    state = StateManager(db_path=tmp_path / "state.db")
    return ShortsPipeline(state_manager=state)


def test_rerender_refuses_without_selected_clip(tmp_path: Path, monkeypatch):
    from autoshorts.core.models import Job, JobStatus

    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: tmp_path / "work")
    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: tmp_path / "output")

    pipe = _make_pipeline(tmp_path)
    clip = tmp_path / "raw.mp4"
    clip.write_bytes(b"video")

    job = Job(
        id="rerender_noclip",
        source_url="",
        status=JobStatus.READY_FOR_REVIEW,
        raw_video_path=str(clip),
        selected_clips=[],
    )
    pipe.state_manager.save_job(job)

    with pytest.raises(AutoShortsError):
        pipe.rerender_job("rerender_noclip")


def test_rerender_refuses_on_empty_raw_cut(tmp_path: Path, monkeypatch):
    from autoshorts.core.models import ClipCandidate, ClipSelection, Job, JobStatus

    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: tmp_path / "work")
    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: tmp_path / "output")

    pipe = _make_pipeline(tmp_path)
    empty = tmp_path / "empty_raw.mp4"
    empty.write_bytes(b"")

    job = Job(
        id="rerender_empty",
        source_url="",
        status=JobStatus.READY_FOR_REVIEW,
        raw_video_path=str(empty),
        selected_clips=[
            ClipSelection(candidate=ClipCandidate(start=0.0, end=20.0, text="hi there"))
        ],
    )
    pipe.state_manager.save_job(job)

    # Empty file + no way to regenerate => hard, explicit failure rather than a bad render.
    with pytest.raises(AutoShortsError):
        pipe.rerender_job("rerender_empty")
