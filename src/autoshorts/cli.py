"""AutoShorts Master Command-Line Interface (CLI).

Supports commands:
  autoshorts run --links "..." --bg subway --upload --preset medium
  autoshorts dashboard --port 8765 --open-edge
  autoshorts scheduler --daemon
  autoshorts quota
  autoshorts clean --days 1 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from autoshorts.cleanup.safety_cleaner import SafetyCleaner
from autoshorts.core.config import Settings, load_config
from autoshorts.core.exceptions import AutoShortsError, InvalidURLError
from autoshorts.core.logging_setup import setup_logging
from autoshorts.core.models import JobStatus
from autoshorts.core.state_manager import StateManager
from autoshorts.media.render_dispatch import parse_methods
from autoshorts.pipeline import ShortsPipeline
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.scheduler.scheduler import SpreadScheduler
from autoshorts.uploader.local_exporter import LocalExportUploader
from autoshorts.uploader.youtube_api import YouTubeApiUploader

logger = logging.getLogger("autoshorts.cli")


LAYOUT_CHOICES = [
    "split_50_50",
    "split_70_30",
    "full_blur_bg",
    "full_center_crop",
    "pip_gameplay",
    "cam_gameplay",
]


def _positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got '{value}'") from exc
    if n < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return n


def _render_methods_arg(value: str) -> list[str]:
    try:
        methods = parse_methods(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if not methods:
        raise argparse.ArgumentTypeError("at least one render method is required")
    return [m.value for m in methods]


def build_parser() -> argparse.ArgumentParser:
    """Construct command-line argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="autoshorts",
        description="AutoShorts v3.2: URL-Driven SemIf Video Clipping Orchestrator",
    )
    parser.add_argument("--config", type=str, default=None, help="Path to custom config.yaml")
    parser.add_argument(
        "--log-level",
        type=str.upper,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # 1. run command
    run_parser = subparsers.add_parser(
        "run", help="Process one or more video links through the pipeline"
    )
    run_parser.add_argument(
        "--links",
        "-l",
        type=str,
        required=True,
        help="YouTube URL, file path, or multiline links string",
    )
    run_parser.add_argument(
        "--bg",
        "--background",
        type=str,
        default=None,
        help="Background theme (minecraft, subway, auto)",
    )
    run_parser.add_argument(
        "--preset",
        "--dopamine",
        type=str,
        default="medium",
        choices=["low", "medium", "high"],
        help="Dopamine editing preset",
    )
    run_parser.add_argument(
        "--upload", "-u", action="store_true", help="Trigger immediate upload after rendering"
    )
    run_parser.add_argument(
        "--auto-upload", action="store_true", help="Enqueue rendered shorts into scheduler"
    )
    run_parser.add_argument(
        "--instructions", "-i", type=str, default=None, help="Custom operator clipping instructions"
    )
    run_parser.add_argument(
        "--chyron",
        "--breaking-news",
        action="store_true",
        help="Add broadcast lower-third chyron overlay",
    )
    run_parser.add_argument(
        "--chyron-badge",
        type=str,
        default=None,
        help="Custom category badge for chyron (e.g. 'BREAKING NEWS', 'EXCLUSIVE')",
    )
    run_parser.add_argument(
        "--chyron-text",
        type=str,
        default=None,
        help="Custom headline text for chyron overlay",
    )

    run_parser.add_argument(
        "--render-method",
        type=str.lower,
        default=None,
        choices=["auto", "raea", "remotion", "ffmpeg"],
        help="Renderer: auto (raea -> remotion -> ffmpeg), raea, remotion, or ffmpeg",
    )
    run_parser.add_argument(
        "--render-methods",
        type=_render_methods_arg,
        default=None,
        help="Comma-separated methods for multi-method batches, e.g. raea,ffmpeg",
    )
    run_parser.add_argument(
        "--render-strategy",
        type=str.lower,
        default=None,
        choices=["single", "round_robin", "variants"],
        help="single | round_robin (one method per job) | variants (every method per clip)",
    )
    run_parser.add_argument(
        "--layout",
        type=str.lower,
        default=None,
        choices=LAYOUT_CHOICES,
        help="Output layout (cam_gameplay = streamer cam on top, gameplay below)",
    )
    run_parser.add_argument(
        "--streamer-cam",
        action="store_true",
        help=(
            "Streamer mode: facecam on the top half and the streamer's own gameplay on the "
            "bottom half (no Subway Surfers/Minecraft background). Same as --layout cam_gameplay."
        ),
    )
    run_parser.add_argument(
        "--parallel",
        type=_positive_int,
        default=None,
        help="Max shorts processed/rendered concurrently (sets pipeline concurrency)",
    )

    # 2. dashboard command
    dash_parser = subparsers.add_parser("dashboard", help="Launch the local FastAPI web dashboard")
    dash_parser.add_argument(
        "--port", "-p", type=int, default=8765, help="Port to bind dashboard server (default 8765)"
    )
    dash_parser.add_argument(
        "--host", type=str, default="127.0.0.1", help="Host interface (default 127.0.0.1)"
    )
    dash_parser.add_argument(
        "--open-edge",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Launch browser in Microsoft Edge on Windows (use --no-open-edge to disable)",
    )
    dash_parser.add_argument(
        "--no-browser", action="store_true", help="Do not automatically launch browser tab"
    )

    # 3. scheduler command
    sched_parser = subparsers.add_parser(
        "scheduler", help="Run the YouTube upload spread scheduler"
    )
    sched_parser.add_argument(
        "--daemon", "-d", action="store_true", help="Run indefinitely in background polling loop"
    )
    sched_parser.add_argument(
        "--interval", type=int, default=30, help="Poll interval in seconds for daemon mode"
    )
    sched_parser.add_argument(
        "--once", action="store_true", help="Run a single scheduler dispatch tick and exit"
    )

    # 4. quota command
    quota_parser = subparsers.add_parser(
        "quota", help="Inspect current Pacific-Time daily API quota status"
    )
    quota_parser.add_argument("--json", action="store_true", help="Output raw JSON format")

    # 5. clean command
    clean_parser = subparsers.add_parser(
        "clean", help="Safely purge intermediate work files to Recycle Bin"
    )
    clean_parser.add_argument(
        "--days", type=int, default=1, help="Purge output master MP4s older than N days (default 1)"
    )
    clean_parser.add_argument(
        "--dry-run", action="store_true", help="Simulate cleanup without deleting files"
    )
    clean_parser.add_argument(
        "--job-id", type=str, default=None, help="Purge files strictly for a specific job ID"
    )
    clean_parser.add_argument(
        "--force", action="store_true", help="Bypass upload verification safety requirement"
    )

    # 6. edge-login command
    login_parser = subparsers.add_parser(
        "edge-login", help="Launch MS Edge browser to sign into YouTube Studio"
    )
    login_parser.add_argument(
        "--timeout", type=int, default=300, help="Login timeout in seconds (default 300)"
    )

    # 7. longform command
    longform_parser = subparsers.add_parser(
        "longform", help="Render a long-form stick-figure video (1-4 minutes)"
    )
    longform_parser.add_argument(
        "--topic",
        type=str,
        default="science",
        help="Topic for the long-form video",
    )
    longform_parser.add_argument(
        "--url",
        "--link",
        type=str,
        default=None,
        help="YouTube URL to adapt into a long-form explainer video",
    )
    longform_parser.add_argument(
        "--minutes", type=float, default=2.5, help="Target length in minutes (default 2.5)"
    )
    longform_parser.add_argument(
        "--provider",
        choices=["api", "local"],
        default="api",
        help="Script and voice provider (default api)",
    )
    longform_parser.add_argument(
        "--publish",
        action="store_true",
        default=False,
        help="Upload the rendered long-form video to YouTube after rendering",
    )
    longform_parser.add_argument(
        "--aspect-ratio",
        choices=["16:9", "9:16"],
        default="16:9",
        help="Video aspect ratio: 16:9 (Landscape default) or 9:16 (YouTube Shorts)",
    )
    longform_parser.add_argument(
        "--shorts",
        action="store_true",
        default=False,
        help="Generate vertical YouTube Shorts (9:16) instead of 16:9 landscape video",
    )
    longform_parser.add_argument(
        "--segments",
        type=int,
        default=7,
        help="Number of country segments for Wojak Shorts (6-10, default 7)",
    )
    longform_parser.add_argument(
        "--subtitles",
        choices=["kinetic", "card", "both", "none"],
        default="kinetic",
        help="Subtitle style for Wojak Shorts: kinetic (rapid 2-word glow burst) or card (floating white joke box)",
    )
    longform_parser.add_argument(
        "--words-per-burst",
        type=int,
        default=2,
        help="Number of words per kinetic subtitle burst (default 2)",
    )
    longform_parser.add_argument(
        "--font-size",
        type=int,
        default=84,
        help="Font size in points for kinetic subtitles (default 84)",
    )
    longform_parser.add_argument(
        "--render-method",
        choices=["ffmpeg", "remotion"],
        default="ffmpeg",
        help="Frame rendering backend for Wojak Shorts: ffmpeg (direct pipe) or remotion (caption overlay)",
    )

    # 8. cleanup-work command
    cleanup_work_parser = subparsers.add_parser(
        "cleanup-work", help="Prune work/ directory according to retention policy"
    )
    cleanup_work_parser.add_argument(
        "--dry-run", action="store_true", help="Simulate cleanup without deleting directories"
    )

    # 9. meme and types-of commands
    def _add_meme_args(
        p: argparse.ArgumentParser,
        default_mode: str = "country_meme",
        default_cat: str = "countries",
    ) -> None:
        p.add_argument(
            "--mode",
            choices=["country_meme", "types_of"],
            default=default_mode,
            help="Meme generation mode (country_meme or types_of)",
        )
        p.add_argument(
            "--topic",
            type=str,
            default="",
            help="Topic for the meme video (e.g. 'Every Country\\'s Public Transport' or 'Types of Gamers')",
        )
        p.add_argument(
            "--category",
            choices=["countries", "school", "food", "relatable", "dark_comedy", "anime", "custom"],
            default=default_cat,
            help="Topic category (default %(default)s)",
        )
        p.add_argument(
            "--segments",
            type=int,
            default=6,
            help="Number of country/type segments (5-10, default 6)",
        )
        p.add_argument(
            "--aspect-ratio",
            choices=["9:16", "16:9"],
            default="9:16",
            help="Video aspect ratio: 9:16 (Shorts/TikTok) or 16:9 (Landscape) (default 9:16)",
        )
        p.add_argument(
            "--style",
            choices=["anime_color", "manga_bw"],
            default="anime_color",
            help="Visual style (anime_color or manga_bw, default anime_color)",
        )
        p.add_argument(
            "--voice",
            type=str,
            default="en-US-ChristopherNeural",
            help="TTS narrator voice",
        )
        p.add_argument(
            "--auto-topic",
            action="store_true",
            default=False,
            help="Automatically invent a creative topic",
        )
        p.add_argument(
            "--publish",
            action="store_true",
            default=False,
            help="Upload the rendered meme video to YouTube after rendering",
        )

    meme_parser = subparsers.add_parser(
        "meme", help="Generate Country Meme or Types Of... Meme Animation video"
    )
    _add_meme_args(meme_parser, default_mode="country_meme", default_cat="countries")

    types_parser = subparsers.add_parser(
        "types-of", help="Generate Types Of... anime comedy meme video"
    )
    _add_meme_args(types_parser, default_mode="types_of", default_cat="relatable")

    # 10. wojak command (SapX-style 16:9 long-form meme video)
    wojak_parser = subparsers.add_parser(
        "wojak", help="Generate SapX-style 16:9 long-form Wojak meme video"
    )
    wojak_parser.add_argument(
        "--aspect-ratio",
        type=str,
        default="16:9",
        choices=["16:9", "9:16"],
        help="Aspect ratio (16:9 for landscape longform, 9:16 for vertical YouTube Shorts)",
    )
    wojak_parser.add_argument(
        "--shorts",
        action="store_true",
        default=False,
        help="Generate a vertical (9:16) Wojak YouTube Shorts video",
    )
    wojak_parser.add_argument(
        "--topic",
        type=str,
        default="hospital",
        help="Topic for the Wojak video (hospital, school, transport, or custom)",
    )
    wojak_parser.add_argument(
        "--countries",
        type=str,
        default=None,
        help="Comma-separated country list or number of countries",
    )
    wojak_parser.add_argument(
        "--segments",
        "--count",
        type=int,
        default=None,
        help="Number of country segments (default: all curated for topic)",
    )
    wojak_parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output video file or directory path",
    )
    wojak_parser.add_argument(
        "--fps",
        type=int,
        default=30,
        help="Video frame rate (default 30)",
    )
    wojak_parser.add_argument(
        "--music",
        type=str,
        default=None,
        help="Path to custom BGM audio file",
    )
    wojak_parser.add_argument(
        "--disclaimer-seconds",
        type=float,
        default=3.5,
        help="Duration of Scene 0 parody disclaimer card in seconds (default 3.5)",
    )
    wojak_parser.add_argument(
        "--scene-seconds",
        type=float,
        default=4.5,
        help="Duration of each country meme scene in seconds (default 4.5)",
    )
    wojak_parser.add_argument(
        "--no-thumbnail",
        action="store_true",
        default=False,
        help="Disable high-CTR YouTube thumbnail generation",
    )
    wojak_parser.add_argument(
        "--publish",
        action="store_true",
        default=False,
        help="Upload the rendered Wojak video to YouTube after rendering",
    )

    return parser


def cmd_run(args: argparse.Namespace, config: Settings, state_manager: StateManager) -> int:
    """Execute clipping pipeline on provided links."""
    logger.info("Executing AutoShorts pipeline run...")
    layout = getattr(args, "layout", None)
    if getattr(args, "streamer_cam", False):
        layout = "cam_gameplay"
    if layout:
        config.branding.layout_mode = layout
    pipeline = ShortsPipeline(
        config=config,
        state_manager=state_manager,
        concurrency=getattr(args, "parallel", None),
    )

    jobs = pipeline.process_batch(
        jobs_or_links=args.links,
        auto_upload=args.auto_upload,
        immediate_upload=args.upload,
        background_category=args.bg,
        dopamine_level=args.preset,
        custom_instructions=args.instructions,
        enable_chyron=getattr(args, "chyron", False),
        chyron_badge=getattr(args, "chyron_badge", None),
        chyron_text=getattr(args, "chyron_text", None),
        render_method=getattr(args, "render_method", None),
        render_methods=getattr(args, "render_methods", None),
        render_strategy=getattr(args, "render_strategy", None),
    )

    print("\n" + "=" * 60)
    print("PIPELINE EXECUTION SUMMARY")
    print("=" * 60)
    enc = sys.stdout.encoding or "utf-8"
    for j in jobs:
        print(f"Job ID: {j.id} | Status: {j.status.value}")
        if j.video_metadata:
            title_safe = j.video_metadata.title.encode(enc, errors="replace").decode(enc)
            print(f"  Title: {title_safe}")
        if j.rendered_video_path:
            print(f"  Master MP4: {j.rendered_video_path}")
        used = (j.diagnostics.get("render") or {}).get("method_used")
        if used:
            print(f"  Render method: {used}")
        for vname, vinfo in (j.diagnostics.get("render_variants") or {}).items():
            state = vinfo.get("path") if vinfo.get("ok") else f"FAILED ({vinfo.get('error')})"
            print(f"  Variant {vname}: {state}")
        if j.upload_result:
            print(f"  Uploaded URL: {j.upload_result.url} (Verified: {j.upload_result.verified})")
        if j.error_message:
            err_safe = str(j.error_message).encode(enc, errors="replace").decode(enc)
            print(f"  Error: {err_safe}")
        print("-" * 60)

    failed_count = sum(1 for j in jobs if j.status == JobStatus.FAILED)
    return 1 if failed_count > 0 else 0


def cmd_dashboard(args: argparse.Namespace, config: Settings, state_manager: StateManager) -> int:
    """Launch the FastAPI dashboard."""
    from autoshorts.dashboard.app import run_dashboard

    open_browser = not args.no_browser
    run_dashboard(
        host=args.host,
        port=args.port,
        open_browser=open_browser,
        open_edge=args.open_edge,
    )
    return 0


def cmd_scheduler(args: argparse.Namespace, config: Settings, state_manager: StateManager) -> int:
    """Run upload scheduler tick or background daemon."""
    tracker = QuotaTracker(state_manager=state_manager, config=config)

    if config.upload.mode == "local":
        uploader = LocalExportUploader()
    elif config.upload.mode == "studio":
        try:
            from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader

            uploader = PlaywrightStudioUploader(config=config)
        except Exception as e:
            logger.warning(
                "Playwright Studio uploader initialization failed: %s. Using LocalExportUploader fallback.",
                e,
            )
            uploader = LocalExportUploader()
    else:
        try:
            uploader = YouTubeApiUploader(
                config=config,
                quota_tracker=tracker,
            )
        except Exception as e:
            logger.warning(
                "YouTube API uploader initialization deferred or failed: %s. Using LocalExportUploader fallback.",
                e,
            )
            uploader = LocalExportUploader()

    scheduler = SpreadScheduler(
        state_mgr=state_manager,
        quota_tracker=tracker,
        uploader=uploader,
        config=config,
    )

    if getattr(config, "retention", None) and config.retention.enabled:
        try:
            startup_cleaner = SafetyCleaner(config=config, state_manager=state_manager)
            pruned_startup = startup_cleaner.prune_work_dir()
            if pruned_startup:
                eff_dry_run = getattr(config.retention, "dry_run", True)
                if eff_dry_run:
                    logger.info(
                        "Scheduler startup retention (dry-run): would delete %d work directories: %s",
                        len(pruned_startup),
                        [p.name for p in pruned_startup],
                    )
                else:
                    logger.info(
                        "Scheduler startup retention: pruned %d work directories.",
                        len(pruned_startup),
                    )
        except Exception as ex:
            logger.debug("Non-critical scheduler startup retention check: %s", ex)

    if args.once or not args.daemon:
        logger.info("Running single scheduler dispatch tick...")
        result = scheduler.run_tick()
        if result:
            print(f"Dispatched upload: {result.video_id} ({result.url})")
        else:
            print("No slots due or dispatch conditions not met.")
        return 0

    logger.info(
        "Starting AutoShorts scheduler daemon (interval=%ds)... Press Ctrl+C to stop.",
        args.interval,
    )
    try:
        while True:
            try:
                res = scheduler.run_tick()
                if res:
                    logger.info("Scheduler dispatched: %s", res.video_id)
            except Exception as e:
                logger.error("Error in scheduler tick: %s", e)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("Scheduler daemon stopped by operator.")
        return 0


def cmd_quota(args: argparse.Namespace, config: Settings, state_manager: StateManager) -> int:
    """Print daily quota metrics."""
    import json

    tracker = QuotaTracker(state_manager=state_manager, config=config)
    status = tracker.get_status()

    if args.json:
        print(json.dumps(status, indent=2))
        return 0

    upload_b = status.get("upload_bucket", {})
    general_b = status.get("general_bucket", {})
    daily_c = status.get("daily_cap", {})

    print("\n" + "=" * 50)
    print("YOUTUBE DATA API v3 QUOTA STATUS (PACIFIC TIME)")
    print("=" * 50)
    print(f"Quota Date (PT):      {status.get('date_pt')}")
    print(
        f"Upload Bucket (100):  {upload_b.get('spent', 0)} spent / {upload_b.get('remaining', 100)} remaining"
    )
    print(
        f"General Bucket:       {general_b.get('spent', 0)} spent / {general_b.get('daily_allocation', 10000)} pool ({general_b.get('remaining', 10000)} remaining)"
    )
    print(
        f"Completed Today:      {daily_c.get('completed', 0)} / {daily_c.get('cap', 40)} shorts cap"
    )
    print(
        f"Can Upload Now:       {'YES' if upload_b.get('can_upload_fresh', True) else 'NO (Throttled or Exhausted)'}"
    )
    print("=" * 50)
    return 0


def cmd_clean(args: argparse.Namespace, config: Settings, state_manager: StateManager) -> int:
    """Purge intermediate work files safely."""
    cleaner = SafetyCleaner(
        config=config,
        state_manager=state_manager,
        dry_run=args.dry_run,
        keep_final_days=args.days,
    )

    total_deleted = 0
    if args.job_id:
        paths = cleaner.cleanup_job(
            job_id=args.job_id,
            force=args.force,
            dry_run=args.dry_run,
            keep_final_days=args.days,
        )
        total_deleted = len(paths)
        print(
            f"Job {args.job_id}: {'Would delete' if args.dry_run else 'Deleted'} {len(paths)} items."
        )
    else:
        # Purge only completed or cleaned jobs. --force relaxes the upload-verification check
        # inside the cleaner; it must never widen the set to in-progress or unreviewed jobs.
        jobs = state_manager.list_jobs(limit=500)
        for j in jobs:
            if j.status in (JobStatus.COMPLETED, JobStatus.CLEANED):
                try:
                    p = cleaner.cleanup_job(
                        job_id=j.id,
                        force=args.force,
                        dry_run=args.dry_run,
                        keep_final_days=args.days,
                    )
                    total_deleted += len(p)
                except Exception as ex:
                    logger.debug("Skipped job %s during cleanup: %s", j.id, ex)

        action = "Would delete" if args.dry_run else "Deleted"
        print(f"Cleanup summary: {action} {total_deleted} items across completed jobs.")

    return 0


def cmd_cleanup_work(
    args: argparse.Namespace, config: Settings, state_manager: StateManager
) -> int:
    """Prune work directory according to configured retention policy."""
    dry_run = args.dry_run if args.dry_run else None
    cleaner = SafetyCleaner(config=config, state_manager=state_manager)
    pruned = cleaner.prune_work_dir(dry_run=dry_run, force=True)
    is_dry = bool(args.dry_run or (dry_run is None and config.retention.dry_run))
    action = "Would prune" if is_dry else "Pruned"
    print(f"Retention policy: {action} {len(pruned)} job item(s) from work directory.")
    for p in pruned:
        print(f"  - {p}")
    return 0


def cmd_edge_login(args: argparse.Namespace, config: Settings, state_manager: StateManager) -> int:
    """Launch MS Edge browser session for YouTube Studio sign-in."""
    from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader

    uploader = PlaywrightStudioUploader(config=config, headless=False)
    timeout = getattr(args, "timeout", 300)
    logger.info("Opening MS Edge login session (timeout=%ds)...", timeout)
    success = uploader.open_login_session(timeout_sec=float(timeout))
    if success:
        print("\n[OK] Sign-in detected successfully! Edge profile updated.")
        return 0
    print("\n[FAIL] Sign-in session closed or timed out.")
    return 1


def cmd_longform(args: argparse.Namespace, config: Settings, state_manager: StateManager) -> int:
    """Render a long-form stick-figure video or Wojak Shorts video and print the result paths."""
    from autoshorts.longform.render import render_longform

    target_topic = args.topic or "science"
    if getattr(args, "url", None):
        try:
            from autoshorts.metadata.extractor import MetadataExtractor
            from autoshorts.transcript.fetcher import TranscriptFetcher

            extractor = MetadataExtractor()
            meta = extractor.extract(args.url)
            title = meta.title if meta and meta.title else "YouTube Video"

            fetcher = TranscriptFetcher(config=config)
            transcript_res = fetcher.fetch(args.url)
            summary_text = ""
            if transcript_res and transcript_res.segments:
                summary_text = " ".join(s.text for s in transcript_res.segments[:60])
            target_topic = f"{title}: {summary_text[:350]}" if summary_text else title
        except Exception as e:
            logger.warning("Could not extract YouTube transcript for longform: %s", e)
            target_topic = target_topic or "science"

    aspect_ratio = getattr(args, "aspect_ratio", "16:9")
    is_shorts = getattr(args, "shorts", False) or aspect_ratio == "9:16"

    if is_shorts:
        from autoshorts.longform.wojak_shorts import render_wojak_shorts_video

        seg_count = getattr(args, "segments", 7)
        subtitle_mode = getattr(args, "subtitles", "kinetic")
        words_per_burst = getattr(args, "words_per_burst", 2)
        font_size = getattr(args, "font_size", 84)
        render_method = getattr(args, "render_method", "ffmpeg")

        try:
            result = render_wojak_shorts_video(
                topic=target_topic,
                segment_count=seg_count,
                config=config,
                state_manager=state_manager,
                subtitle_mode=subtitle_mode,
                words_per_burst=words_per_burst,
                font_size=font_size,
                render_method=render_method,
            )
        except (ValueError, RuntimeError) as e:
            print(str(e), file=sys.stderr)
            return 1

        print(f"Title: {result.title}")
        print(f"Video: {result.video_path}")
        print(f"Thumbnail: {result.thumbnail_path}")
        print(f"Duration: {result.duration:.1f}s")
        print("Aspect Ratio: 9:16")

        if getattr(args, "publish", False):
            try:
                from autoshorts.longform.publish import publish_longform

                print("Uploading Wojak Shorts video to YouTube...")
                yt_url = publish_longform(result, config=config, state_manager=state_manager)
                print(f"Published to YouTube: {yt_url}")
            except Exception as p_err:
                print(f"Failed to upload video: {p_err}", file=sys.stderr)
                return 1
        return 0

    try:
        result = render_longform(target_topic, args.minutes, args.provider, config)
    except (ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        return 1

    print(f"Title: {result.title}")
    print(f"Video: {result.video_path}")
    print(f"Thumbnail: {result.thumbnail_path}")
    print(f"Duration: {result.duration:.1f}s")

    if getattr(args, "publish", False):
        try:
            from autoshorts.longform.publish import publish_longform

            print("Uploading long-form video to YouTube...")
            yt_url = publish_longform(result, config=config, state_manager=state_manager)
            print(f"Published to YouTube: {yt_url}")
        except Exception as p_err:
            print(f"Failed to upload long-form video: {p_err}", file=sys.stderr)
            return 1

    return 0


def cmd_meme(args: argparse.Namespace, config: Settings, state_manager: StateManager) -> int:
    """Render a Country Meme or Types Of... meme animation video and print result paths."""
    from autoshorts.meme import MemeConfig, generate_topic_idea, render_meme

    topic = (getattr(args, "topic", "") or "").strip()
    category = getattr(args, "category", "countries") or "countries"
    mode = getattr(args, "mode", "country_meme")

    if getattr(args, "auto_topic", False) or not topic:
        topic_idea, cat_idea = generate_topic_idea(category=category, mode=mode)
        if not topic:
            topic = topic_idea
            category = cat_idea

    meme_cfg = MemeConfig(
        mode=mode,
        topic=topic,
        category=category,
        segment_count=getattr(args, "segments", 6),
        aspect_ratio=getattr(args, "aspect_ratio", "9:16"),
        visual_style=getattr(args, "style", "anime_color"),
        voice=getattr(args, "voice", "en-US-ChristopherNeural"),
        publish=getattr(args, "publish", False),
    )

    try:
        result = render_meme(
            config=meme_cfg,
            app_settings=config,
            state_manager=state_manager,
        )
    except (ValueError, RuntimeError) as e:
        print(f"Error rendering meme video: {e}", file=sys.stderr)
        return 1

    print(f"Title: {result.title}")
    print(f"Video: {result.video_path}")
    print(f"Thumbnail: {result.thumbnail_path}")
    print(f"Duration: {result.duration:.1f}s")
    print(f"Aspect Ratio: {result.aspect_ratio}")
    print(f"QC Passed: {result.qc_passed}")
    if result.youtube_url:
        print(f"Published: {result.youtube_url}")
    return 0


def cmd_wojak(args: argparse.Namespace, config: Settings, state_manager: StateManager) -> int:
    """Render a SapX-style 16:9 long-form Wojak meme video (or 9:16 Shorts) and print result paths."""
    aspect_ratio = getattr(args, "aspect_ratio", "16:9")
    is_shorts = getattr(args, "shorts", False) or aspect_ratio == "9:16"

    if is_shorts:
        from autoshorts.longform.wojak_shorts import render_wojak_shorts_video

        topic = getattr(args, "topic", "hospital") or "hospital"
        seg_count = getattr(args, "segments", None)
        countries_arg = getattr(args, "countries", None)
        if seg_count is None and countries_arg and str(countries_arg).strip().isdigit():
            seg_count = int(countries_arg)
        if seg_count is None:
            seg_count = 7

        try:
            result = render_wojak_shorts_video(
                topic=topic,
                segment_count=seg_count,
                config=config,
                state_manager=state_manager,
            )
        except (ValueError, RuntimeError) as e:
            print(str(e), file=sys.stderr)
            return 1

        print(f"Title: {result.title}")
        print(f"Video: {result.video_path}")
        print(f"Thumbnail: {result.thumbnail_path}")
        print(f"Duration: {result.duration:.1f}s")
        print("Aspect Ratio: 9:16")

        if getattr(args, "publish", False):
            try:
                from autoshorts.longform.publish import publish_longform

                print("Uploading Wojak Shorts video to YouTube...")
                yt_url = publish_longform(result, config=config, state_manager=state_manager)
                print(f"Published to YouTube: {yt_url}")
            except Exception as p_err:
                print(f"Failed to upload video: {p_err}", file=sys.stderr)
                return 1
        return 0

    from autoshorts.longform.wojak_pipeline import (
        WojakPipelineConfig,
        WojakScene,
        resolve_topic_scenes,
        run_wojak_pipeline,
    )

    topic = getattr(args, "topic", "hospital") or "hospital"
    scene_count = getattr(args, "segments", None)

    countries_arg = getattr(args, "countries", None)
    scenes: list[WojakScene] = []
    title = f"Every Country's {topic.capitalize()}"
    if countries_arg:
        if isinstance(countries_arg, str) and not countries_arg.strip().isdigit():
            c_list = [c.strip() for c in countries_arg.split(",") if c.strip()]
            from autoshorts.longform.wojak_topics import (
                generate_procedural_joke,
                get_curated_topic_data,
            )

            topic_data = get_curated_topic_data(topic) or {}

            for idx, c in enumerate(c_list):
                side = "left" if idx % 2 == 0 else "right"
                c_clean = c.lower()
                joke = None
                for c_k, c_v in topic_data.items():
                    if c_k.lower() == c_clean or c_v.get("heading", "").lower() == c_clean:
                        joke = c_v.get("joke_text")
                        break
                if not joke:
                    joke_dict = generate_procedural_joke(topic, c)
                    joke = joke_dict.get("joke_text", f"Everyday {topic} in {c}")

                scenes.append(
                    WojakScene(
                        country=c,
                        caption=joke,
                        character_side=side,
                        setting=topic,
                    )
                )
        elif str(countries_arg).strip().isdigit():
            scene_count = int(countries_arg)

    if not scenes:
        title, scenes = resolve_topic_scenes(topic, scene_count=scene_count)
    elif scene_count and scene_count > 0:
        scenes = scenes[:scene_count]

    out_arg = getattr(args, "output", None)
    if out_arg:
        out_p = Path(out_arg)
        if out_p.suffix == ".mp4":
            out_dir = out_p.parent
        else:
            out_dir = out_p
    else:
        out_dir = Path("work") / "wojak"

    cfg = WojakPipelineConfig(
        topic=topic,
        title=title,
        scenes=scenes,
        fps=getattr(args, "fps", 30),
        disclaimer_duration=getattr(args, "disclaimer_seconds", 3.5),
        scene_duration=getattr(args, "scene_seconds", 4.5),
        output_dir=out_dir,
        music_file_path=getattr(args, "music", None),
        generate_thumbnail=not getattr(args, "no_thumbnail", False),
    )

    try:
        result = run_wojak_pipeline(cfg)
    except Exception as e:
        print(f"Error rendering Wojak video: {e}", file=sys.stderr)
        return 1

    print(f"Title: {result.title}")
    print(f"Video: {result.video_path}")
    if result.thumbnail_path:
        print(f"Thumbnail: {result.thumbnail_path}")
    print(f"Duration: {result.duration_sec:.1f}s ({result.scene_count} scenes)")
    print(
        f"Streaming FPS: {result.streaming_fps:.1f} fps (Rendered in {result.render_time_sec:.2f}s)"
    )

    if getattr(args, "publish", False):
        try:
            from autoshorts.longform.publish import publish_longform
            from autoshorts.longform.types import LongformResult

            print("Uploading Wojak long-form video to YouTube...")
            lf_result = LongformResult(
                topic=result.topic,
                title=result.title,
                video_path=result.video_path,
                thumbnail_path=result.thumbnail_path or result.video_path,
                duration=result.duration_sec,
                description=f"{result.title}\n\nParody meme video in the style of Wojak country comparisons. Created for entertainment purposes.",
                hashtags=["#wojak", "#memes", "#everycountry", "#countrymemes"],
                provider="wojak_pipeline",
            )
            yt_url = publish_longform(lf_result, config=config, state_manager=state_manager)
            print(f"Published to YouTube: {yt_url}")
        except Exception as p_err:
            print(f"Failed to upload Wojak video: {p_err}", file=sys.stderr)
            return 1

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Master CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    # Setup logging
    log_level = getattr(args, "log_level", "INFO").upper()
    setup_logging(level=log_level)

    if args.config and not Path(args.config).is_file():
        parser.error(f"config file not found: {args.config}")

    try:
        return _dispatch(parser, args)
    except InvalidURLError as exc:
        print(f"Error: invalid link: {exc}", file=sys.stderr)
        return 2
    except AutoShortsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted by operator.", file=sys.stderr)
        return 130


def _dispatch(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Load configuration and run the selected subcommand."""
    config = load_config(args.config)
    state_manager = StateManager()

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "run":
        return cmd_run(args, config, state_manager)
    elif args.command == "dashboard":
        return cmd_dashboard(args, config, state_manager)
    elif args.command == "scheduler":
        return cmd_scheduler(args, config, state_manager)
    elif args.command == "quota":
        return cmd_quota(args, config, state_manager)
    elif args.command == "cleanup-work":
        return cmd_cleanup_work(args, config, state_manager)
    elif args.command == "clean":
        return cmd_clean(args, config, state_manager)
    elif args.command == "edge-login":
        return cmd_edge_login(args, config, state_manager)
    elif args.command == "longform":
        return cmd_longform(args, config, state_manager)
    elif args.command in ("meme", "types-of"):
        return cmd_meme(args, config, state_manager)
    elif args.command == "wojak":
        return cmd_wojak(args, config, state_manager)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
