"""SafetyCleaner treats flat <job>_master_<method>.mp4 variants as masters (and only this job's)."""

from __future__ import annotations

from pathlib import Path

from autoshorts.cleanup.safety_cleaner import SafetyCleaner


def _cleaner(tmp_path: Path) -> tuple[SafetyCleaner, Path]:
    work, out = tmp_path / "work", tmp_path / "output"
    work.mkdir()
    out.mkdir()
    return SafetyCleaner(
        work_dir=work, output_dir=out, allowed_roots=[work, out], permanent=True
    ), out


def test_flat_variants_and_captioned_follow_master_policy(tmp_path: Path) -> None:
    cleaner, out = _cleaner(tmp_path)
    mine = [out / f"j1_master{tag}.mp4" for tag in ("", "_raea", "_captioned")]
    other = out / "j2_master_raea.mp4"
    for f in [*mine, other]:
        f.write_bytes(b"x")
    cleaner.cleanup_job("j1", verified=True, keep_master_mp4=False)
    assert not any(f.exists() for f in mine) and other.exists()


def test_flat_variants_kept_when_master_retained(tmp_path: Path) -> None:
    cleaner, out = _cleaner(tmp_path)
    variant = out / "j1_master_raea.mp4"
    variant.write_bytes(b"x")
    cleaner.cleanup_job("j1", verified=True, keep_master_mp4=True)
    assert variant.exists()
