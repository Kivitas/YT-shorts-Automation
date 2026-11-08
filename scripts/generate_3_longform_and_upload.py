"""Script to generate 3 long-form videos with 20 viral hashtags and upload them to YouTube."""

import logging
import sys
from pathlib import Path

# Configure logging to console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("batch_longform")

from autoshorts.core.config import load_config
from autoshorts.core.state_manager import StateManager
from autoshorts.longform.publish import publish_longform
from autoshorts.longform.render import render_script
from autoshorts.longform.script import build_script

VIRAL_20_HASHTAGS = [
    "viral",
    "trending",
    "fyp",
    "foryou",
    "explore",
    "youtube",
    "shorts",
    "reels",
    "tiktok",
    "motivation",
    "money",
    "success",
    "algorithm",
    "gaming",
    "mindset",
    "growth",
    "podcast",
    "entertainment",
    "comedy",
    "life",
]

TOPICS = [
    ("finance", "finance"),
    ("science", "science"),
    ("motivation", "motivation"),
]


def run():
    config = load_config()
    state_manager = StateManager()

    results = []
    hashtags_block = " ".join(f"#{t}" for t in VIRAL_20_HASHTAGS)

    for i, (topic, genre) in enumerate(TOPICS, 1):
        logger.info(f"=== Starting Video {i}/3: Topic '{topic}' (Genre: {genre}) ===")
        print(f"\n[Video {i}/3] Generating script for {topic}...", flush=True)

        # 1. Build the script (~1.0 minute target duration)
        script = build_script(topic, minutes=1.0, provider="api", config=config, genre=genre)
        script.hashtags = list(VIRAL_20_HASHTAGS)
        logger.info(f"Script ready: '{script.title}' ({len(script.sections)} sections)")

        # 2. Render narration, visual frames, audio mixing, and thumbnail
        print(f"[Video {i}/3] Rendering video & audio for '{script.title}'...", flush=True)
        longform_result = render_script(
            script,
            config=config,
            extra_description=f"\n\n{hashtags_block}",
        )
        longform_result.hashtags = list(VIRAL_20_HASHTAGS)
        logger.info(
            f"Render complete: {longform_result.video_path} (Duration: {longform_result.duration:.1f}s)"
        )

        # 3. Upload to YouTube
        print(f"[Video {i}/3] Uploading to YouTube...", flush=True)
        try:
            yt_url = publish_longform(
                longform_result,
                config=config,
                state_manager=state_manager,
                queue_on_quota=True,
            )
            print(f"[Video {i}/3] Successfully published: {yt_url}", flush=True)
            results.append({
                "index": i,
                "topic": topic,
                "title": longform_result.title,
                "duration": f"{longform_result.duration:.1f}s",
                "video_path": str(longform_result.video_path),
                "url": yt_url,
                "hashtags_count": len(VIRAL_20_HASHTAGS),
            })
        except Exception as e:
            logger.error(f"Upload failed for video {i}: {e}", exc_info=True)
            results.append({
                "index": i,
                "topic": topic,
                "title": longform_result.title,
                "duration": f"{longform_result.duration:.1f}s",
                "video_path": str(longform_result.video_path),
                "url": f"Error: {e}",
                "hashtags_count": len(VIRAL_20_HASHTAGS),
            })

    print("\n" + "=" * 60, flush=True)
    print("BATCH SUMMARY", flush=True)
    print("=" * 60, flush=True)
    for res in results:
        print(f"[{res['index']}] {res['title']} ({res['duration']}) -> {res['url']}", flush=True)


if __name__ == "__main__":
    run()
