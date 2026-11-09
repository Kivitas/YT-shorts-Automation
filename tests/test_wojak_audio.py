"""Unit tests for Wojak comedic audio synthesis and mastering."""

from pathlib import Path

import numpy as np

from autoshorts.longform.wojak_audio import (
    SAMPLE_RATE,
    assemble_wojak_audio,
    generate_procedural_wojak_bgm,
    read_wav,
    synth_comedic_pop,
    synth_kaching,
    synth_record_scratch,
    synth_rimshot,
    synth_transition_whoosh,
    write_wav,
)


def test_procedural_bgm_generation():
    duration = 4.0
    bgm = generate_procedural_wojak_bgm(duration, sr=SAMPLE_RATE)
    assert len(bgm) == int(duration * SAMPLE_RATE)
    assert not np.isnan(bgm).any()
    assert not np.isinf(bgm).any()
    # Ensure audio is not dead silent
    assert float(np.max(np.abs(bgm))) > 0.05


def test_sfx_generators():
    sfx_funcs = [
        synth_transition_whoosh,
        synth_comedic_pop,
        synth_kaching,
        synth_record_scratch,
        synth_rimshot,
    ]
    for func in sfx_funcs:
        sample = func(sr=SAMPLE_RATE)
        assert len(sample) > 0
        assert not np.isnan(sample).any()
        assert float(np.max(np.abs(sample))) > 0.0


def test_wav_read_write(tmp_path: Path):
    wav_path = tmp_path / "test_io.wav"
    samples = np.sin(
        2.0 * np.pi * 440.0 * np.linspace(0, 0.5, int(SAMPLE_RATE * 0.5), endpoint=False)
    ).astype(np.float32)
    saved_path = write_wav(wav_path, samples, sr=SAMPLE_RATE)
    assert saved_path.is_file()

    loaded_samples, sr = read_wav(saved_path)
    assert sr == SAMPLE_RATE
    assert len(loaded_samples) == len(samples)
    # Check close match after 16-bit quantization
    assert np.allclose(samples, loaded_samples, atol=1e-3)


def test_assemble_wojak_audio(tmp_path: Path):
    out_wav = tmp_path / "assembled.wav"
    result_path = assemble_wojak_audio(
        total_duration_sec=3.0,
        scene_transition_timestamps=[0.0, 1.5],
        output_wav_path=out_wav,
    )
    assert result_path.is_file()
    assert result_path.stat().st_size > 1000
