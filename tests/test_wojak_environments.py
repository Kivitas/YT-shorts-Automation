"""Routing and rendering tests for procedural Wojak environments."""

from __future__ import annotations

import pytest

from autoshorts.longform import wojak_environments as env


@pytest.mark.parametrize(
    ("setting", "country", "expected"),
    [
        ("hospital", "India", "rural"),
        ("", "India", "rural"),
        ("hospital", "USA", None),
        ("rural", "Kenya", "rural"),
        ("highway", "USA", "road"),
        ("open road", "Japan", "road"),
        ("bedroom", "UK", "home"),
        ("gym locker", "Brazil", "home"),
        ("school", "France", None),
        ("transport", "India", None),
    ],
)
def test_environment_routing(setting: str, country: str, expected: str | None) -> None:
    assert env.environment_for(setting, country) == expected


@pytest.mark.parametrize("name", env.ENVIRONMENTS)
def test_each_environment_renders_full_size_rgb(name: str) -> None:
    img = env.render_environment(name, "India", 640, 360)
    assert img.size == (640, 360)
    assert img.mode == "RGB"


def test_unknown_environment_raises() -> None:
    with pytest.raises(ValueError):
        env.render_environment("moon", "USA", 64, 36)


def test_environments_differ_from_each_other() -> None:
    images = [env.render_environment(name, "USA", 320, 180).tobytes() for name in env.ENVIRONMENTS]
    assert len(set(images)) == len(images)
