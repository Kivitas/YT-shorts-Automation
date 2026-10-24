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
from autoshorts.core.logging_setup import setup_logging
from autoshorts.core.models import JobStatus
from autoshorts.core.state_manager import StateManager
from autoshorts.pipeline import ShortsPipeline
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.scheduler.scheduler import SpreadScheduler
from autoshorts.uploader.local_exporter import LocalExportUploader
from autoshorts.uploader.youtube_api import YouTubeApiUploader

logger = logging.getLogger("autoshorts.cli")


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

    return parser


def cmd_run(args: argparse.Namespace, config: Settings, state_manager: StateManager) -> int:
    """Execute clipping pipeline on provided links."""
    logger.info("Executing AutoShorts pipeline run...")
    pipeline = ShortsPipeline(config=config, state_manager=state_manager)

    jobs = pipeline.process_batch(
        jobs_or_links=args.links,
        auto_upload=args.auto_upload,
        immediate_upload=args.upload,
        background_category=args.bg,
        dopamine_level=args.preset,
        custom_instructions=args.instructions,
    )

    print("\n" + "=" * 60)
    print("PIPELINE EXECUTION SUMMARY")
    print("=" * 60)
    for j in jobs:
        print(f"Job ID: {j.id} | Status: {j.status.value}")
        if j.video_metadata:
            print(f"  Title: {j.video_metadata.title}")
        if j.rendered_video_path:
            print(f"  Master MP4: {j.rendered_video_path}")
        if j.upload_result:
            print(f"  Uploaded URL: {j.upload_result.url} (Verified: {j.upload_result.verified})")
        if j.error_message:
            print(f"  Error: {j.error_message}")
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
        f"Completed Today:      {daily_c.get('completed', 0)} / {daily_c.get('cap', 50)} shorts cap"
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


def main(argv: Sequence[str] | None = None) -> int:
    """Master CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    # Setup logging
    log_level = getattr(args, "log_level", "INFO").upper()
    setup_logging(level=log_level)

    if args.config and not Path(args.config).is_file():
        parser.error(f"config file not found: {args.config}")

    # Load configuration
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
    elif args.command == "clean":
        return cmd_clean(args, config, state_manager)
    elif args.command == "edge-login":
        return cmd_edge_login(args, config, state_manager)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
