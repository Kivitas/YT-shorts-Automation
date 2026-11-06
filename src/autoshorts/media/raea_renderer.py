"""raea render backend: the primary renderer for finished 9:16 shorts.

raea (https://github.com/shinyobjectz/raea, Rust + Bevy) renders React/TypeScript
compositions from a CLI. This module turns a ``RenderRequest`` into a props dict, bakes it
into a per-job copy of ``raea/short.tsx``, runs ``raea render`` and validates the output.

CLI used (verified against the raea repo, see raea/README.md):

    raea render JOB.tsx -o OUT.mp4 --composition short --w 1080 --h 1920 --fps 30 [extra_args]
    raea check OUT.mp4 --shape 9:16 --platform shorts -o OUT.check.json   (optional)

Props are embedded into the generated composition file as a constant instead of using
``--input``, because ``--input`` is documented only as the input-timeline file for games.

Failures raise ``RenderUnavailableError`` (no binary / disabled) or ``RenderFailedError``
so the dispatcher can fall back to Remotion and then FFmpeg.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from autoshorts.core.config import RaeaConfig
from autoshorts.core.render_spec import (
    CAM_GAMEPLAY_LAYOUT,
    CamLayoutSpec,
    NormRect,
    RenderFailedError,
    RenderMethod,
    RenderRequest,
    RenderResult,
    RenderUnavailableError,
)
from autoshorts.media.downloader import probe_video_stream_info, run_command_with_cleanup

logger = logging.getLogger(__name__)

try:  # chyron colours are shared with the FFmpeg/ASS chyron so both renderers match
    from autoshorts.media.chyron_overlay import CHYRON_STYLES
except Exception:  # pragma: no cover - defensive, chyron module is optional here
    CHYRON_STYLES = {}

RAEA_DIR = Path(__file__).resolve().parents[3] / "raea"
TEMPLATE_PATH = RAEA_DIR / "short.tsx"
COMPOSITION_ID = "short"
ENV_BINARY = "AUTOSHORTS_RAEA_BIN"
MIN_TIMEOUT_SEC = 60.0
VERSION_PROBE_TIMEOUT_SEC = 10.0
CHECK_TIMEOUT_SEC = 120.0
DEFAULT_DURATION_SEC = 30.0
DURATION_TOLERANCE_SEC = 1.5
PROPS_CONST = "__SHORTS_PROPS__"
MEDIA_MODES = ("static", "path")

# Caption looks per project style preset (see core/config.py SubtitlePreset).
CAPTION_PRESETS: dict[str, dict[str, Any]] = {
    "kurz": {"fontSize": 60, "wordsPerPage": 3, "uppercase": True, "stroke": 6},
    "neon": {
        "fontSize": 76,
        "wordsPerPage": 3,
        "uppercase": True,
        "activeColor": "#39FF14",
        "glow": "#39FF14",
        "stroke": 5,
    },
    "minimal": {
        "fontSize": 64,
        "wordsPerPage": 4,
        "uppercase": False,
        "activeColor": "#FFFFFF",
        "stroke": 3,
    },
    "pill": {
        "fontSize": 66,
        "wordsPerPage": 3,
        "uppercase": False,
        "pill": "rgba(0,0,0,0.72)",
        "stroke": 0,
    },
    "hormozi": {
        "fontSize": 84,
        "wordsPerPage": 2,
        "uppercase": True,
        "activeColor": "#FFE600",
        "stroke": 8,
    },
    "mrbeast": {
        "fontSize": 88,
        "wordsPerPage": 2,
        "uppercase": True,
        "activeColor": "#FFD400",
        "stroke": 10,
    },
    "cyberpunk": {
        "fontSize": 72,
        "wordsPerPage": 3,
        "uppercase": True,
        "activeColor": "#00F0FF",
        "glow": "#FF2BD6",
        "stroke": 5,
    },
    "cinematic": {
        "fontSize": 60,
        "wordsPerPage": 4,
        "uppercase": False,
        "activeColor": "#FFFFFF",
        "stroke": 2,
    },
    "rapid": {"fontSize": 84, "wordsPerPage": 2, "uppercase": True, "stroke": 8},
}
DEFAULT_CAPTION_PRESET = "kurz"

# Layout split ratios for the two-panel layouts: top panel share of the canvas height.
_SPLIT_RATIOS = {"split_50_50": 0.5, "split_70_30": 0.7}
SUPPORTED_LAYOUTS = (
    "split_50_50",
    "split_70_30",
    "full_blur_bg",
    "full_center_crop",
    "pip_gameplay",
    CAM_GAMEPLAY_LAYOUT,
)


# --------------------------------------------------------------------------------------
# Binary discovery
# --------------------------------------------------------------------------------------


def _binary_candidates(configured: str | None) -> list[Path]:
    out: list[Path] = []
    if configured:
        out.append(Path(configured).expanduser())
    env = os.environ.get(ENV_BINARY)
    if env:
        out.append(Path(env).expanduser())
    return out


def find_raea_binary(configured: str | None = None) -> Path | None:
    """Locate the raea executable: config path, $AUTOSHORTS_RAEA_BIN, PATH, ~/.raea/bin."""
    for cand in _binary_candidates(configured):
        if cand.is_file():
            return cand
        resolved = shutil.which(str(cand))
        if resolved:
            return Path(resolved)
    on_path = shutil.which("raea")
    if on_path:
        return Path(on_path)
    home_bin = Path.home() / ".raea" / "bin"
    for name in ("raea.exe", "raea"):
        cand = home_bin / name
        if cand.is_file():
            return cand
    return None


# --------------------------------------------------------------------------------------
# Request -> props
# --------------------------------------------------------------------------------------


def _css_color(spec: str | None, default: str) -> str:
    """Convert chyron colours like '0xDC2626@0.98' / 'white@1.0' to CSS."""
    if not spec:
        return default
    text = str(spec)
    color, _, alpha = text.partition("@")
    a = 1.0
    try:
        a = float(alpha) if alpha else 1.0
    except ValueError:
        a = 1.0
    if color.lower().startswith("0x") and len(color) == 8:
        r, g, b = (int(color[i : i + 2], 16) for i in (2, 4, 6))
        return f"rgba({r},{g},{b},{a:g})" if a < 1.0 else f"#{color[2:].upper()}"
    return color if a >= 1.0 else default


def _norm_rect(rect: NormRect | list[float] | None) -> NormRect | None:
    if not rect or len(rect) != 4:
        return None
    x, y, w, h = (float(v) for v in rect)
    x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
    w, h = min(max(w, 0.01), 1.0 - x), min(max(h, 0.01), 1.0 - y)
    return (x, y, w, h)


def place_media(
    media_w: int,
    media_h: int,
    box_w: float,
    box_h: float,
    crop: NormRect | None = None,
    fit: str = "cover",
) -> dict[str, float]:
    """Position/size a media element (px, relative to its panel) so ``crop`` fills the box.

    ``cover`` scales until the crop rect covers the whole box (centred on the crop);
    ``contain`` scales until the crop rect fits entirely inside it (centred).
    """
    mw, mh = max(int(media_w), 1), max(int(media_h), 1)
    cx, cy, cw, ch = crop or (0.0, 0.0, 1.0, 1.0)
    crop_w, crop_h = cw * mw, ch * mh
    sx, sy = box_w / crop_w, box_h / crop_h
    scale = max(sx, sy) if fit == "cover" else min(sx, sy)
    ctr_x, ctr_y = (cx + cw / 2) * mw * scale, (cy + ch / 2) * mh * scale
    return {
        "left": round(box_w / 2 - ctr_x, 2),
        "top": round(box_h / 2 - ctr_y, 2),
        "width": round(mw * scale, 2),
        "height": round(mh * scale, 2),
    }


def _panel(
    media: str,
    box: tuple[float, float, float, float],
    dims: tuple[int, int],
    *,
    crop: NormRect | None = None,
    fit: str = "cover",
    blur: float = 0.0,
    dim: float = 0.0,
    mirror: bool = False,
    radius: float = 0.0,
    border: str | None = None,
    audio: bool = False,
) -> dict[str, Any]:
    x, y, w, h = box
    return {
        "media": media,
        "box": {"x": round(x), "y": round(y), "w": round(w), "h": round(h)},
        "video": place_media(dims[0], dims[1], w, h, crop, fit),
        "blur": blur,
        "dim": dim,
        "mirror": mirror,
        "radius": radius,
        "border": border,
        "audio": audio,
    }


def build_panels(
    request: RenderRequest,
    media_dims: dict[str, tuple[int, int]],
) -> tuple[list[dict[str, Any]], int | None]:
    """Compute the video panels (canvas px) for ``request.layout_mode``; also the seam y."""
    width, height = request.width, request.height
    mode = (request.layout_mode or "split_50_50").strip().lower()
    src_dims = media_dims["source"]
    bg_media = "background" if "background" in media_dims else "source"
    bg_dims = media_dims.get(bg_media, src_dims)
    bg_blur = 0.0 if bg_media == "background" else 30.0

    if mode in _SPLIT_RATIOS:
        top_h = round(height * _SPLIT_RATIOS[mode])
        return [
            _panel("source", (0, 0, width, top_h), src_dims, audio=True),
            _panel(bg_media, (0, top_h, width, height - top_h), bg_dims, blur=bg_blur),
        ], top_h
    if mode == "full_blur_bg":
        fg_h = width * src_dims[1] / max(src_dims[0], 1)
        return [
            _panel("source", (0, 0, width, height), src_dims, blur=40.0, dim=0.45),
            _panel(
                "source",
                (0, (height - fg_h) / 2, width, fg_h),
                src_dims,
                fit="contain",
                audio=True,
            ),
        ], None
    if mode == "full_center_crop":
        return [_panel("source", (0, 0, width, height), src_dims, audio=True)], None
    if mode == "pip_gameplay":
        pw, ph = round(width * 0.4), round(width * 0.4 * 16 / 9)
        return [
            _panel("source", (0, 0, width, height), src_dims, audio=True),
            _panel(
                bg_media,
                (width - pw - 36, height - ph - 240, pw, ph),
                bg_dims,
                blur=bg_blur,
                radius=28,
                border="#FFFFFF",
            ),
        ], None
    if mode == CAM_GAMEPLAY_LAYOUT:
        spec = request.cam_layout or CamLayoutSpec()
        ratio = min(max(spec.top_ratio, 0.2), 0.8)
        top_h = round(height * ratio)
        if spec.cam_source == "file" and "cam" in media_dims:
            cam_panel = _panel(
                "cam", (0, 0, width, top_h), media_dims["cam"], mirror=spec.mirror_cam
            )
        else:
            cam_panel = _panel(
                "source",
                (0, 0, width, top_h),
                src_dims,
                crop=_norm_rect(spec.cam_rect),
                mirror=spec.mirror_cam,
            )
        game_panel = _panel(
            "source",
            (0, top_h, width, height - top_h),
            src_dims,
            crop=_norm_rect(spec.gameplay_rect),
            audio=True,
        )
        return [cam_panel, game_panel], top_h
    raise RenderFailedError(f"raea: unsupported layout_mode {request.layout_mode!r}")


def _keep_segments(
    edl: dict[str, Any] | None, start: float, duration: float
) -> list[dict[str, float]]:
    """Source-time segments to keep (jump cuts removed), each with its output start."""
    end = start + duration
    cuts: list[tuple[float, float]] = []
    for c in (edl or {}).get("jump_cuts") or []:
        try:
            a, b = float(c["cut_start"]), float(c["cut_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b > a >= 0:
            cuts.append((a, b))
    segments: list[dict[str, float]] = []
    cursor, out_t = start, 0.0
    for a, b in sorted(cuts):
        a, b = max(a, start), min(b, end)
        if b <= a:
            continue
        if a > cursor:
            segments.append({"start": cursor, "end": a, "outStart": out_t})
            out_t += a - cursor
        cursor = max(cursor, b)
    if cursor < end:
        segments.append({"start": cursor, "end": end, "outStart": out_t})
    return segments or [{"start": start, "end": end, "outStart": 0.0}]


def _load_caption_plan(path: Path | None) -> dict[str, Any]:
    if not path or not Path(path).is_file():
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("raea: unreadable caption track %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _caption_props(request: RenderRequest, plan: dict[str, Any]) -> dict[str, Any] | None:
    words = [
        {"text": str(w.get("text", "")), "start": float(w["start"]), "end": float(w["end"])}
        for w in (plan.get("words") or [])
        if isinstance(w, dict) and w.get("text") and "start" in w and "end" in w
    ]
    if not words:
        return None
    preset_name = str(
        request.branding.get("style_preset") or request.extras.get("style_preset") or ""
    ).lower()
    preset = dict(CAPTION_PRESETS.get(preset_name, CAPTION_PRESETS[DEFAULT_CAPTION_PRESET]))
    # Values written into the caption plan JSON (rapid=2 words etc.) win over preset defaults.
    for src_key, dst_key in (
        ("wordsPerPage", "wordsPerPage"),
        ("fontSize", "fontSize"),
        ("activeColor", "activeColor"),
        ("primaryColor", "primaryColor"),
    ):
        if plan.get(src_key):
            preset[dst_key] = plan[src_key]
    preset.setdefault("activeColor", "#FFE600")
    preset.setdefault("primaryColor", "#FFFFFF")
    preset.setdefault("glow", None)
    preset.setdefault("pill", None)
    return {
        "words": words,
        "preset": preset_name or DEFAULT_CAPTION_PRESET,
        "bottomPercent": float(plan.get("bottomPercent", 38)),
        **preset,
    }


def _chyron_props(request: RenderRequest, hook_text: str | None) -> dict[str, Any] | None:
    b, x = request.branding, request.extras
    if not (b.get("enable_chyron") or x.get("enable_chyron")):
        return None
    text = x.get("chyron_text") or b.get("chyron_text") or hook_text
    if not text:
        return None
    style_name = str(x.get("chyron_style") or b.get("chyron_style") or "broadcast_red")
    style = CHYRON_STYLES.get(style_name) or CHYRON_STYLES.get("broadcast_red") or {}
    return {
        "badge": str(x.get("chyron_badge") or b.get("chyron_badge") or "BREAKING NEWS"),
        "text": str(text),
        "style": style_name,
        "badgeBg": _css_color(style.get("badge_bg"), "#DC2626"),
        "badgeFg": _css_color(style.get("badge_fg"), "#FFFFFF"),
        "bannerBg": _css_color(style.get("banner_bg"), "#FFFFFF"),
        "bannerFg": _css_color(style.get("banner_fg"), "#111827"),
        "accent": _css_color(style.get("accent"), "#DC2626"),
        "startSec": float(x.get("chyron_start_sec", 0.0)),
        "durationSec": x.get("chyron_duration_sec"),
        "position": str(x.get("chyron_position") or b.get("chyron_position") or "seam"),
    }


def _progress_props(request: RenderRequest, seam_y: int | None) -> dict[str, Any] | None:
    b = request.branding
    pos = str(b.get("progress_bar_position", "seam")).lower()
    if pos == "none":
        return None
    h = int(b.get("progress_bar_height", 12))
    if pos == "bottom":
        y = request.height - h
    elif pos == "top":
        y = 0
    else:
        y = (seam_y if seam_y is not None else request.height // 2) - h // 2
    return {"color": str(b.get("progress_bar_color", "#FFE600")), "height": h, "y": y}


def stage_name(role: str, path: Path) -> str:
    """Name a media file takes inside the job's ``media/`` folder (what staticFile() sees)."""
    suffix = path.suffix.lower() or ".mp4"
    return f"{role}{suffix}"


def build_props(
    request: RenderRequest,
    *,
    probe: Callable[[Path], dict[str, Any]] | None = None,
    media_mode: str = "static",
) -> dict[str, Any]:
    """Convert a RenderRequest into the JSON props consumed by raea/short.tsx.

    Pure apart from the ffprobe calls (injectable through ``probe``).
    """
    probe = probe or probe_video_stream_info
    if media_mode not in MEDIA_MODES:
        raise ValueError(f"media_mode must be one of {MEDIA_MODES}")
    if not request.source_video or not Path(request.source_video).is_file():
        raise RenderFailedError(f"raea: source video missing: {request.source_video}")
    mode = (request.layout_mode or "split_50_50").strip().lower()
    if mode not in SUPPORTED_LAYOUTS:
        raise RenderFailedError(f"raea: unsupported layout_mode {request.layout_mode!r}")

    files: dict[str, Path] = {"source": Path(request.source_video)}
    if request.background_video and Path(request.background_video).is_file():
        files["background"] = Path(request.background_video)
    spec = request.cam_layout
    if mode == CAM_GAMEPLAY_LAYOUT and spec and spec.cam_source == "file":
        if not spec.cam_file or not Path(spec.cam_file).is_file():
            raise RenderFailedError(f"raea: cam_file missing: {spec.cam_file}")
        files["cam"] = Path(spec.cam_file)

    infos: dict[str, dict[str, Any]] = {k: probe(p) for k, p in files.items()}
    media_dims: dict[str, tuple[int, int]] = {}
    for key, info in infos.items():
        w, h = int(info.get("width") or 0), int(info.get("height") or 0)
        if w <= 0 or h <= 0:
            if key == "source":
                raise RenderFailedError(f"raea: cannot read source video size: {files[key]}")
            w, h = 1920, 1080
        media_dims[key] = (w, h)

    plan = _load_caption_plan(request.caption_track)
    start = float(request.extras.get("start_sec", 0.0) or 0.0)
    duration = float(
        request.extras.get("duration_sec")
        or plan.get("durationSec")
        or max(float(infos["source"].get("duration") or 0.0) - start, 0.0)
        or DEFAULT_DURATION_SEC
    )
    duration = max(duration, 0.5)
    fps = max(int(request.fps), 1)

    panels, seam_y = build_panels(request, media_dims)
    hook_text = request.extras.get("hook_text") or request.branding.get("hook_text")
    watermark = request.branding.get("watermark_text")

    media = {
        key: {
            "path": str(path.resolve()).replace("\\", "/"),
            "static": stage_name(key, path),
            "width": media_dims[key][0],
            "height": media_dims[key][1],
            "hasAudio": bool(infos[key].get("has_audio")),
            "durationSec": round(float(infos[key].get("duration") or 0.0), 3),
        }
        for key, path in files.items()
    }
    return {
        "version": 1,
        "jobId": request.job_id,
        "width": request.width,
        "height": request.height,
        "fps": fps,
        "durationSec": round(duration, 3),
        "durationInFrames": max(1, round(duration * fps)),
        "layout": mode,
        "mediaMode": media_mode,
        "media": media,
        "segments": _keep_segments(request.edl, start, duration),
        "panels": panels,
        "seamY": seam_y,
        "captions": _caption_props(request, plan),
        "hook": {
            "text": str(hook_text),
            "durationSec": float(request.extras.get("hook_duration_sec", 3.0)),
        }
        if hook_text
        else None,
        "progressBar": _progress_props(request, seam_y),
        "chyron": _chyron_props(request, str(hook_text) if hook_text else None),
        "watermark": {
            "text": str(watermark),
            "position": str(request.branding.get("watermark_position", "top_right")),
            "opacity": float(request.branding.get("watermark_opacity", 0.65)),
        }
        if watermark
        else None,
    }


def write_job_composition(
    props: dict[str, Any], dest: Path, template: Path = TEMPLATE_PATH
) -> Path:
    """Write ``dest`` = props constant + the raea/short.tsx template."""
    if not template.is_file():
        raise RenderUnavailableError(f"raea composition template missing: {template}")
    header = f"const {PROPS_CONST} = {json.dumps(props, ensure_ascii=False)};\n"
    dest.write_text(header + template.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


# --------------------------------------------------------------------------------------
# Backend
# --------------------------------------------------------------------------------------


class RaeaBackend:
    """RenderBackend that renders through the raea CLI."""

    method = RenderMethod.RAEA

    def __init__(self, config: RaeaConfig | None = None) -> None:
        self.config = config or RaeaConfig()
        self._version: str | None = None
        self._version_probed = False
        self.last_diagnostics: dict[str, Any] = {}

    # -- availability -------------------------------------------------------------
    def binary(self) -> Path | None:
        if not self.config.enabled:
            return None
        return find_raea_binary(self.config.binary)

    def is_available(self) -> bool:
        """True when raea is enabled and its binary exists (``--version`` probed once)."""
        binary = self.binary()
        if binary is None:
            return False
        if not self._version_probed:
            self._version_probed = True
            try:
                proc = run_command_with_cleanup(
                    [str(binary), "--version"], timeout=VERSION_PROBE_TIMEOUT_SEC
                )
                if proc.returncode == 0:
                    self._version = (proc.stdout or "").strip() or None
                else:
                    logger.warning("raea --version exited %s; treating as present", proc.returncode)
            except Exception as exc:
                logger.warning("raea --version probe failed (%s); treating as present", exc)
        return True

    @property
    def version(self) -> str | None:
        return self._version

    # -- rendering ----------------------------------------------------------------
    def render(self, request: RenderRequest) -> RenderResult:
        started = time.monotonic()
        binary = self.binary()
        if binary is None:
            why = "disabled in config" if not self.config.enabled else "binary not found"
            raise RenderUnavailableError(f"raea unavailable: {why}")

        media_mode = str(request.extras.get("raea_media_mode", "static"))
        props = build_props(request, media_mode=media_mode)
        out = Path(request.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = out.with_name(f".{out.stem}.raea-{os.getpid()}.tmp{out.suffix or '.mp4'}")
        timeout = max(MIN_TIMEOUT_SEC, props["durationSec"] * self.config.timeout_sec_per_clip_sec)

        work = Path(tempfile.mkdtemp(prefix=f"raea_{request.job_id}_"))
        try:
            for info in props["media"].values():
                _link_or_copy(Path(info["path"]), work / "media" / info["static"])
            comp = write_job_composition(props, work / "job.tsx")
            (work / "props.json").write_text(
                json.dumps(props, ensure_ascii=False), encoding="utf-8"
            )
            cmd = [
                str(binary),
                "render",
                str(comp),
                "-o",
                str(tmp_out),
                "--composition",
                COMPOSITION_ID,
                "--w",
                str(props["width"]),
                "--h",
                str(props["height"]),
                "--fps",
                str(props["fps"]),
                *self.config.extra_args,
            ]
            self._run_render(cmd, work, timeout)
            info = self._validate_output(tmp_out, props)
            os.replace(tmp_out, out)
            if self.config.run_check:
                self.last_diagnostics = self._run_check(binary, out, work)
                request.extras["raea_check"] = self.last_diagnostics
            request.extras["raea_version"] = self._version
        except (RenderFailedError, RenderUnavailableError):
            raise
        except Exception as exc:  # unexpected IO errors etc. must still trigger fallback
            raise RenderFailedError(f"raea render crashed: {exc}") from exc
        finally:
            tmp_out.unlink(missing_ok=True)
            shutil.rmtree(work, ignore_errors=True)

        return RenderResult(
            output_path=out,
            method_used=RenderMethod.RAEA,
            attempts=[(RenderMethod.RAEA, None)],
            duration_sec=float(info.get("duration") or time.monotonic() - started),
        )

    def _run_render(self, cmd: list[str], work: Path, timeout: float) -> None:
        env = {**os.environ, "RAEA_OFFLINE": os.environ.get("RAEA_OFFLINE", "1")}
        try:
            proc = run_command_with_cleanup(cmd, timeout=timeout, cwd=str(work), env=env)
        except subprocess.TimeoutExpired as exc:
            raise RenderFailedError(f"raea render timed out after {timeout:.0f}s") from exc
        except FileNotFoundError as exc:
            raise RenderUnavailableError(f"raea binary not executable: {exc}") from exc
        except OSError as exc:
            raise RenderUnavailableError(f"raea could not be started: {exc}") from exc
        if proc.returncode != 0:
            tail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()[-1500:]
            raise RenderFailedError(f"raea render exited {proc.returncode}: {tail}")

    def _validate_output(self, path: Path, props: dict[str, Any]) -> dict[str, Any]:
        if not path.is_file() or path.stat().st_size == 0:
            raise RenderFailedError("raea produced no output file")
        info = probe_video_stream_info(path)
        if not info.get("has_video"):
            raise RenderFailedError("raea output has no video stream")
        got = (int(info.get("width") or 0), int(info.get("height") or 0))
        want = (int(props["width"]), int(props["height"]))
        if got != want:
            raise RenderFailedError(
                f"raea output is {got[0]}x{got[1]}, expected {want[0]}x{want[1]}"
            )
        if not info.get("has_audio"):
            raise RenderFailedError("raea output has no audio stream")
        dur = float(info.get("duration") or 0.0)
        if abs(dur - props["durationSec"]) > max(
            DURATION_TOLERANCE_SEC, 0.1 * props["durationSec"]
        ):
            raise RenderFailedError(
                f"raea output duration {dur:.2f}s differs from expected {props['durationSec']:.2f}s"
            )
        return info

    def _run_check(self, binary: Path, video: Path, work: Path) -> dict[str, Any]:
        """Run ``raea check`` (exit 1 means P0/P1 findings); never raises."""
        report = work / "check.json"
        cmd = [
            str(binary),
            "check",
            str(video),
            "--shape",
            "9:16",
            "--platform",
            "shorts",
            "-o",
            str(report),
        ]
        diag: dict[str, Any] = {"ran": True}
        try:
            proc = run_command_with_cleanup(cmd, timeout=CHECK_TIMEOUT_SEC, cwd=str(work))
            diag["exit_code"] = proc.returncode
            diag["passed"] = proc.returncode == 0
            diag["output"] = ((proc.stdout or "") + (proc.stderr or "")).strip()[-1500:]
            if report.is_file():
                try:
                    diag["report"] = json.loads(report.read_text(encoding="utf-8"))
                except ValueError:
                    pass
        except Exception as exc:
            diag.update({"ran": False, "error": str(exc)})
        if diag.get("passed") is False:
            logger.warning("raea check reported findings for %s", video.name)
        return diag
