"""Long-Form Wojak Meme Video Pipeline Orchestrator (SapX Style).

Orchestrates:
1. Script curation: Built-in viral topics (Hospital Bill, High School, Public Transport) or custom input.
2. Scene 0 (0 to 3.5s): Sleek Parody Disclaimer Card.
3. Scenes 1 to N (4.5s each): 16:9 1920x1080 Country scenes with 110px header bar,
   photographic environment, bottom-anchored Wojak character, and floating white punchline card.
4. Continuous bouncy comedic meme BGM track + subtle transition SFX + EBU R128 mastering.
5. High-speed raw RGB24 video streaming directly to FFmpeg stdin (no intermediate frame files).
6. High-CTR 16:9 YouTube thumbnail generation.
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from autoshorts.longform.wojak_audio import assemble_wojak_audio
from autoshorts.longform.wojak_backgrounds import get_wojak_background
from autoshorts.longform.wojak_compositor import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    render_disclaimer_frame,
    render_wojak_scene_frame,
)
from autoshorts.longform.wojak_generator import purge_remote_tmp, wojak_for_context
from autoshorts.longform.wojak_thumbnail import generate_wojak_thumbnail
from autoshorts.media.encoder_probe import get_ffmpeg_path

logger = logging.getLogger(__name__)

DEFAULT_FPS = 30
DEFAULT_DISCLAIMER_DUR = 3.5  # seconds
DEFAULT_SCENE_DUR = 4.5  # seconds


# ============================================================================
# Data Models
# ============================================================================


@dataclass
class WojakScene:
    """One country meme scene specification."""

    country: str
    caption: str
    emotion: str = "shocked"  # shocked, annoyed, sad, smug, tired, neutral
    prop: str = "bill"
    character_side: str = "left"  # left or right (punchline card goes to opposite side)
    setting: str = "hospital"
    custom_photo_path: str | None = None
    gender: str | None = None
    race: str | None = None
    height: str | None = None
    age: str | None = None
    clothing: str | None = None
    hair: str | None = None


@dataclass
class WojakPipelineConfig:
    """Configuration settings for the Wojak pipeline run."""

    topic: str = "hospital"
    title: str = "Every Country's Hospital Bill"
    scenes: list[WojakScene] = field(default_factory=list)
    script: Any | None = None
    fps: int = DEFAULT_FPS
    disclaimer_duration: float = DEFAULT_DISCLAIMER_DUR
    scene_duration: float = DEFAULT_SCENE_DUR
    output_dir: Path = field(default_factory=lambda: Path("work") / "wojak")
    music_file_path: Path | str | None = None
    generate_thumbnail: bool = True
    custom_disclaimer_text: str | None = None
    width: int = FRAME_WIDTH
    height: int = FRAME_HEIGHT
    # Opt-in: build (or load cached) format profiles for the reference channels before rendering.
    research_channel_profiles: bool = False


# Reference channels whose format the Wojak videos imitate (research only; no content is copied).
CHANNEL_PROFILE_URLS: tuple[str, ...] = (
    "https://www.youtube.com/@SapX",
    "https://www.youtube.com/@Wojak-Explainer",
)


def _log_channel_profiles() -> None:
    """Log format profiles of the reference channels. Research failures never fail the render."""
    try:
        from autoshorts.longform.wojak_web_research import build_format_profile
    except Exception as exc:
        logger.warning("Channel research unavailable: %s", exc)
        return
    for url in CHANNEL_PROFILE_URLS:
        try:
            profile = build_format_profile(url)
        except Exception as exc:
            logger.warning("Channel research failed for %s: %s", url, exc)
            continue
        if profile is None:
            logger.info("No channel profile available for %s", url)
            continue
        logger.info(
            "Channel %s: %d videos, median %.0fs, top title patterns %s",
            url,
            profile.video_count,
            profile.median_duration_seconds or 0.0,
            list(profile.title_patterns[:3]),
        )


@dataclass
class WojakPipelineResult:
    """Output artifacts and performance metrics from the pipeline run."""

    video_path: Path
    thumbnail_path: Path | None
    audio_path: Path
    title: str
    topic: str
    duration_sec: float
    scene_count: int
    frame_count: int
    render_time_sec: float
    fps: int
    streaming_fps: float


# ============================================================================
# Built-In Curated Viral Meme Scripts
# ============================================================================

CURATED_HOSPITAL_SCENES: list[dict[str, str]] = [
    {
        "country": "USA",
        "caption": "1x Tylenol: $4,500. Ambulance ride: $18,000. Total: Bankruptcy.",
        "emotion": "shocked",
        "prop": "bill",
    },
    {
        "country": "UK",
        "caption": "Free treatment, but the waiting room has patients from 2019.",
        "emotion": "tired",
        "prop": "tea_cup",
    },
    {
        "country": "Canada",
        "caption": "Everything is 100% free, but your next appointment is in 2032.",
        "emotion": "annoyed",
        "prop": "phone",
    },
    {
        "country": "Sweden",
        "caption": "Visit cost $12. Doctor gave me herbal tea and told me to enjoy nature.",
        "emotion": "smug",
        "prop": "tea_cup",
    },
    {
        "country": "South Korea",
        "caption": "Walked in for a cough, walked out with flawless skin and 2 cosmetic surgeries.",
        "emotion": "shocked",
        "prop": "phone",
    },
    {
        "country": "India",
        "caption": "Consultation was $3, but doctor prescribed 14 different syrups and 8 tablets.",
        "emotion": "neutral",
        "prop": "clipboard",
    },
    {
        "country": "Germany",
        "caption": "Must submit Krankenkasse Form 48B in triplicate stamped by the Bürgermeister.",
        "emotion": "annoyed",
        "prop": "clipboard",
    },
    {
        "country": "Japan",
        "caption": "Polite robot bowed, checked vitals, bill was $18 with complimentary green tea.",
        "emotion": "neutral",
        "prop": "tea_cup",
    },
    {
        "country": "Italy",
        "caption": "Doctor was having espresso, prescribed pasta and fresh sea air, bill was €8.",
        "emotion": "smug",
        "prop": "coffee_cup",
    },
    {
        "country": "Australia",
        "caption": "Doctor said 'she\\'ll be right mate', covered by Medicare, hospital had a snake.",
        "emotion": "neutral",
        "prop": "phone",
    },
    {
        "country": "Switzerland",
        "caption": "Hospital looks like a 5-star alpine resort, monthly insurance costs more than rent.",
        "emotion": "shocked",
        "prop": "bill",
    },
    {
        "country": "France",
        "caption": "Paid 1 euro. Pharmacist handed me 8 boxes of medicine and a baguette.",
        "emotion": "smug",
        "prop": "pastry",
    },
    {
        "country": "Brazil",
        "caption": "Waited 7 hours in SUS queue, made 12 new lifelong friends and danced samba.",
        "emotion": "happy",
        "prop": "phone",
    },
    {
        "country": "Mexico",
        "caption": "Dr. Simi next to the pharmacy treated me for 50 pesos while the mascot danced.",
        "emotion": "happy",
        "prop": "phone",
    },
    {
        "country": "Poland",
        "caption": "Doctor looked furious you woke him up, prescribed herbal drops and sent you home.",
        "emotion": "tired",
        "prop": "clipboard",
    },
]

CURATED_SCHOOL_SCENES: list[dict[str, str]] = [
    {
        "country": "USA",
        "caption": "Lunch is mystery grey nuggets and lunch debt threatens graduation.",
        "emotion": "sad",
        "prop": "bill",
    },
    {
        "country": "France",
        "caption": "3-course gourmet lunch with brie cheese, fresh baguette, and sparkling water.",
        "emotion": "smug",
        "prop": "pastry",
    },
    {
        "country": "Japan",
        "caption": "Students clean their own classroom spotless and prepare handmade bento boxes.",
        "emotion": "neutral",
        "prop": "tea_cup",
    },
    {
        "country": "UK",
        "caption": "Grey soggy chips and beans, strictly monitored by angry dinner ladies.",
        "emotion": "tired",
        "prop": "tea_cup",
    },
    {
        "country": "South Korea",
        "caption": "School ends at 4 PM, private cram academy Hagwon runs until 11:30 PM.",
        "emotion": "shocked",
        "prop": "clipboard",
    },
    {
        "country": "Sweden",
        "caption": "Free laptops, free hot lunch, zero homework, and school starts at 9 AM.",
        "emotion": "happy",
        "prop": "coffee_cup",
    },
]

CURATED_TRANSPORT_SCENES: list[dict[str, str]] = [
    {
        "country": "Japan",
        "caption": "Train arrives 15 seconds late: Railway director issues national televised apology.",
        "emotion": "shocked",
        "prop": "clipboard",
    },
    {
        "country": "Germany",
        "caption": "Deutsche Bahn: 'Your train is delayed due to unexpected track existing.'",
        "emotion": "annoyed",
        "prop": "phone",
    },
    {
        "country": "UK",
        "caption": "15-minute train ride costs £84. Train cancelled because of leaves on track.",
        "emotion": "shocked",
        "prop": "bill",
    },
    {
        "country": "India",
        "caption": "Peak hour Mumbai local: 3,000 people boarding a 1,000 capacity train, zero fear.",
        "emotion": "happy",
        "prop": "phone",
    },
    {
        "country": "USA",
        "caption": "Nearest bus stop is 9 miles away across an 8-lane stroad with no sidewalk.",
        "emotion": "tired",
        "prop": "phone",
    },
    {
        "country": "Switzerland",
        "caption": "Train arrives at 14:03:00 on the second, smooth as a Rolex watch.",
        "emotion": "smug",
        "prop": "tea_cup",
    },
]


def resolve_topic_scenes(
    topic: str, scene_count: int | None = None
) -> tuple[str, list[WojakScene]]:
    """Resolve a topic name to title and list of WojakScene instances."""
    from autoshorts.longform.wojak_archetypes import resolve_archetype_topic

    # 1. Check Wojak Explainer Archetype Topics (Introverts, Gym, Generations, Coders, Students)
    arch_data = resolve_archetype_topic(topic)
    if arch_data:
        title = arch_data["title"]
        segs = arch_data["segments"]
        if scene_count and scene_count > 0:
            segs = segs[:scene_count]
        scenes: list[WojakScene] = []
        for idx, seg in enumerate(segs):
            side = "left" if idx % 2 == 0 else "right"
            scenes.append(
                WojakScene(
                    country=seg.archetype_title,
                    caption=seg.caption,
                    emotion=seg.expression,
                    prop=seg.prop,
                    character_side=side,
                    setting=seg.setting,
                    gender=getattr(seg, "gender", None),
                    race=getattr(seg, "race", None),
                )
            )
        return title, scenes

    t_clean = topic.strip().lower()
    if "school" in t_clean or "lunch" in t_clean or "education" in t_clean:
        title = "Every Country's High School"
        raw_list = CURATED_SCHOOL_SCENES
        setting = "school"
    elif "transport" in t_clean or "train" in t_clean or "subway" in t_clean:
        title = "Every Country's Public Transport"
        raw_list = CURATED_TRANSPORT_SCENES
        setting = "transport"
    else:
        title = "Every Country's Hospital Bill"
        raw_list = CURATED_HOSPITAL_SCENES
        setting = "hospital"

    if scene_count and scene_count > 0:
        raw_list = raw_list[:scene_count]

    scenes: list[WojakScene] = []
    for idx, item in enumerate(raw_list):
        side = "left" if idx % 2 == 0 else "right"
        scenes.append(
            WojakScene(
                country=item["country"],
                caption=item["caption"],
                emotion=item.get("emotion", "shocked"),
                prop=item.get("prop", "bill"),
                character_side=side,
                setting=setting,
            )
        )
    return title, scenes


# ============================================================================
# Main Orchestrator Pipeline
# ============================================================================


def run_wojak_pipeline(config: WojakPipelineConfig) -> WojakPipelineResult:
    """Execute the full Wojak long-form meme video generation pipeline.

    Steps:
    1. Resolve scenes & timeline durations.
    2. Synthesize continuous comedic BGM + SFX + EBU R128 mastering.
    3. Stream raw RGB24 frames directly into FFmpeg stdin pipe.
    4. Generate high-CTR 16:9 YouTube thumbnail.
    5. Return result with performance metrics.
    """
    start_time = time.perf_counter()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    stage_secs = {"background": 0.0, "character": 0.0}
    if config.research_channel_profiles:
        _log_channel_profiles()

    # 1. Resolve scenes & script
    scenes = config.scenes
    title = config.title
    disclaimer_text = config.custom_disclaimer_text
    disclaimer_heading = "⚠️  PARODY & SATIRE DISCLAIMER"

    # Automated research & improvement if no pre-defined scenes are passed
    if not scenes and config.script is None:
        try:
            from autoshorts.longform.wojak_improver import improve_wojak_script
            from autoshorts.longform.wojak_researcher import research_viral_wojak_ideas

            aspect = (
                "9:16"
                if getattr(config, "height", FRAME_HEIGHT) > getattr(config, "width", FRAME_WIDTH)
                else "16:9"
            )
            briefs = research_viral_wojak_ideas(query=config.topic, aspect_ratio=aspect)
            if briefs:
                best_brief = briefs[0]
                title = best_brief.title
                raw_scenes = [
                    {
                        "heading": s.heading,
                        "punchline": s.punchline,
                        "emotion": s.emotion,
                        "prop": s.prop,
                        "setting": s.setting,
                        "gender": s.gender,
                        "race": s.race,
                        "height": s.height,
                        "clothing": s.clothing,
                        "character_side": s.character_side,
                    }
                    for s in best_brief.scenes
                ]
                report = improve_wojak_script(raw_scenes, topic=config.topic)
                scenes = []
                for s in report.improved_scenes:
                    scenes.append(
                        WojakScene(
                            country=s.get("heading") or s.get("country", ""),
                            caption=s.get("punchline") or s.get("caption", ""),
                            emotion=s.get("emotion", "shocked"),
                            prop=s.get("prop", "bill"),
                            character_side=s.get("character_side", "left"),
                            setting=s.get("setting", config.topic),
                            gender=s.get("gender"),
                            race=s.get("race"),
                            height=s.get("height"),
                            clothing=s.get("clothing"),
                        )
                    )
                logger.info(
                    "Auto-researched '%s' (%d scenes, diversity=%.2f)",
                    title,
                    len(scenes),
                    report.diversity_score,
                )
        except Exception as r_exc:
            logger.debug("Automated research/improver fallback: %s", r_exc)

    if config.script is not None:
        script = config.script
        title = getattr(script, "title", title)
        disclaimer_text = getattr(script, "disclaimer_text", disclaimer_text)
        disclaimer_heading = getattr(script, "disclaimer_heading", disclaimer_heading)
        if hasattr(script, "disclaimer_duration"):
            config.disclaimer_duration = script.disclaimer_duration
        if hasattr(script, "segments") and script.segments:
            scenes = []
            for idx, seg in enumerate(script.segments):
                side = "left" if idx % 2 == 0 else "right"
                char_ctx = getattr(seg, "character_context", {}) or {}
                scenes.append(
                    WojakScene(
                        country=getattr(seg, "heading", None)
                        or getattr(seg, "country", f"Country {idx + 1}"),
                        caption=getattr(seg, "joke_text", ""),
                        emotion=char_ctx.get("expression", "shocked"),
                        prop=char_ctx.get("prop", "bill"),
                        character_side=side,
                        setting=getattr(seg, "setting", config.topic),
                    )
                )
    elif not scenes:
        try:
            from autoshorts.longform.wojak_script import build_wojak_script

            script = build_wojak_script(topic=config.topic)
            title = script.title
            disclaimer_text = script.disclaimer_text
            disclaimer_heading = script.disclaimer_heading
            scenes = []
            for idx, seg in enumerate(script.segments):
                side = "left" if idx % 2 == 0 else "right"
                char_ctx = seg.character_context or {}
                scenes.append(
                    WojakScene(
                        country=seg.heading or seg.country,
                        caption=seg.joke_text,
                        emotion=char_ctx.get("expression", "shocked"),
                        prop=char_ctx.get("prop", "bill"),
                        character_side=side,
                        setting=seg.setting,
                    )
                )
        except Exception as s_exc:
            logger.debug("Falling back to local curated topic scenes: %s", s_exc)
            title, scenes = resolve_topic_scenes(config.topic)

    scene_count = len(scenes)
    fps = config.fps
    disclaimer_dur = config.disclaimer_duration
    scene_dur = config.scene_duration
    total_duration = disclaimer_dur + scene_count * scene_dur
    total_frames = int(fps * disclaimer_dur) + sum(int(fps * scene_dur) for _ in scenes)

    logger.info(
        "Starting Wojak video pipeline: '%s' (%d scenes, %.1fs total, %d frames @ %dfps)",
        title,
        scene_count,
        total_duration,
        total_frames,
        fps,
    )

    # 2. Scene transition timestamps for SFX cues
    transition_timestamps: list[float] = [0.0]  # Disclaimer start
    curr_t = disclaimer_dur
    for _ in scenes:
        transition_timestamps.append(curr_t)
        curr_t += scene_dur

    # 3. Assemble and master audio track
    audio_path = config.output_dir / "master_audio.wav"
    assemble_wojak_audio(
        total_duration_sec=total_duration,
        scene_transition_timestamps=transition_timestamps,
        music_file_path=config.music_file_path,
        output_wav_path=audio_path,
    )

    # 4. Set up FFmpeg raw video streaming subprocess
    output_video_path = config.output_dir / "wojak_longform.mp4"
    ffmpeg_bin = get_ffmpeg_path()

    ffmpeg_cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-s",
        f"{FRAME_WIDTH}x{FRAME_HEIGHT}",
        "-pix_fmt",
        "rgb24",
        "-r",
        str(fps),
        "-i",
        "-",  # Read raw RGB24 video from stdin pipe
        "-i",
        str(audio_path),  # Mastered audio track
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(output_video_path),
    ]

    logger.info("Spawning streaming FFmpeg process...")
    proc = subprocess.Popen(
        ffmpeg_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if proc.stdin is None:
        raise RuntimeError("Failed to open FFmpeg stdin pipe")

    # 5. Stream Scene 0: Disclaimer Card
    disclaimer_frames = int(fps * disclaimer_dur)
    logger.info("Streaming Scene 0 (Disclaimer, %d frames)...", disclaimer_frames)
    disclaimer_img = render_disclaimer_frame(
        disclaimer_text=disclaimer_text,
        warning_badge=disclaimer_heading,
    )
    disclaimer_raw = disclaimer_img.tobytes()
    for _ in range(disclaimer_frames):
        proc.stdin.write(disclaimer_raw)

    # 6. Stream Scenes 1 to N: Country Meme Scenes
    # Pre-render each scene once and write identical frame bytes fps*dur times
    for s_idx, scene in enumerate(scenes):
        logger.info(
            "Streaming Scene %d/%d (%s, %d frames)...",
            s_idx + 1,
            scene_count,
            scene.country,
            int(fps * scene_dur),
        )

        # Resolve background
        _t_bg = time.perf_counter()
        bg_img = get_wojak_background(
            country=scene.country,
            setting=scene.setting,
            custom_photo_path=scene.custom_photo_path,
        )
        stage_secs["background"] += time.perf_counter() - _t_bg

        # Resolve character
        char_img: Image.Image | None = None
        _t_char = time.perf_counter()
        try:
            # 1. Attempt on-demand single image fetch via Playwright browser without storing on disk
            try:
                from autoshorts.longform.wojak_library import load_library_character
                from autoshorts.longform.wojak_temp_assets import (
                    category_from_context,
                    choose_category_with_llm,
                    temporary_wojak_asset,
                )

                cat = category_from_context(
                    expression=scene.emotion,
                    gender=scene.gender,
                    clothing=scene.clothing,
                    prop=scene.prop,
                    heading=scene.country,
                )
                if not cat:
                    cat = choose_category_with_llm(f"{scene.country} {scene.caption}")
                if cat:
                    with temporary_wojak_asset(cat) as temp_p:
                        if temp_p and temp_p.is_file():
                            char_img = load_library_character(temp_p)
            except Exception as play_exc:
                logger.debug("On-demand Playwright asset fetch bypassed: %s", play_exc)

            # 2. If not fetched from web/drive, resolve via local indexed library or procedural drawing
            if char_img is None:
                char_png_path = wojak_for_context(
                    country=scene.country,
                    setting=scene.setting,
                    scenario=scene.caption,
                    expression=scene.emotion,
                    prop=scene.prop,
                    gender=scene.gender,
                    race=scene.race,
                    height=scene.height,
                    age=scene.age,
                    clothing=scene.clothing,
                    hair=scene.hair,
                )
                if char_png_path and Path(char_png_path).is_file():
                    with Image.open(char_png_path) as c_file:
                        char_img = c_file.convert("RGBA")
        except Exception as c_exc:
            logger.warning("Character resolution fallback for %s: %s", scene.country, c_exc)
        stage_secs["character"] += time.perf_counter() - _t_char

        # Stream raw bytes into FFmpeg pipe with cinematic Ken-Burns camera breathing
        num_frames = int(fps * scene_dur)
        step_frames = (
            4  # Refresh camera push every 4 frames (7.5 fps motion rate) for speed + smoothness
        )
        f_idx = 0
        while f_idx < num_frames:
            prog = f_idx / max(1, num_frames - 1)
            frame_img = render_wojak_scene_frame(
                country=scene.country,
                caption=scene.caption,
                background_img=bg_img,
                character_img=char_img,
                character_side=scene.character_side,
                progress=prog,
            )
            raw_bytes = frame_img.tobytes()
            repeat = min(step_frames, num_frames - f_idx)
            for _ in range(repeat):
                proc.stdin.write(raw_bytes)
            f_idx += repeat

    # Close stdin and wait for encoding
    proc.stdin.close()
    stdout, stderr = proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="replace") if stderr else "Unknown error"
        logger.error("FFmpeg streaming encoding failed: %s", err_msg)
        raise RuntimeError(
            f"FFmpeg encoding failed with exit code {proc.returncode}: {err_msg[:400]}"
        )

    elapsed = time.perf_counter() - start_time
    streaming_fps = total_frames / max(0.001, elapsed)

    logger.info(
        "Successfully streamed & encoded video in %.2fs (%.1f fps effective): %s",
        elapsed,
        streaming_fps,
        output_video_path,
    )
    logger.info(
        "Stage timings: background %.2fs, character %.2fs, compositing+encoding %.2fs (total %.2fs)",
        stage_secs["background"],
        stage_secs["character"],
        max(0.0, elapsed - stage_secs["background"] - stage_secs["character"]),
        elapsed,
    )

    # 7. Generate High-CTR Thumbnail
    thumb_path: Path | None = None
    if config.generate_thumbnail:
        thumb_path = config.output_dir / "thumbnail.jpg"
        # Extract a shocked character if available
        first_char = None
        try:
            p = wojak_for_context(
                country="USA",
                setting="hospital",
                scenario="bill invoice ambulance hospital",
                expression="shocked",
            )
            if p and Path(p).is_file():
                with Image.open(p) as cf:
                    first_char = cf.convert("RGBA")
        except Exception as thumb_exc:
            logger.debug(
                "Thumbnail character unavailable, using default art: %s",
                thumb_exc,
                exc_info=True,
            )

        generate_wojak_thumbnail(
            topic_title=title.upper(),
            left_badge="USA: $2,500,000?!",
            right_badge="VS FREE?!",
            character_img=first_char,
            output_path=thumb_path,
            setting=scenes[0].setting if scenes else "hospital",
        )

    purge_remote_tmp()
    return WojakPipelineResult(
        video_path=output_video_path,
        thumbnail_path=thumb_path,
        audio_path=audio_path,
        title=title,
        topic=config.topic,
        duration_sec=total_duration,
        scene_count=scene_count,
        frame_count=total_frames,
        render_time_sec=elapsed,
        fps=fps,
        streaming_fps=streaming_fps,
    )
