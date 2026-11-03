"""Shorts pacing: one time-sorted list of editing events built from EDL jump cuts and zoom punches.

Pure functions only. Each event is a plain dict with a stable schema:

- ``kind``: ``"jump_cut"`` or ``"zoom_punch"``
- ``start``: event start in seconds on the clip timeline
- ``end``: event end in seconds (a zoom punch lasts its ``duration``)
- ``strength``: ``1.0`` for a jump cut, the zoom ``scale`` for a zoom punch
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

JUMP_CUT = "jump_cut"
ZOOM_PUNCH = "zoom_punch"
EVENT_KEYS = ("kind", "start", "end", "strength")


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a pydantic model, a dataclass-like object or a plain mapping."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _event(kind: str, start: float, end: float, strength: float) -> dict[str, Any]:
    return {"kind": kind, "start": start, "end": end, "strength": strength}


def build_pacing_events(
    jump_cuts: Iterable[Any] | None,
    zoom_punch_ins: Iterable[Any] | None,
    clip_duration: float | None = None,
) -> list[dict[str, Any]]:
    """Merge jump cuts and zoom punches into one list sorted by time.

    Jump cuts use ``cut_start``/``cut_end``. Zoom punches use ``timestamp`` and ``duration`` and
    ``scale``. Inverted or empty spans are dropped. When ``clip_duration`` is given, every span is
    clamped to ``[0, clip_duration]`` and spans that collapse to zero length are dropped.
    Sorting is by ``start``, then ``kind``, then ``end``, so equal inputs give equal output.
    """
    if clip_duration is not None and clip_duration <= 0:
        return []
    upper = float(clip_duration) if clip_duration is not None else None

    events: list[dict[str, Any]] = []
    for cut in jump_cuts or []:
        start = float(_field(cut, "cut_start", 0.0) or 0.0)
        end = float(_field(cut, "cut_end", 0.0) or 0.0)
        events.append(_event(JUMP_CUT, start, end, 1.0))

    for punch in zoom_punch_ins or []:
        start = float(_field(punch, "timestamp", 0.0) or 0.0)
        duration = float(_field(punch, "duration", 0.0) or 0.0)
        scale = float(_field(punch, "scale", 1.0) or 1.0)
        events.append(_event(ZOOM_PUNCH, start, start + max(0.0, duration), scale))

    clipped: list[dict[str, Any]] = []
    for ev in events:
        start, end = max(0.0, ev["start"]), ev["end"]
        if upper is not None:
            start = min(start, upper)
            end = min(end, upper)
        if end <= start:
            continue
        clipped.append(_event(ev["kind"], start, end, ev["strength"]))

    clipped.sort(key=lambda e: (e["start"], e["kind"], e["end"]))
    return clipped


__all__ = ["EVENT_KEYS", "JUMP_CUT", "ZOOM_PUNCH", "build_pacing_events"]
