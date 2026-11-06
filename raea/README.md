# raea renderer

`raea/short.tsx` is the 9:16 short composition rendered by
[raea](https://github.com/shinyobjectz/raea) (Rust + Bevy, FSL-1.1 license). It is the
primary renderer; the pipeline falls back to Remotion, then FFmpeg, when raea is missing or fails.

## How raea was installed here (verified)

On this Windows machine raea was installed from the release zip: downloaded, sha256-verified,
and extracted to `~/.raea` (binary `~/.raea/bin/raea.exe`). The `curl | iex` installer was NOT
run, PATH and `~/.claude` were not touched. Other options (review before running):

- macOS/Linux: `curl -fsSL https://raea.io/install | sh` (installs under `~/.raea`)
- Windows 10/11 (VC++ Redistributable and a DirectX 12 or Vulkan GPU needed):
  `irm https://raea.io/install.ps1 | iex`
- From source: Rust stable + bun, then `cargo build --release -p raea-cli --no-default-features`

## How the backend finds it

`RaeaBackend` (`src/autoshorts/media/raea_renderer.py`) looks, in order, at `raea.binary` in
`config.yaml`, the `AUTOSHORTS_RAEA_BIN` env var, `raea` on `PATH`, then `~/.raea/bin`.
`is_available()` is a cheap cached "binary exists" check. Set `raea.enabled: false` to skip it.

## Facts verified against the real binary

- **No `--version` flag** (it is refused). Nothing is probed; `raea guide` / `raea --help` work.
- **Sources with B-frames are REFUSED** ("use an edit mezzanine") and **ProRes/.mov is REFUSED**
  ("no video track"). H.264 mp4 with `-bf 0` (or all-intra `-g 1`) works.
  So the backend first builds an *edit mezzanine* for the source, the cam file and the
  background in the job work dir: `libx264 -bf 0 -g 30 -pix_fmt yuv420p`, AAC audio (source only),
  constant fps, long edge capped at 2560, trimmed to the EDL window (`start_sec`/`duration_sec`),
  cached per (path, mtime, size, window). Originals are never modified; the work dir is deleted.
  Segment times in the props are relative to the trimmed mezzanine.
- **Startup is slow**: ~15-30s even for a 2s clip, and about 0.25s per frame with two videos
  (decode bound). Timeout = `max(120, clip_sec * timeout_sec_per_clip_sec + 60)`.
- `<Video>` honours `objectFit`, `objectPosition`, `filter: blur()`, `transform: scaleX(-1)`,
  and `overflow: hidden` / `borderRadius` clipping on a parent. Without an explicit `objectFit` it
  stretched, so `short.tsx` sizes the video exactly (cover/contain/crop-rect math in Python,
  `objectFit: fill`) inside a clipping panel. `staticFile("x.mp4")` resolves in `media/` beside
  the composition.
- The props constant must not be declared twice: the job file prepends
  `const __SHORTS_PROPS__ = ...`, so `short.tsx` must not `declare` it (bundler error).
- stderr always carries harmless noise (missing `bin/assets`, WebAssetPlugin). Only the exit code
  plus output validation decide failure; on failure the `REFUSED`/error lines are put in the
  `RenderFailedError` message.
- `-o out.mp4` produces exactly `out.mp4`. The Windows build writes **Opus** audio and
  Constrained Baseline video in the mp4; the backend remuxes audio to AAC (video stream copied).
- Source audio level is preserved (mean volume identical to the source, mono becomes stereo).
- Captions stay baked into the props (our presets/pages); raea's `--words words.json` /
  `useWords()` is not used (its schema is for `raea cut` transcripts).
- `raea check` on a synthetic render reported one P1: loudness (-29 LUFS on a quiet test tone;
  real speech is normally much louder). `raea.run_check: true` records findings in
  `request.extras["raea_check"]` (informational only).

## How a job is rendered

1. `build_props(request, trim_window=True)` computes layout geometry, captions, hook, progress
   bar, chyron and watermark as JSON.
2. Mezzanines are built and hard-linked into `<workdir>/media/`; the props are written as
   `const __SHORTS_PROPS__ = {...};` on top of a copy of `short.tsx`.
3. `raea render job.tsx -o out.tmp.mp4 --composition short --w 1080 --h 1920 --fps N`
   runs with `RAEA_OFFLINE=1`; audio is remuxed to AAC, output is checked with ffprobe and
   atomically moved into place. No partial output is ever left behind.

Layouts: `split_50_50`, `split_70_30`, `full_blur_bg`, `full_center_crop`, `pip_gameplay`,
`cam_gameplay` (camera on top `top_ratio`, gameplay below). All six were rendered with the real
binary and inspected frame by frame.

## Preview a frame

```
raea render raea/short.tsx --frame 30 -o preview.png     # placeholder props
```

To preview a real job, call `write_job_composition(build_props(request), Path("job.tsx"))`
and render that file (stage a `media/` folder next to it).

## Tests

`tests/test_raea_renderer.py` fakes the binary. `tests/test_raea_integration.py` runs the real
raea (skipped when it is not installed): ~40s for a split layout from a B-frame source, a
`cam_gameplay` layout from a ProRes `.mov`, and the REFUSED error path.
