"""Fictional Reddit-style story Shorts (sad / funny / angry) over gameplay footage.

Generates a first-person fictional story, voices it (Edge neural TTS or local
pyttsx3), and hands a draft Job to the standard subtitles + compositing stages.
Jobs always stop at READY_FOR_REVIEW: nothing is uploaded or scheduled here.
"""

from __future__ import annotations

import json
import logging
import random
import re
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from autoshorts.core.models import (
    ClipCandidate,
    ClipSelection,
    EditDecisionList,
    Job,
    JobStatus,
    VideoMetadata,
)
from autoshorts.core.paths import get_work_dir
from autoshorts.media.dopamine_effects import pick_background_asset
from autoshorts.research_agent.moneyprinter import synthesize_neural_tts

logger = logging.getLogger("autoshorts.research_agent.story_shorts")

STORY_MOODS = ("sad", "funny", "angry", "emotional", "advice", "heartbreak")
STORY_PROVIDERS = ("api", "local")
FICTION_DISCLAIMER = (
    "Fictional story written for entertainment. Names, places and events are made up."
)
BACKGROUND_LINE = "Background: gameplay footage."

# The compositor caps a render at 60 s, so a longer narration would be cut off.
MIN_WORDS = 135
MAX_WORDS = 170
MIN_DURATION = 30.0
MAX_DURATION = 60.0

# One bright, fast female voice for every mood: short Shorts need energy to hold attention.
STORY_VOICE = "en-US-JennyNeural"
STORY_RATE = "+25%"
STORY_PITCH = "+6Hz"
MOOD_VOICES = {mood: STORY_VOICE for mood in ("sad", "funny", "angry", "emotional", "advice", "heartbreak")}

CURATED_STORIES: dict[str, dict[str, str]] = {
    "sad": {
        "title": "The Voicemail I Never Deleted",
        "hook": "I kept my grandfather's last voicemail for three years without playing it.",
        "narration": (
            "I kept my grandfather's last voicemail for three years without playing it. Every "
            "time my phone warned me about storage, I deleted photos and apps, but never that "
            "message. Last week I finally pressed play, sitting alone in my car outside the "
            "grocery store. His voice was cracked and cheerful. He was calling to remind me "
            "to wear a jacket because the weather had turned cold. That was it. No big "
            "goodbye, no wise last words, just a man who worried about me catching a chill. I "
            "laughed first, then I cried so hard the windows fogged up. Today I wore the "
            "jacket. It was too warm outside, but I kept it on all day anyway. If someone you "
            "love is still a phone call away, call them. Say the ordinary things. Some nights "
            "I still hear his voice telling me to wear my jacket."
        ),
    },
    "funny": {
        "title": "I Accidentally Joined the Wrong Wedding",
        "hook": "I ate dinner at a stranger's wedding and ended up giving the toast.",
        "narration": (
            "I ate dinner at a stranger's wedding and ended up giving the toast. I walked "
            "into a hotel ballroom looking for my cousin's birthday party, and a nice lady "
            "handed me a name card, so I assumed I was in the right place. I ate three bread "
            "rolls and chatted with a man named Gerald about his boat. Then the microphone "
            "landed in my hands. Somebody shouted, speech from the best friend of the groom. "
            "I froze. So I said, Gerald talks about boats the way other people talk about "
            "love. The room exploded, because Gerald was the groom's father and it was "
            "painfully true. The bride hugged me. Later I found out my cousin's party was on "
            "the floor below, and they had saved me cake. Gerald now texts me boat pictures "
            "every single week, and honestly, I love it."
        ),
    },
    "angry": {
        "title": "My Neighbor Kept Stealing My Parking Spot",
        "hook": "My neighbor stole my parking spot for a whole year, and then I snapped.",
        "narration": (
            "My neighbor stole my parking spot for a whole year, and then I snapped. I pay "
            "extra for that numbered space. It is written in my lease. Every evening I came "
            "home exhausted and found his shiny car parked there like he owned the place. I "
            "left polite notes. I knocked on his door. He just smirked and said, first come, "
            "first served. It was not first come. I called the property manager six times, "
            "and each time they promised to look into it and did nothing. So I documented "
            "everything with dated photos, sent one formal complaint, and asked for the "
            "towing company's number. Two days later his car was gone. He knocked on my door "
            "asking who called the tow. I told him, the rules did. Nobody parks in my space "
            "anymore, and I still smile every time I pull in."
        ),
    },
    "emotional": {
        "title": "The Stranger Who Paid for My Groceries Changed My Whole Year",
        "hook": "A stranger paid for my groceries when I was eleven dollars short, and I still think about her every week.",
        "narration": (
            "A stranger paid for my groceries when I was eleven dollars short, and I still think about her every week. "
            "It was a Tuesday, and I was standing at the register counting coins, face burning. I was too proud to put "
            "anything back. The woman behind me stepped up and said, Put it on mine. I tried to refuse. She just smiled and "
            "said, Someday somebody will do this for you, and you will remember to do it for someone else. I never got her "
            "name. I went home and cried in my car, not because I was sad, but because I felt seen. I started volunteering "
            "at the food bank on Saturdays. Last week a young man at the same register was short by three dollars. I stepped "
            "up without thinking and said, Put it on mine. His shoulders dropped, and he thanked me like I had saved his day. "
            "That is how kindness works. It only travels when someone passes it on."
        ),
    },
    "advice": {
        "title": "Stop Saying Yes When You Mean No. Here Is What It Costs You",
        "hook": "If you keep saying yes when you mean no, you are not being nice. You are slowly losing your own life.",
        "narration": (
            "If you keep saying yes when you mean no, you are not being nice. You are slowly losing your own life. I learned "
            "this the hard way. For years I said yes to extra shifts, weekend favors, and friends who always needed a ride. "
            "Every yes felt like a small win, until I had no time left for my own goals. I was tired, resentful, and quietly "
            "angry at everyone. Then I tried a new rule. Before I answer, I count to five and ask myself one question. Do I "
            "want to do this, or do I just want to be liked? If the answer is no, I say, Let me check my schedule and get "
            "back to you. That small pause saved me. I still help people. I just choose when. Try it this week. Say no once, "
            "and notice how the world does not end. Your time is not a prize you hand out to whoever asks loudest."
        ),
    },
    "heartbreak": {
        "title": "He Said He Loved Me on Monday and Blocked Me on Tuesday",
        "hook": "On Monday he told me he loved me. On Tuesday my texts stopped going through, and I had no idea why.",
        "narration": (
            "On Monday he told me he loved me. On Tuesday my texts stopped going through, and I had no idea why. I called "
            "from three different numbers. Nothing. I went to his apartment and his car was gone. I spent that night "
            "rereading our messages, looking for the moment I had ruined everything. There was no moment. Two weeks later a "
            "mutual friend told me the truth. He had been talking to someone else since January. He had only told me he loved "
            "me because he wanted one more good memory before he ended it. I was furious at first, then just empty. Here is "
            "what helped. I stopped searching for the reason. A person who cannot tell you the truth is not someone you need "
            "to understand. He was never the answer. He was just the lesson."
        ),
    },
}


@dataclass
class StoryDraft:
    mood: str
    title: str
    hook: str
    narration: str
    description: str
    provider: str


def _description() -> str:
    return f"{FICTION_DISCLAIMER}\n{BACKGROUND_LINE}"


def normalize_mood(mood: str | None, rng: random.Random | None = None) -> str:
    if mood is None or not str(mood).strip():
        return (rng or random).choice(STORY_MOODS)
    value = str(mood).strip().lower()
    if value not in STORY_MOODS:
        raise ValueError(f"Unknown story mood {mood!r}; expected one of {STORY_MOODS}")
    return value


def normalize_provider(provider: str | None) -> str:
    if provider is None or not str(provider).strip():
        return "api"
    value = str(provider).strip().lower()
    if value not in STORY_PROVIDERS:
        raise ValueError(f"Unknown story provider {provider!r}; expected one of {STORY_PROVIDERS}")
    return value


def build_story_prompt(mood: str, topic: str | None) -> str:
    mood = normalize_mood(mood)
    topic_line = f"Story topic or seed idea: {topic.strip()}\n" if topic and topic.strip() else ""
    return (
        f"Write a fictional Reddit-style first-person story with a {mood} mood, "
        "meant to be narrated aloud in a YouTube Short.\n"
        f"{topic_line}"
        f"The narration must be between {MIN_WORDS} and {MAX_WORDS} words. "
        "Open with a strong one-sentence hook. Use invented names and places only; do not "
        "mention real people, brands, or subreddits. Plain spoken sentences, no emojis, "
        "no stage directions.\n"
        "Respond ONLY with JSON having exactly the keys title, hook, narration."
    )


def parse_story_response(raw: str, mood: str, provider: str) -> StoryDraft:
    text = (raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"Story response is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Story response JSON must be an object")
    fields: dict[str, str] = {}
    for key in ("title", "hook", "narration"):
        val = data.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"Story response missing non-empty '{key}'")
        fields[key] = val.strip()
    count = len(fields["narration"].split())
    if not MIN_WORDS <= count <= MAX_WORDS:
        raise ValueError(f"Narration has {count} words; expected {MIN_WORDS}-{MAX_WORDS}")
    return StoryDraft(
        mood=normalize_mood(mood),
        title=fields["title"],
        hook=fields["hook"],
        narration=fields["narration"],
        description=_description(),
        provider=provider,
    )


def _curated_draft(mood: str, provider: str) -> StoryDraft:
    story = CURATED_STORIES[mood]
    return StoryDraft(
        mood=mood,
        title=story["title"],
        hook=story["hook"],
        narration=story["narration"],
        description=_description(),
        provider=provider,
    )


def generate_story(
    mood: str,
    topic: str | None = None,
    provider: str = "api",
    config: Any = None,
) -> StoryDraft:
    mood = normalize_mood(mood)
    provider = normalize_provider(provider)
    if config is None:
        logger.warning("No config supplied; using curated %s story.", mood)
        return _curated_draft(mood, provider)

    prompt = build_story_prompt(mood, topic)
    try:
        from autoshorts.core.providers import get_provider

        if provider == "local":
            model_path = Path(str(getattr(config.llm, "local_model_path", "") or ""))
            if not model_path.is_file():
                logger.warning(
                    "Local story model not found at %s; using curated %s story instead.",
                    model_path,
                    mood,
                )
                return _curated_draft(mood, provider)
            llm = get_provider(name="local", settings=config)
        else:
            llm = get_provider(name="gemini", settings=config)
        raw = llm.complete(prompt)
        return parse_story_response(raw, mood, provider)
    except Exception as exc:
        logger.warning("Story generation via %s failed (%s); using curated story.", provider, exc)
        return _curated_draft(mood, provider)


def _synthesize_local(narration: str, out_wav: Path) -> None:
    try:
        import pyttsx3  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("pip install pyttsx3 to use the local voice") from exc
    engine = pyttsx3.init()
    engine.save_to_file(narration, str(out_wav))
    engine.runAndWait()


def synthesize_story_voice(
    narration: str,
    mood: str,
    provider: str,
    work_dir: Path,
) -> tuple[Path, list[dict[str, Any]], float]:
    mood = normalize_mood(mood)
    provider = normalize_provider(provider)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    words_path = work_dir / "aligned_words.json"

    if provider == "api":
        audio_path, words, duration = synthesize_neural_tts(
            text=narration,
            voice=MOOD_VOICES[mood],
            output_audio_path=work_dir / "story_voice.mp3",
            output_words_path=words_path,
            rate=STORY_RATE,
            pitch=STORY_PITCH,
        )
    else:
        audio_path = work_dir / "story_voice.wav"
        _synthesize_local(narration, audio_path)
        if not audio_path.is_file():
            raise RuntimeError("Local voice produced no audio file")
        from autoshorts.media.compositor import FasterWhisperTranscriber

        words = FasterWhisperTranscriber().align_words(audio_path)
        with wave.open(str(audio_path), "rb") as wf:
            duration = wf.getnframes() / float(wf.getframerate() or 1)
        words_path.write_text(json.dumps(words), encoding="utf-8")

    duration = float(duration)
    if not MIN_DURATION <= duration <= MAX_DURATION:
        raise ValueError(
            f"Narration audio is {duration:.1f}s; expected {MIN_DURATION:.0f}-{MAX_DURATION:.0f}s"
        )
    return Path(audio_path), list(words), duration


def create_story_job(
    *,
    mood: str | None,
    topic: str | None,
    provider: str | None,
    pipe: Any,
    state_mgr: Any,
    draft: StoryDraft | None = None,
) -> Job:
    """Build one story Short. Pass ``draft`` to use a written story instead of generating one."""
    mood = normalize_mood(mood)
    provider = normalize_provider(provider)
    config = getattr(pipe, "config", None)
    if draft is None:
        draft = generate_story(mood, topic, provider, config)
    mood = normalize_mood(draft.mood)

    job_id = f"story_{uuid4().hex[:10]}"
    work_dir = get_work_dir() / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    audio_path, words, duration = synthesize_story_voice(
        draft.narration, draft.mood, provider, work_dir
    )
    # The subtitles stage reads this cache; keep it authoritative for both providers.
    (work_dir / "aligned_words.json").write_text(json.dumps(words), encoding="utf-8")

    bg_file, _ = pick_background_asset(category="subway", auto_fetch=False)
    section_path = work_dir / f"section_{job_id}.mp4"
    if bg_file and Path(bg_file).is_file():
        mux_cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(bg_file),
            "-i", str(audio_path), "-t", str(duration),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "192k", "-shortest", str(section_path),
        ]  # fmt: skip
    else:
        mux_cmd = [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=0x0f172a:s=1080x1920:r=30:d={duration}",
            "-i", str(audio_path), "-t", str(duration),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", str(section_path),
        ]  # fmt: skip
    try:
        proc = subprocess.run(
            mux_cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=180.0,
            check=False,
        )
        if proc.returncode != 0 or not section_path.is_file():
            logger.warning("Story mux failed: %s", (proc.stderr or "")[-300:])
            section_path.touch()
    except Exception as exc:
        logger.warning("Story mux failed (%s); creating empty section.", exc)
        section_path.touch()

    candidate = ClipCandidate(
        start=0.0, end=duration, text=draft.narration, score=1.0, reason=f"story:{draft.mood}"
    )
    selection = ClipSelection(
        candidate=candidate,
        clip_index=0,
        target_duration=duration,
        background_style="subway",
        hook_text=draft.hook,
        title=draft.title,
    )
    edl = EditDecisionList(
        hook_in_sec=0.0,
        hook_out_sec=duration,
        recommended_title=draft.title[:60],
        recommended_hook=draft.hook[:100],
        background_style_recommendation="subway",
    )
    metadata = VideoMetadata(
        title=draft.title,
        channel="AutoShorts Stories",
        channel_url="https://youtube.com",
        video_url=f"story://{job_id}",
        duration=duration,
        video_id=job_id,
        description=draft.description,
    )
    job = Job(
        id=job_id,
        source_url=f"story://{job_id}",
        custom_instructions=f"Story Short ({draft.mood}): {draft.title}",
        status=JobStatus.DOWNLOADING_SECTION,
        video_metadata=metadata,
        candidate_windows=[candidate],
        selected_clips=[selection],
        edl=edl,
        raw_video_path=str(section_path),
        style_preset="hormozi",
        layout_mode="full_center_crop",
        custom_tags=["Shorts", "Story", "Fiction"],
    )
    state_mgr.save_job(job)
    pipe._run_subtitles_stage(job, use_cached_words=True)
    pipe._run_compositing_stage(job, background_category="subway")
    state_mgr.save_job(job)
    return job


__all__ = [
    "CURATED_STORIES",
    "FICTION_DISCLAIMER",
    "STORY_MOODS",
    "STORY_PROVIDERS",
    "StoryDraft",
    "build_story_prompt",
    "create_story_job",
    "generate_story",
    "normalize_mood",
    "normalize_provider",
    "parse_story_response",
    "synthesize_story_voice",
]
