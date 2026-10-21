#!/usr/bin/env python3
"""CLI tool for collecting and organizing high-dopamine background gameplay footage.

Searches YouTube via yt-dlp, filters for high quality non-watermarked footage,
downloads capped at <=1080p, cuts into 30-90s vertical-friendly segments with FFmpeg,
and indexes clips in category manifest.json files.

Usage:
    python scripts/collect_gameplay.py --list
    python scripts/collect_gameplay.py --categories minecraft subway --dry-run
    python scripts/collect_gameplay.py --categories minecraft subway --per-category 2 --segment-sec 60
    python scripts/collect_gameplay.py --categories all --per-category 1
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from autoshorts.media.gameplay_collector import (
    DEFAULT_CATEGORY_QUERIES,
    collect_gameplay,
    get_categories_summary,
    normalize_category_name,
)

logger = logging.getLogger("autoshorts.gameplay_collector")


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Collect and organize royalty-free high-dopamine background gameplay clips.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--categories",
        nargs="+",
        default=["minecraft", "subway"],
        help=(
            "One or more categories to collect (e.g. 'minecraft subway gta'). "
            "Use 'all' to collect across all predefined high-dopamine categories."
        ),
    )
    parser.add_argument(
        "--per-category",
        type=int,
        default=2,
        help="Number of source videos to fetch and process per category.",
    )
    parser.add_argument(
        "--segment-sec",
        type=float,
        default=60.0,
        help="Segment cut duration in seconds (clamped between 30 and 90 seconds).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Search and print candidate videos without downloading or cutting.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        default=False,
        help="List available categories, default queries, and current folder clip inventory.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable detailed debug logging output.",
    )
    return parser.parse_args(args)


def print_categories_list() -> None:
    """Print tabular inventory and query summary for all background categories."""
    summary = get_categories_summary()
    print("=" * 80)
    print("BACKGROUND GAMEPLAY CATEGORIES & INVENTORY")
    print("=" * 80)
    print(f"{'Category':<18} {'Clips':<8} {'Manifest':<10} {'Default Query'}")
    print("-" * 80)
    for row in summary:
        has_man = f"Yes ({row['manifest_clips_count']})" if row["has_manifest"] else "No"
        print(
            f"{row['category']:<18} {row['clips_count']:<8} {has_man:<10} {row['default_query']}"
        )
    print("=" * 80)


def main(args: list[str] | None = None) -> int:
    """Main execution entrypoint."""
    parsed = parse_args(args)

    log_level = logging.DEBUG if parsed.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if parsed.list:
        print_categories_list()
        return 0

    # Expand categories
    raw_categories: list[str] = []
    for item in parsed.categories:
        # Support comma-separated strings inside arguments
        for part in item.split(","):
            cleaned = part.strip()
            if cleaned:
                raw_categories.append(cleaned)

    if "all" in [c.lower() for c in raw_categories]:
        target_categories = list(DEFAULT_CATEGORY_QUERIES.keys())
    else:
        target_categories = [normalize_category_name(c) for c in raw_categories]

    # Deduplicate while preserving order
    seen: set[str] = set()
    categories_to_run: list[str] = []
    for c in target_categories:
        if c not in seen:
            seen.add(c)
            categories_to_run.append(c)

    print(
        f"\nStarting gameplay collection for categories: {', '.join(categories_to_run)} "
        f"(per-category={parsed.per_category}, segment-sec={parsed.segment_sec:.1f}s, "
        f"dry-run={parsed.dry_run})\n"
    )

    results = collect_gameplay(
        categories=categories_to_run,
        per_category=parsed.per_category,
        segment_duration_sec=parsed.segment_sec,
        dry_run=parsed.dry_run,
    )

    print("\n" + "=" * 80)
    print("COLLECTION SUMMARY")
    print("=" * 80)
    for cat, res in results.items():
        if "error" in res:
            print(f"[{cat}] ERROR: {res['error']}")
            continue

        if parsed.dry_run:
            cands = res.get("candidates", [])
            print(f"[{cat}] Dry-run found {len(cands)} candidates:")
            for c in cands:
                print(f"   - {c['id']}: '{c['title']}' ({c['duration']}s, score={c['score']})")
        else:
            print(
                f"[{cat}] Processed: {res.get('videos_processed', 0)} videos | "
                f"New Segments: {res.get('segments_created', 0)} | "
                f"Total In Manifest: {res.get('total_clips', 0)}"
            )

    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
