"""Autonomous Faceless AI Shorts Studio Engine.

Generates viral faceless video scripts with anti-duplication memory, studio-grade
neural voiceovers with exact word-level timing via Microsoft Edge TTS (edge-tts),
and integrates with AutoShorts pure FFmpeg 1080x1920 multi-layout compositor.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import math
import random
import re
import struct
import subprocess
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from autoshorts.core.config import Settings
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
from autoshorts.research_agent.models import FacelessScriptResult, FacelessShortRequest

logger = logging.getLogger("autoshorts.research_agent.moneyprinter")

VOICE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "en-US-ChristopherNeural",
        "name": "Christopher (US Male - Authority / Deep)",
        "language": "en",
        "gender": "male",
        "preview_tag": "Top Pick for Tech, Finance & Wealth",
    },
    {
        "id": "en-US-GuyNeural",
        "name": "Guy (US Male - Conversational / Friendly)",
        "language": "en",
        "gender": "male",
        "preview_tag": "Great for Casual Podcasts & Stories",
    },
    {
        "id": "en-US-AriaNeural",
        "name": "Aria (US Female - Engaging / Dynamic)",
        "language": "en",
        "gender": "female",
        "preview_tag": "High Energy for Viral Hacks & Facts",
    },
    {
        "id": "en-US-JennyNeural",
        "name": "Jenny (US Female - Clear / Professional)",
        "language": "en",
        "gender": "female",
        "preview_tag": "Professional for Business & Tutorials",
    },
    {
        "id": "en-GB-RyanNeural",
        "name": "Ryan (British Male - Sophisticated / Docu)",
        "language": "en",
        "gender": "male",
        "preview_tag": "Perfect for Science & Astrophysics",
    },
    {
        "id": "es-ES-AlvaroNeural",
        "name": "Alvaro (Spanish Male - Energetic)",
        "language": "es",
        "gender": "male",
        "preview_tag": "Spanish Tech, Business & Motivation",
    },
    {
        "id": "hi-IN-MadhurNeural",
        "name": "Madhur (Hindi Male - Expressive)",
        "language": "hi",
        "gender": "male",
        "preview_tag": "Hindi Tech, Coding & Facts",
    },
    {
        "id": "de-DE-ConradNeural",
        "name": "Conrad (German Male - Authoritative)",
        "language": "de",
        "gender": "male",
        "preview_tag": "German Engineering & Economics",
    },
    {
        "id": "fr-FR-HenriNeural",
        "name": "Henri (French Male - Smooth)",
        "language": "fr",
        "gender": "male",
        "preview_tag": "French Luxury & Mindset",
    },
]

# Rich multi-topic templates for deterministic script generation
NICHE_TOPIC_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "ai_tech": [
        {
            "topic": "The 3 Secret AI Websites Developers Keep Hidden",
            "hook": "If you are not using this AI workflow in 2026, you are wasting 4 hours every single day.",
            "sentences": [
                "If you are not using this AI workflow in 2026, you are wasting 4 hours every single day.",
                "First, Bolt and v0 allow anyone to prompt a full-stack web application into existence in under 60 seconds.",
                "Second, Cursor and Claude 3.5 Sonnet can refactor legacy codebases better than most senior engineers.",
                "Third, Perplexity Pro gives you verified citations so you never get hallucinated research papers again.",
                "The real secret isn't knowing how to code anymore.",
                "It is knowing how to orchestrate autonomous AI agents to do the heavy lifting for you.",
                "Save this video right now before your competitors discover it.",
            ],
            "title": "3 AI Tools That Replace 40 Hours of Work #shorts #ai #tech",
            "description": "These 3 autonomous AI tools are replacing traditional coding and research workflows. Which one is your favorite?",
            "hashtags": [
                "#ai",
                "#techtok",
                "#artificialintelligence",
                "#productivity",
                "#coding",
                "#shorts",
            ],
            "search_keywords": [
                "artificial intelligence",
                "coding software",
                "cyberpunk neon code",
                "futuristic robot",
            ],
        },
        {
            "topic": "What OpenAI Does Not Want You To Know About GPT-5",
            "hook": "OpenAI just quietly updated their system prompt, and the implications are terrifying.",
            "sentences": [
                "OpenAI just quietly updated their system prompt, and the implications are terrifying.",
                "Internal benchmarks show the newest reasoning models solve PhD-level physics problems in seconds.",
                "Instead of predicting the next token, these systems generate full trees of thought before answering.",
                "This means autonomous agents can now write, debug, and deploy entire apps without human oversight.",
                "The gap between people who use AI and people who do not is widening every week.",
                "Drop a comment if you think human programmers will exist by 2030.",
            ],
            "title": "The Terrifying Truth About Next-Gen AI #shorts #ai #future",
            "description": "Next-gen AI reasoning models are changing the game. Are you prepared?",
            "hashtags": ["#ai", "#openai", "#gpt5", "#technology", "#future", "#shorts"],
            "search_keywords": [
                "supercomputer server room",
                "digital brain",
                "ai neural network",
                "robot looking at screen",
            ],
        },
    ],
    "finance": [
        {
            "topic": "The Wealth Rule The Top 1% Never Tell You",
            "hook": "Keeping more than 6 months of expenses in a savings account is making you poorer every day.",
            "sentences": [
                "Keeping more than 6 months of expenses in a savings account is making you poorer every day.",
                "Because of real inflation, the dollar loses purchasing power every single year you leave it idle.",
                "The wealthy don't hoard cash in banks.",
                "They use cash flow to buy income-producing assets like dividend indexes, real estate, and digital IP.",
                "Then, they borrow against those assets tax-free to fund their lifestyles.",
                "That is the difference between working for money, and having your money work for you.",
                "Follow for more wealth secrets the school system never taught you.",
            ],
            "title": "Why Your Savings Account Is a Trap #shorts #money #wealth",
            "description": "Why holding idle cash destroys wealth through inflation. The 1% blueprint explained in 40 seconds.",
            "hashtags": [
                "#finance",
                "#moneytips",
                "#investing",
                "#wealth",
                "#passiveincome",
                "#shorts",
            ],
            "search_keywords": [
                "money cash falling",
                "luxury skyscraper",
                "stock chart rising",
                "gold bars",
            ],
        },
        {
            "topic": "How To Turn $100 A Month Into $1,000,000",
            "hook": "You do not need to be a Wall Street genius to retire as a multi-millionaire.",
            "sentences": [
                "You do not need to be a Wall Street genius to retire as a multi-millionaire.",
                "If an 18-year-old invests just $100 every month into the S&P 500, compounding works miracles.",
                "At the historical 10% average annual return, that modest contribution grows to over 1.2 million dollars.",
                "You only deposited about 50,000 dollars out of pocket over that entire lifetime.",
                "The remaining 1.1 million dollars is pure compound interest created out of thin air.",
                "Start today, because the biggest cost in investing is waiting.",
            ],
            "title": "How $100/mo Makes You a Millionaire #shorts #finance",
            "description": "The math behind compound interest in the S&P 500. Start investing early.",
            "hashtags": [
                "#finance",
                "#compoundinterest",
                "#investing101",
                "#millionairemindset",
                "#shorts",
            ],
            "search_keywords": [
                "compound interest graph",
                "calculator counting cash",
                "green stock candles",
                "city skyline",
            ],
        },
    ],
    "business": [
        {
            "topic": "How To Build A $10,000/Month One-Person Business",
            "hook": "You don't need venture capital or employees to build a 6-figure business anymore.",
            "sentences": [
                "You don't need venture capital or employees to build a 6-figure business anymore.",
                "In 2026, a solo founder using AI automations can outcompete a 20-person agency from 5 years ago.",
                "Step one: pick a boring, expensive problem businesses face every day.",
                "Step two: package the solution into a standardized monthly subscription.",
                "Step three: automate your outreach and fulfillment using modern AI tools.",
                "With just 10 clients paying $1,000 a month, you've unlocked complete location and financial freedom.",
                "Save this blueprint and get to work.",
            ],
            "title": "How To Make $10k/Mo As A Solo Founder #shorts #business",
            "description": "The modern blueprint for building a 1-person software or automation agency.",
            "hashtags": [
                "#business",
                "#entrepreneur",
                "#sidehustle",
                "#solopreneur",
                "#saas",
                "#shorts",
            ],
            "search_keywords": [
                "laptop workspace coffee",
                "entrepreneur working late",
                "modern luxury office",
                "digital nomad",
            ],
        },
    ],
    "health": [
        {
            "topic": "The 90-Minute Morning Rule For All-Day Energy",
            "hook": "If you drink coffee right after waking up, you are sabotaging your energy for the entire day.",
            "sentences": [
                "If you drink coffee right after waking up, you are sabotaging your energy for the entire day.",
                "When you wake up, your body naturally clears out adenosine, the chemical that makes you feel drowsy.",
                "If you flood your receptors with caffeine immediately, that adenosine stays trapped.",
                "As soon as the caffeine wears off around 2 PM, all that stored fatigue crashes over you at once.",
                "Instead, delay your first cup by 90 minutes and get 10 minutes of direct sunlight into your eyes.",
                "Try this protocol for just 3 days and feel the difference.",
            ],
            "title": "Why You Must Delay Morning Coffee #shorts #health",
            "description": "Neuroscientist Andrew Huberman's 90-minute caffeine delay protocol for zero afternoon crashes.",
            "hashtags": ["#health", "#biohacking", "#energy", "#hubermanlab", "#sleep", "#shorts"],
            "search_keywords": [
                "morning sunrise sunlight",
                "espresso brewing",
                "brain neural scan",
                "running athlete",
            ],
        },
    ],
    "science": [
        {
            "topic": "The Mind-Bending Truth About Black Holes",
            "hook": "What actually happens if you fall into a supermassive black hole? It defies reality.",
            "sentences": [
                "What actually happens if you fall into a supermassive black hole? It defies reality.",
                "As you cross the event horizon, time and space literally swap coordinate roles.",
                "Moving forward in time becomes mathematically identical to moving toward the singularity center.",
                "To an outside observer watching from Earth, you appear to slow down and freeze forever at the edge.",
                "But from your perspective, you watch the entire future history of the universe speed up in seconds.",
                "The laws of physics break down completely at the core, pointing to physics we haven't discovered yet.",
                "Subscribe to unlock more cosmic mysteries.",
            ],
            "title": "Falling Into a Black Hole Looks Like This #shorts #space",
            "description": "General relativity explains black hole time dilation and spaghettification.",
            "hashtags": ["#science", "#space", "#astronomy", "#blackhole", "#physics", "#shorts"],
            "search_keywords": [
                "black hole accretion disk",
                "deep space nebula",
                "james webb telescope",
                "galaxy rotation",
            ],
        },
    ],
    "self_improvement": [
        {
            "topic": "The Stoic Hack To Stop Caring What Anyone Thinks",
            "hook": "Marcus Aurelius wrote this in his private journal 2,000 years ago, and it still hits hard today.",
            "sentences": [
                "Marcus Aurelius wrote this in his private journal 2,000 years ago, and it still hits hard today.",
                "He wrote: We all love ourselves more than others, but we care more about their opinion than our own.",
                "Think about how absurd that is.",
                "Most people spend their entire lives trying to impress strangers who don't even care about them.",
                "Next time someone criticizes you, remember this:",
                "Their judgment is a reflection of their own internal chaos, not your worth.",
                "Protect your peace and focus on your mission.",
            ],
            "title": "Marcus Aurelius on Not Caring What People Think #shorts",
            "description": "Timeless Stoic philosophy for modern mental clarity and emotional resilience.",
            "hashtags": [
                "#stoicism",
                "#marcusaurelius",
                "#mindset",
                "#philosophy",
                "#discipline",
                "#shorts",
            ],
            "search_keywords": [
                "roman marble statue",
                "ancient ruins sunlight",
                "man walking in rain",
                "dramatic clouds",
            ],
        },
    ],
}


YOUTUBE_TITLE_MAX_CHARS = 100

# Upper bound for one edge-tts synthesis before falling back to Kokoro / offline audio.
_TTS_TIMEOUT_SEC = 45.0


def _clamp_title(title: str, max_len: int = YOUTUBE_TITLE_MAX_CHARS) -> str:
    """Collapse whitespace and cut an over-long LLM title to YouTube's 100-character limit."""
    cleaned = " ".join(title.split())
    if len(cleaned) <= max_len:
        return cleaned
    cut = cleaned[:max_len]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" -|:,.!?") or cleaned[:max_len]


def _text_items(value: Any) -> list[str]:
    """Return non-empty strings from a list, or from a comma/newline separated string."""
    if isinstance(value, str):
        parts = re.split(r"[,\n]", value)
    elif isinstance(value, list | tuple):
        parts = [str(x) for x in value]
    else:
        return []
    return [p.strip() for p in parts if p.strip()]


def _normalize_hashtags(value: Any) -> list[str]:
    """Return unique hashtags that start with '#' and contain no whitespace."""
    tags: list[str] = []
    seen: set[str] = set()
    for raw in _text_items(value):
        body = re.sub(r"\s+", "", raw.lstrip("#"))
        if not body:
            continue
        tag = f"#{body}"
        if tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return tags


def _normalize_llm_script(
    data: dict[str, Any], chosen_topic: str, template: dict[str, Any]
) -> FacelessScriptResult | None:
    """Build a script result from LLM JSON, bounding every field; None if the core fields are unusable.

    Missing or malformed optional fields fall back to the curated template instead of discarding
    the whole LLM script.
    """
    raw_sentences = data.get("script_sentences")
    if not isinstance(raw_sentences, list):
        return None
    sentences = [str(x).strip() for x in raw_sentences if str(x).strip()]
    if not sentences:
        return None
    hook = str(data.get("hook") or "").strip() or str(template["hook"])
    title = _clamp_title(str(data.get("title") or "")) or str(template["title"])
    description = str(data.get("description") or "").strip() or str(template["description"])
    hashtags = _normalize_hashtags(data.get("hashtags")) or list(template["hashtags"])
    keywords = _text_items(data.get("search_keywords")) or list(template["search_keywords"])
    return FacelessScriptResult(
        topic=chosen_topic,
        hook=hook,
        script_sentences=sentences,
        full_script=" ".join(sentences),
        title=title,
        description=description,
        hashtags=hashtags,
        search_keywords=keywords,
    )


def generate_faceless_script(
    niche: str,
    topic: str | None = None,
    language: str = "en",
    config: Settings | None = None,
    past_topics: Sequence[str] = (),
) -> FacelessScriptResult:
    """Generate a high-retention faceless script and SEO metadata without repetition."""
    norm_niche = niche.lower().replace("-", "_").replace(" ", "_")
    templates = NICHE_TOPIC_TEMPLATES.get(norm_niche)
    if not templates:
        templates = NICHE_TOPIC_TEMPLATES.get("ai_tech", [])

    # Filter out already used topics to guarantee anti-duplication
    available = [t for t in templates if t["topic"] not in past_topics]
    if not available:
        available = templates

    chosen_template = random.choice(available) if available else templates[0]

    # If user provided custom topic or LLM is configured, try dynamic generation
    if topic and topic.strip():
        chosen_topic = topic.strip()
    else:
        chosen_topic = chosen_template["topic"]

    # Attempt LLM script generation if provider is configured and available
    if config and getattr(config, "llm", None):
        try:
            from autoshorts.core.providers import get_provider

            provider = get_provider(name=config.llm.provider, settings=config)
            prompt = (
                f"You are FujiwaraChoki-style high-retention YouTube Shorts scriptwriter. "
                f"Write a viral 35-45 second faceless Short about '{chosen_topic}' in {language}.\n"
                f"Return JSON with keys:\n"
                f"- hook: opening 3-second explosive hook statement (under 16 words)\n"
                f"- script_sentences: list of 5-8 short, punchy sentences totaling ~75-95 words\n"
                f"- title: SEO Shorts title with hashtags\n"
                f"- description: 2-sentence description\n"
                f"- hashtags: list of 5 tags\n"
                f"- search_keywords: list of 4 visual search terms for vertical stock video\n"
                f"Respond ONLY with valid JSON."
            )
            raw = provider.complete(prompt)
            s = (raw or "").strip()
            fenced = re.search(r"```[A-Za-z]*\s*([\s\S]*?)\s*(?:```|\Z)", s)
            if fenced:
                s = fenced.group(1).strip()
            data = json.loads(s)
            if isinstance(data, dict) and "hook" in data and "script_sentences" in data:
                normalized = _normalize_llm_script(data, chosen_topic, chosen_template)
                if normalized is not None:
                    return normalized
        except Exception as e:
            logger.debug("LLM faceless script generation skipped (%s); using curated template.", e)

    # Deterministic fallback using chosen template
    sentences = list(chosen_template["sentences"])
    full_script = " ".join(sentences)
    return FacelessScriptResult(
        topic=chosen_topic,
        hook=chosen_template["hook"],
        script_sentences=sentences,
        full_script=full_script,
        title=chosen_template["title"],
        description=chosen_template["description"],
        hashtags=chosen_template["hashtags"],
        search_keywords=chosen_template["search_keywords"],
    )


async def _async_synthesize_edge_tts(
    text: str,
    voice: str,
    output_audio_path: Path,
    output_words_path: Path,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> tuple[Path, list[dict[str, Any]], float]:
    """Asynchronously synthesize neural voiceover using edge_tts Communicate."""
    import edge_tts

    output_audio_path.parent.mkdir(parents=True, exist_ok=True)
    output_words_path.parent.mkdir(parents=True, exist_ok=True)

    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
    words: list[dict[str, Any]] = []
    audio_buf = bytearray()

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_buf.extend(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            # Offset and duration are in 100ns (ticks)
            start_sec = chunk["offset"] / 10_000_000.0
            dur_sec = chunk["duration"] / 10_000_000.0
            words.append(
                {
                    "word": chunk["text"],
                    "start": round(start_sec, 3),
                    "end": round(start_sec + dur_sec, 3),
                    "probability": 1.0,
                }
            )
        elif chunk["type"] == "SentenceBoundary":
            # Fallback expansion from sentence boundaries to words
            sent_text = chunk.get("text", "")
            tokens = sent_text.split()
            s_start = chunk["offset"] / 10_000_000.0
            s_dur = chunk["duration"] / 10_000_000.0
            if tokens:
                step = s_dur / len(tokens)
                for idx, tok in enumerate(tokens):
                    w_start = s_start + idx * step
                    w_end = s_start + (idx + 1) * step
                    words.append(
                        {
                            "word": tok,
                            "start": round(w_start, 3),
                            "end": round(w_end, 3),
                            "probability": 1.0,
                        }
                    )

    output_audio_path.write_bytes(bytes(audio_buf))
    # Save aligned_words.json
    output_words_path.write_text(json.dumps(words, indent=2), encoding="utf-8")

    total_dur = words[-1]["end"] if words else 40.0
    return output_audio_path, words, total_dur


def _fallback_synthesize_audio(
    text: str,
    output_audio_path: Path,
    output_words_path: Path,
) -> tuple[Path, list[dict[str, Any]], float]:
    """Deterministic offline audio synthesis using FFmpeg sine / pure Python wave + word alignment."""
    tokens = text.split()
    total_dur = max(10.0, min(55.0, len(tokens) * 0.38))
    step = total_dur / max(1, len(tokens))

    words = [
        {
            "word": tok,
            "start": round(i * step, 3),
            "end": round((i + 1) * step, 3),
            "probability": 1.0,
        }
        for i, tok in enumerate(tokens)
    ]

    output_audio_path.parent.mkdir(parents=True, exist_ok=True)
    output_words_path.parent.mkdir(parents=True, exist_ok=True)

    # Write aligned words JSON
    with open(output_words_path, "w", encoding="utf-8") as f:
        json.dump(words, f, indent=2)

    # Synthesize clean voiceover tone
    wav_target = output_audio_path.with_suffix(".wav")
    try:
        sample_rate = 24000
        n_samples = int(sample_rate * total_dur)
        with wave.open(str(wav_target), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            frames = bytearray()
            for i in range(n_samples):
                t = i / sample_rate
                # Formant speech simulation (f1=500Hz, f2=1500Hz modulated)
                val = (
                    math.sin(2.0 * math.pi * 140.0 * t) * 0.25
                    + math.sin(2.0 * math.pi * 280.0 * t) * 0.12
                ) * (0.8 + 0.2 * math.sin(2.0 * math.pi * 3.5 * t))
                sample_int = int(max(-32767, min(32767, val * 32767)))
                frames.extend(struct.pack("<h", sample_int))
            wf.writeframes(frames)
        return wav_target, words, total_dur
    except Exception as e:
        logger.error("Audio fallback synthesis failed: %s", e)
        # Touch target
        output_audio_path.touch()
        return output_audio_path, words, total_dur


def _try_kokoro_tts(
    text: str,
    voice: str,
    output_audio_path: Path,
    output_words_path: Path,
) -> tuple[Path, list[dict[str, Any]], float] | None:
    """Kokoro fallback; returns None when unavailable or failing."""
    try:
        from autoshorts.media.kokoro_fallback import kokoro_available, synthesize_kokoro

        if not kokoro_available():
            return None
        from autoshorts.media.compositor import FasterWhisperTranscriber

        output_audio_path.parent.mkdir(parents=True, exist_ok=True)
        output_words_path.parent.mkdir(parents=True, exist_ok=True)
        wav = synthesize_kokoro(text, voice, output_audio_path.with_suffix(".wav"))
        words = FasterWhisperTranscriber().align_words(wav)
        output_words_path.write_text(json.dumps(words, indent=2), encoding="utf-8")
        total_dur = words[-1]["end"] if words else 40.0
        logger.info("TTS engine used: kokoro")
        return wav, words, total_dur
    except Exception as err:
        logger.info("Kokoro fallback failed (%s); using offline fallback.", err)
        return None


def synthesize_neural_tts(
    text: str,
    voice: str,
    output_audio_path: Path,
    output_words_path: Path,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> tuple[Path, list[dict[str, Any]], float]:
    """Synthesize studio-quality voiceover via Microsoft Edge TTS with aligned words.

    Returns (audio_path, aligned_words_list, total_duration_seconds).
    Falls back gracefully to offline synthesized voiceover if network is unavailable.
    """

    def _run_bounded() -> tuple[Path, list[dict[str, Any]], float]:
        # wait_for cancels the stream when it hangs, so no late write can race the fallback.
        return asyncio.run(
            asyncio.wait_for(
                _async_synthesize_edge_tts(
                    text, voice, output_audio_path, output_words_path, rate=rate, pitch=pitch
                ),
                timeout=_TTS_TIMEOUT_SEC,
            )
        )

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Run in a dedicated thread to avoid blocking running event loop. Do not use the
            # pool as a context manager: its __exit__ waits for the worker and would defeat the timeout.
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                result = pool.submit(_run_bounded).result(timeout=_TTS_TIMEOUT_SEC + 5.0)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        else:
            result = _run_bounded()
        logger.info("TTS engine used: edge-tts")
        return result
    except Exception as tts_err:
        logger.info("edge-tts live synthesis unavailable (%s); trying Kokoro.", tts_err)
        kokoro_result = _try_kokoro_tts(text, voice, output_audio_path, output_words_path)
        if kokoro_result is not None:
            return kokoro_result
        logger.info("TTS engine used: offline fallback")
        return _fallback_synthesize_audio(text, output_audio_path, output_words_path)


def create_faceless_short_job(
    req: FacelessShortRequest,
    pipe: Any,
    state_mgr: Any,
) -> dict[str, Any]:
    """Execute complete autonomous faceless Short creation.

    1. Script & SEO metadata generation
    2. Topic de-duplication recording
    3. Neural TTS synthesis with aligned words
    4. Base visual background assembly
    5. ASS karaoke subtitle generation
    6. Master 1080x1920 compositing
    """
    # 1. Past topics retrieval
    past_topics: list[str] = []
    if hasattr(state_mgr, "get_topic_history"):
        try:
            past_topics = state_mgr.get_topic_history(niche=req.niche)
        except Exception as e:
            logger.debug("Failed retrieving topic history: %s", e)

    # 2. Script generation
    cfg = getattr(pipe, "config", None)
    script = generate_faceless_script(
        niche=req.niche,
        topic=req.topic,
        language=req.language,
        config=cfg,
        past_topics=past_topics,
    )

    # 3. Record topic history
    if hasattr(state_mgr, "record_topic_history"):
        try:
            state_mgr.record_topic_history(niche=req.niche, topic=script.topic)
        except Exception as e:
            logger.debug("Failed recording topic history: %s", e)

    # 4. Initialize Job & work directories
    job_id = f"faceless_{uuid4().hex[:10]}"
    work_dir = get_work_dir() / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    tts_audio_path = work_dir / f"{job_id}_voice.mp3"
    aligned_words_path = work_dir / "aligned_words.json"

    actual_audio_path, _words, audio_duration = synthesize_neural_tts(
        text=script.full_script,
        voice=req.voice,
        output_audio_path=tts_audio_path,
        output_words_path=aligned_words_path,
    )

    # 5. Build base visual cut
    bg_file, _ = pick_background_asset(category=req.background_category)
    section_path = work_dir / f"section_{job_id}.mp4"

    # Mux background video and voiceover audio
    if bg_file and Path(bg_file).is_file():
        mux_cmd = [
            "ffmpeg",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(bg_file),
            "-i",
            str(actual_audio_path),
            "-t",
            str(audio_duration),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(section_path),
        ]
    else:
        # Generate 1080x1920 solid dark canvas
        mux_cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x0f172a:s=1080x1920:r=30:d={audio_duration}",
            "-i",
            str(actual_audio_path),
            "-t",
            str(audio_duration),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(section_path),
        ]

    try:
        proc = subprocess.run(
            mux_cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120.0,
            check=False,
        )
        if proc.returncode != 0 or not section_path.is_file():
            # If FFmpeg failed, create empty fallback section
            section_path.touch()
    except Exception as mux_err:
        logger.warning("Base video mux failed (%s); creating mock section.", mux_err)
        section_path.touch()

    # 6. Configure Job state
    candidate = ClipCandidate(
        start=0.0,
        end=audio_duration,
        text=script.full_script,
        score=1.0,
        reason=script.topic,
    )
    selection = ClipSelection(
        candidate=candidate,
        clip_index=0,
        target_duration=audio_duration,
        background_style=req.background_category,
        hook_text=script.hook,
        title=script.title,
    )
    edl = EditDecisionList(
        hook_in_sec=0.0,
        hook_out_sec=audio_duration,
        recommended_title=script.title[:60],
        recommended_hook=script.hook[:100],
        background_style_recommendation=req.background_category
        if req.background_category in ["minecraft", "subway"]
        else "auto",
    )

    metadata = VideoMetadata(
        title=script.title,
        channel="AutoShorts Faceless",
        channel_url="https://youtube.com",
        video_url=f"faceless://{job_id}",
        duration=audio_duration,
        video_id=job_id,
        description=script.description,
    )

    job = Job(
        id=job_id,
        source_url=f"faceless://{job_id}",
        custom_instructions=f"Faceless Short: {script.topic}",
        status=JobStatus.DOWNLOADING_SECTION,
        video_metadata=metadata,
        candidate_windows=[candidate],
        selected_clips=[selection],
        edl=edl,
        raw_video_path=str(section_path),
        style_preset=req.style_preset,
        layout_mode=req.layout_mode,
        custom_tags=script.hashtags,
    )

    if hasattr(state_mgr, "save_job"):
        state_mgr.save_job(job)

    # 7. Run Subtitles Stage
    if hasattr(pipe, "_run_subtitles_stage"):
        pipe._run_subtitles_stage(job, use_cached_words=True)

    # 8. Run Compositing Stage
    if hasattr(pipe, "_run_compositing_stage"):
        pipe._run_compositing_stage(job, background_category=req.background_category)

    if hasattr(state_mgr, "save_job"):
        state_mgr.save_job(job)

    return job.model_dump()


__all__ = [
    "NICHE_TOPIC_TEMPLATES",
    "VOICE_CATALOG",
    "create_faceless_short_job",
    "generate_faceless_script",
    "synthesize_neural_tts",
]
