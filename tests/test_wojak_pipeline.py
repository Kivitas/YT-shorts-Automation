"""Unit and integration tests for Wojak Pipeline Orchestrator."""

import argparse
from pathlib import Path

from autoshorts.cli import cmd_wojak
from autoshorts.core.config import Settings
from autoshorts.core.state_manager import StateManager
from autoshorts.longform.wojak_pipeline import (
    WojakPipelineConfig,
    WojakScene,
    resolve_topic_scenes,
    run_wojak_pipeline,
)


def test_resolve_topic_scenes():
    title, scenes = resolve_topic_scenes("hospital", scene_count=5)
    assert "Hospital" in title
    assert len(scenes) == 5
    assert scenes[0].country == "USA"

    s_title, s_scenes = resolve_topic_scenes("school", scene_count=3)
    assert "School" in s_title
    assert len(s_scenes) == 3

    t_title, t_scenes = resolve_topic_scenes("transport", scene_count=4)
    assert "Transport" in t_title
    assert len(t_scenes) == 4


def test_run_wojak_pipeline_fast(tmp_path: Path):
    s1 = WojakScene(country="USA", caption="1x Tylenol: $4500", emotion="shocked")
    s2 = WojakScene(country="UK", caption="Waiting room from 2019", emotion="tired")
    cfg = WojakPipelineConfig(
        topic="hospital",
        scenes=[s1, s2],
        fps=15,
        disclaimer_duration=1.0,
        scene_duration=1.5,
        output_dir=tmp_path,
        generate_thumbnail=True,
    )
    result = run_wojak_pipeline(cfg)
    assert result.video_path.is_file()
    assert result.video_path.stat().st_size > 10000
    assert result.thumbnail_path is not None
    assert result.thumbnail_path.is_file()
    assert result.scene_count == 2
    assert result.duration_sec == 4.0
    assert result.streaming_fps > 0


def test_cli_cmd_wojak(tmp_path: Path):
    args = argparse.Namespace(
        topic="hospital",
        countries="USA,UK",
        segments=2,
        output=str(tmp_path / "cli_test"),
        fps=15,
        music=None,
        disclaimer_seconds=1.0,
        scene_seconds=1.5,
        no_thumbnail=False,
        publish=False,
    )
    config = Settings()
    sm = StateManager()
    exit_code = cmd_wojak(args, config, sm)
    assert exit_code == 0
    assert (tmp_path / "cli_test" / "wojak_longform.mp4").is_file()
