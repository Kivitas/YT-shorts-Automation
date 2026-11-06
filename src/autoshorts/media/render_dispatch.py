"""Render dispatch: backend adapters plus the raea -> Remotion -> FFmpeg fallback chain.

``render_with_fallback`` is the single entry point the pipeline uses to turn a
``RenderRequest`` into a finished short. It walks the backends in order, records every
unavailable/failed attempt on the ``RenderResult`` and only raises when every backend failed.

Locking model (see also ``pipeline/orchestrator.py``): the pipeline's global render slot is
always taken FIRST; the per-method slots owned by ``MethodLimiter`` are leaf locks. A thread
never holds two method slots at once (the slot is released before the next method in the chain
is tried), so lock ordering is always global -> one method and cannot deadlock.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from autoshorts.core.render_spec import (
    FALLBACK_CHAIN,
    RenderBackend,
    RenderFailedError,
    RenderMethod,
    RenderRequest,
    RenderResult,
    RenderUnavailableError,
)

logger = logging.getLogger("autoshorts.render_dispatch")

DEFAULT_MAX_PARALLEL: dict[str, int] = {"raea": 1, "remotion": 1, "ffmpeg": 2}


class RenderExhaustedError(RenderFailedError):
    """Every backend in the chain was unavailable or failed. ``attempts`` lists each one."""

    def __init__(self, attempts: list[tuple[RenderMethod, str | None]]) -> None:
        self.attempts = attempts
        detail = "; ".join(f"{m.value}: {err or 'ok'}" for m, err in attempts) or "no backend tried"
        super().__init__(f"All render methods failed ({detail})")


# --------------------------------------------------------------------------- method parsing


def coerce_method(value: str | RenderMethod | None, default: RenderMethod) -> RenderMethod:
    """Parse a method name; unknown or empty values fall back to ``default``."""
    if isinstance(value, RenderMethod):
        return value
    try:
        return RenderMethod(str(value).strip().lower())
    except ValueError:
        return default


def parse_methods(value: str | Sequence[str] | None) -> list[RenderMethod]:
    """Parse ``"raea,ffmpeg"`` or a list into concrete (non-auto) methods, de-duplicated.

    Raises ``ValueError`` on an unknown name so CLI/API input is validated loudly.
    """
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else list(value)
    out: list[RenderMethod] = []
    for raw in items:
        name = str(raw).strip().lower()
        if not name:
            continue
        try:
            method = RenderMethod(name)
        except ValueError as exc:
            valid = ", ".join(m.value for m in RenderMethod)
            raise ValueError(f"Unknown render method '{raw}'. Valid: {valid}") from exc
        if method not in out:
            out.append(method)
    return out


def assign_methods(
    strategy: str, methods: Sequence[RenderMethod], count: int
) -> list[RenderMethod]:
    """Primary method for each of ``count`` jobs.

    ``round_robin`` cycles through ``methods``; every other strategy gives each job the first
    method (``variants`` renders the rest as extra files, ``single`` has just one).
    """
    if not methods:
        return [RenderMethod.AUTO] * count
    if strategy == "round_robin":
        return [methods[i % len(methods)] for i in range(count)]
    return [methods[0]] * count


# --------------------------------------------------------------------------- concurrency


class MethodLimiter:
    """Per-method semaphores so heavy renderers cannot be oversubscribed.

    Methods without a configured limit are unlimited. Thread-safe.
    """

    def __init__(self, limits: Mapping[str, int] | None = None) -> None:
        merged = dict(DEFAULT_MAX_PARALLEL if limits is None else limits)
        known = {m.value for m in RenderMethod}
        for key in merged:
            if str(key).lower() not in known:
                logger.warning("render.max_parallel_per_method: unknown method '%s' ignored", key)
        self._sems: dict[str, threading.Semaphore] = {
            str(k).lower(): threading.Semaphore(max(1, int(v)))
            for k, v in merged.items()
            if str(k).lower() in known
        }

    @contextlib.contextmanager
    def slot(self, method: RenderMethod | str) -> Iterator[None]:
        sem = self._sems.get(RenderMethod(method).value)
        if sem is None:
            yield
            return
        with sem:
            yield


# --------------------------------------------------------------------------- backends


def _result_path(res: Any, fallback: Path) -> Path:
    return Path(res) if isinstance(res, (str, Path)) else fallback


class FfmpegBackend:
    """Plain FFmpeg composition with ASS captions (the historical default pipeline)."""

    method = RenderMethod.FFMPEG
    # Mocked compositors in tests return paths without writing files; the real compositor
    # raises on failure, so the dispatcher does not insist on the file here.
    strict_output = False

    def __init__(self, compositor: Any) -> None:
        self.compositor = compositor

    def is_available(self) -> bool:
        return self.compositor is not None

    def render(self, request: RenderRequest) -> RenderResult:
        kwargs = dict(request.extras.get("compose_kwargs") or {})
        if not kwargs:
            raise RenderFailedError("FfmpegBackend needs request.extras['compose_kwargs']")
        kwargs["output_path"] = request.output_path
        if request.cam_layout is not None:
            kwargs.setdefault("cam_layout", request.cam_layout)
        started = time.monotonic()
        try:
            res = self.compositor.compose(**kwargs)
        except RenderFailedError:
            raise
        except Exception as exc:
            raise RenderFailedError(f"FFmpeg compose failed: {exc}") from exc
        out = _result_path(res, request.output_path)
        if not out.is_file() and request.output_path.is_file():
            out = request.output_path
        return RenderResult(
            output_path=out, method_used=self.method, duration_sec=time.monotonic() - started
        )


class RemotionBackend:
    """FFmpeg base composition plus a Remotion caption overlay on top."""

    method = RenderMethod.REMOTION

    def __init__(
        self,
        compositor: Any,
        *,
        project_dir: Path | None = None,
        ffmpeg_path: str | Callable[[], str] | None = None,
    ) -> None:
        from autoshorts.media import remotion_overlay as ro

        self.compositor = compositor
        self.project_dir = project_dir or ro.REMOTION_PROJECT_DIR
        self._ffmpeg_path = ffmpeg_path

    def is_available(self) -> bool:
        from autoshorts.media.remotion_overlay import is_remotion_ready

        return self.compositor is not None and is_remotion_ready(self.project_dir)

    def _ffmpeg(self) -> str:
        if callable(self._ffmpeg_path):
            return self._ffmpeg_path()
        if self._ffmpeg_path:
            return self._ffmpeg_path
        from autoshorts.media.encoder_probe import get_ffmpeg_path

        return get_ffmpeg_path()

    def render(self, request: RenderRequest) -> RenderResult:
        from autoshorts.media.remotion_overlay import (
            RemotionRenderError,
            RemotionUnavailableError,
            overlay_captions,
            render_caption_overlay,
        )

        plan = request.caption_track
        if plan is None or not Path(plan).is_file():
            raise RenderUnavailableError("no Remotion caption plan for this job")
        if not self.is_available():
            raise RenderUnavailableError(
                f"Remotion project is not installed (run `npm install` in {self.project_dir})"
            )
        kwargs = dict(request.extras.get("compose_kwargs") or {})
        if not kwargs:
            raise RenderFailedError("RemotionBackend needs request.extras['compose_kwargs']")
        # When the ASS still contains word captions, burn a words-free ASS instead so the
        # Remotion track is the only word-caption layer.
        hook_ass = request.extras.get("hook_only_ass")
        if hook_ass and Path(hook_ass).is_file():
            kwargs["subtitles_path"] = Path(hook_ass)
        kwargs["output_path"] = request.output_path
        if request.cam_layout is not None:
            kwargs.setdefault("cam_layout", request.cam_layout)
        started = time.monotonic()
        try:
            # The caption track is rendered BEFORE the base composition: it only needs the
            # plan, so when Remotion fails nothing expensive has run yet and the fallback
            # method does not repeat a full composition.
            track = Path(plan).with_suffix(".webm")
            render_caption_overlay(Path(plan), track, project_dir=self.project_dir)
            res = self.compositor.compose(**kwargs)
            base = _result_path(res, request.output_path)
            if not base.is_file() and request.output_path.is_file():
                base = request.output_path
            captioned = base.with_name(f"{base.stem}_captioned{base.suffix}")
            overlay_captions(base, track, captioned, ffmpeg_path=self._ffmpeg())
        except RemotionUnavailableError as exc:
            raise RenderUnavailableError(str(exc)) from exc
        except (RemotionRenderError, RenderFailedError) as exc:
            raise RenderFailedError(str(exc)) from exc
        except Exception as exc:
            raise RenderFailedError(f"Remotion render failed: {exc}") from exc
        return RenderResult(
            output_path=captioned,
            method_used=self.method,
            duration_sec=time.monotonic() - started,
        )


def load_raea_backend(settings: Any = None) -> RenderBackend | None:
    """Build the raea backend from ``settings.raea``; ``None`` when its module is unusable."""
    try:
        from autoshorts.media.raea_renderer import RaeaBackend
    except ImportError:
        return None
    try:
        return RaeaBackend(getattr(settings, "raea", None))
    except Exception as exc:
        logger.warning("raea backend could not be constructed: %s", exc)
        return None


def build_default_backends(
    compositor: Any, settings: Any = None
) -> dict[RenderMethod, RenderBackend]:
    """The backends available to the pipeline, keyed by method (raea only if importable)."""
    backends: dict[RenderMethod, RenderBackend] = {
        RenderMethod.FFMPEG: FfmpegBackend(compositor),
        RenderMethod.REMOTION: RemotionBackend(compositor),
    }
    raea = load_raea_backend(settings)
    if raea is not None:
        backends[RenderMethod.RAEA] = raea
    return backends


# --------------------------------------------------------------------------- dispatcher


def ordered_chain(
    method: RenderMethod, chain: Sequence[RenderMethod] = FALLBACK_CHAIN, fallback: bool = True
) -> list[RenderMethod]:
    """Methods to try, in order. AUTO = whole chain; explicit = that method then the rest."""
    chain = [m for m in chain if m != RenderMethod.AUTO]
    if method == RenderMethod.AUTO:
        return list(chain)
    if not fallback:
        return [method]
    return [method, *[m for m in chain if m != method]]


def _output_ok(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _captioned_name(path: Path) -> Path:
    return path.with_name(f"{path.stem}_captioned{path.suffix}")


def _cleanup_partial(request: RenderRequest, existed_before: bool, captioned_existed: bool) -> None:
    """Delete only files THIS attempt created; anything that pre-existed is left alone."""
    out = request.output_path
    targets: list[Path] = []
    if not captioned_existed:
        targets.append(_captioned_name(out))
    if not existed_before:
        targets.append(out)
    for target in targets:
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)


def _cleanup_attempt(
    request: RenderRequest, tmp: Path | None, existed_before: bool, captioned_existed: bool
) -> None:
    if tmp is not None:  # request.output_path is the temp file: always ours to delete
        _cleanup_partial(request, False, False)
    else:
        _cleanup_partial(request, existed_before, captioned_existed)


def _promote_temp(result: RenderResult, tmp: Path, final: Path) -> None:
    """Atomically move a temp render over ``final`` (and its ``_captioned`` sibling)."""
    produced = Path(result.output_path)
    if produced == tmp:
        os.replace(tmp, final)
        result.output_path = final
    elif produced == _captioned_name(tmp):
        target = _captioned_name(final)
        os.replace(produced, target)
        result.output_path = target
        if tmp.is_file():  # keep the un-captioned base current too (previews are cut from it)
            os.replace(tmp, final)


def render_with_fallback(
    request: RenderRequest,
    method: RenderMethod | str,
    backends: Mapping[RenderMethod, RenderBackend],
    chain: Sequence[RenderMethod] = FALLBACK_CHAIN,
    *,
    fallback: bool = True,
    limiter: MethodLimiter | None = None,
) -> RenderResult:
    """Render ``request`` with ``method``, falling through the chain on any failure.

    Raises ``RenderExhaustedError`` (a ``RenderFailedError``) carrying every attempt when no
    backend produced a valid output.
    """
    method = coerce_method(method, RenderMethod.AUTO)
    attempts: list[tuple[RenderMethod, str | None]] = []
    started = time.monotonic()
    for candidate in ordered_chain(method, chain, fallback):
        backend = backends.get(candidate)
        if backend is None:
            attempts.append((candidate, "unavailable: backend not installed"))
            logger.info("Render %s: %s backend not installed", request.job_id, candidate.value)
            continue
        try:
            available = bool(backend.is_available())
        except Exception as exc:
            available = False
            logger.warning(
                "Render %s: %s availability check failed: %s", request.job_id, candidate.value, exc
            )
        if not available:
            attempts.append((candidate, "unavailable: backend not ready on this machine"))
            logger.info("Render %s: %s unavailable, trying next", request.job_id, candidate.value)
            continue
        out = request.output_path
        existed_before = out.exists()
        captioned_existed = _captioned_name(out).exists()
        # A render that replaces an existing master is written next to it and swapped in only
        # on success, so a crash mid-render can never leave the previous output corrupted.
        tmp = out.with_name(f"{out.stem}.rendering{out.suffix}") if existed_before else None
        attempt_request = dataclasses.replace(request, output_path=tmp) if tmp else request
        try:
            slot = limiter.slot(candidate) if limiter else contextlib.nullcontext()
            with slot:
                result = backend.render(attempt_request)
            if getattr(backend, "strict_output", True) and not _output_ok(Path(result.output_path)):
                raise RenderFailedError(f"{candidate.value} produced no valid output file")
            if tmp is not None:
                _promote_temp(result, tmp, out)
        except RenderUnavailableError as exc:
            attempts.append((candidate, f"unavailable: {exc}"))
            logger.info("Render %s: %s unavailable: %s", request.job_id, candidate.value, exc)
            _cleanup_attempt(attempt_request, tmp, existed_before, captioned_existed)
            continue
        except Exception as exc:  # every failure falls through to the next method
            attempts.append((candidate, f"failed: {exc}"))
            logger.warning("Render %s: %s failed: %s", request.job_id, candidate.value, exc)
            _cleanup_attempt(attempt_request, tmp, existed_before, captioned_existed)
            continue
        attempts.append((candidate, None))
        result.attempts = attempts
        result.method_used = getattr(backend, "method", candidate)
        if not result.duration_sec:
            result.duration_sec = time.monotonic() - started
        return result
    raise RenderExhaustedError(attempts)
