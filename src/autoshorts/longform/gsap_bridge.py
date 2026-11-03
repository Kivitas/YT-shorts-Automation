"""Bridge from the Python frame renderer to GSAP timelines evaluated by Node.

``sample_timeline`` runs ``remotion/scripts/gsap-sample.mjs`` (which uses the project's
``gsap`` package) and returns per-frame values for the animated properties. Results are
cached by the serialized spec, so a repeated spec costs one Node call per process.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

SAMPLE_SCRIPT = Path(__file__).resolve().parents[3] / "remotion" / "scripts" / "gsap-sample.mjs"
NODE_TIMEOUT_SECONDS = 30


def _serialize(spec: Mapping[str, Any]) -> str:
    return json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str)


def _run_node(payload: str) -> dict[str, list[float]]:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node executable not found on PATH; cannot sample GSAP timeline")
    if not SAMPLE_SCRIPT.is_file():
        raise RuntimeError(f"GSAP sampler script missing: {SAMPLE_SCRIPT}")
    try:
        proc = subprocess.run(
            [node, str(SAMPLE_SCRIPT), payload],
            capture_output=True,
            text=True,
            timeout=NODE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"GSAP sampler timed out after {NODE_TIMEOUT_SECONDS}s") from exc
    except OSError as exc:
        raise RuntimeError(f"failed to start node: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"GSAP sampler failed: {detail}")
    try:
        data = json.loads(proc.stdout)
        values = data["values"]
        return {str(k): [float(v) for v in vs] for k, vs in values.items()}
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"GSAP sampler returned unreadable output: {exc}") from exc


@lru_cache(maxsize=512)
def _sample_cached(
    payload: str, cache_key: str | None
) -> tuple[tuple[str, tuple[float, ...]], ...]:
    # cache_key only distinguishes callers; the payload alone determines the result.
    values = _run_node(payload)
    return tuple((k, tuple(vs)) for k, vs in values.items())


def sample_timeline(spec: dict[str, Any], cache_key: str | None = None) -> dict[str, list[float]]:
    """Evaluate a GSAP timeline spec and return ``{key: [value per frame]}``.

    ``spec`` fields (see ``remotion/scripts/gsap-sample.mjs``): ``duration`` (seconds),
    ``fps``, optional ``frames``, ``state`` (initial values) and ``tweens`` (items with
    ``at``, ``duration``, ``vars``, optional ``ease`` and ``from``).

    Raises ``RuntimeError`` when node or the sampler script is unavailable or fails.
    """
    payload = _serialize(spec)
    return {k: list(vs) for k, vs in _sample_cached(payload, cache_key)}


def sample_value(values: Sequence[float], frame: int) -> float:
    """Return ``values[frame]``, clamping ``frame`` to the first and last sample."""
    if not values:
        raise ValueError("cannot sample an empty value list")
    index = min(max(int(frame), 0), len(values) - 1)
    return float(values[index])
