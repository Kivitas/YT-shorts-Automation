# raea renderer

`raea/short.tsx` is the 9:16 short composition rendered by
[raea](https://github.com/shinyobjectz/raea) (Rust + Bevy, FSL-1.1 license). It is the
primary renderer; the pipeline falls back to Remotion, then FFmpeg, when raea is missing or fails.

## Install raea (manual, not done by AutoShorts)

From the raea README (review before running):

- macOS/Linux: `curl -fsSL https://raea.io/install | sh` (installs under `~/.raea`)
- Windows 10/11 (needs the VC++ Redistributable and a DirectX 12 or Vulkan GPU):
  `irm https://raea.io/install.ps1 | iex`
- From source: Rust stable + bun, then `cargo build --release -p raea-cli --no-default-features`

## How the backend finds it

`RaeaBackend` (`src/autoshorts/media/raea_renderer.py`) looks, in order, at `raea.binary` in
`config.yaml`, the `AUTOSHORTS_RAEA_BIN` env var, `raea` on `PATH`, then `~/.raea/bin`.
Set `raea.enabled: false` to skip it. Config keys are documented in `config/config.example.yaml`.

## How a job is rendered

1. `build_props(request)` computes layout geometry, captions, hook, progress bar, chyron and
   watermark as JSON.
2. The props are written as `const __SHORTS_PROPS__ = {...};` on top of a copy of `short.tsx`
   in a temp work dir (raea documents no props flag; `--input` is the game-input timeline).
   Source/background/cam videos are hard-linked into `<workdir>/media/` so `staticFile()` finds
   them. Set `request.extras["raea_media_mode"] = "path"` to pass absolute paths instead.
3. `raea render job.tsx -o out.tmp.mp4 --composition short --w 1080 --h 1920 --fps N`
   runs with `RAEA_OFFLINE=1`, then the output is checked with ffprobe and atomically moved.
4. With `raea.run_check: true`, `raea check out.mp4 --shape 9:16 --platform shorts` findings
   land in `request.extras["raea_check"]` (informational only).

Layouts: `split_50_50`, `split_70_30`, `full_blur_bg`, `full_center_crop`, `pip_gameplay`,
`cam_gameplay` (camera on top `top_ratio`, gameplay below).

## Preview a frame

```
raea render raea/short.tsx --frame 30 -o preview.png     # placeholder props
raea check --quick raea/short.tsx                        # text-safety check on 3 stills
```

To preview a real job, call `write_job_composition(build_props(request), Path("job.tsx"))`
and render that file (stage `media/` next to it, or use `raea_media_mode="path"`).

## Unverified assumptions

The raea docs do not state how `/media/` URLs map to disk for a standalone file, whether
absolute paths work in `<Video src>`, or what fonts are available. If a real render shows
missing video, switch `raea_media_mode` between `static` and `path`.
