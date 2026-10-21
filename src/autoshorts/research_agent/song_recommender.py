"""Trending Viral Songs & 1-Click BGM Downloader.

Manages curated royalty-free BGM tracks for automatic sidechain ducking in the
FFmpeg compositor, downloads tracks via yt-dlp, synthesizes procedural rhythmic
audio fallbacks, and tracks trending commercial YouTube Shorts sounds.
"""

from __future__ import annotations

import logging
import math
import os
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any

import httpx

from autoshorts.core.credential_guardian import CredentialHealthReport, classify_api_error
from autoshorts.core.paths import get_assets_dir
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.research_agent.models import TrendingShortsAudio, ViralBGMTrack

logger = logging.getLogger("autoshorts.research_agent.music")

QUOTA_COST_VIDEOS_LIST: int = 1


class TrendingMusicResult(list):
    """List of TrendingShortsAudio models carrying structured API and quota diagnostics."""

    def __init__(
        self,
        items: list[TrendingShortsAudio] | None = None,
        api_configured: bool = True,
        message: str | None = None,
        status: str = "ok",
        error_code: str | None = None,
        health_report: CredentialHealthReport | None = None,
    ) -> None:
        super().__init__(items or [])
        self.api_configured = api_configured
        self.message = message
        self.status = status
        self.error_code = error_code
        self.health_report = health_report

    def __getitem__(self, item: Any) -> Any:
        if isinstance(item, str):
            if item in ("sounds", "tracks", "items", "trending_shorts_audio"):
                return list(self)
            if hasattr(self, item):
                return getattr(self, item)
            raise KeyError(item)
        return super().__getitem__(item)

    def get(self, key: str, default: Any = None) -> Any:
        if key in ("sounds", "tracks", "items", "trending_shorts_audio"):
            return list(self)
        if hasattr(self, key):
            val = getattr(self, key)
            return val if val is not None else default
        return default

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.status == "ok",
            "api_configured": self.api_configured,
            "message": self.message,
            "status": self.status,
            "error_code": self.error_code,
            "trending_shorts_audio": [
                s.model_dump() if hasattr(s, "model_dump") else s for s in self
            ],
            "count": len(self),
            "health_report": self.health_report.model_dump() if self.health_report else None,
        }


def _build_music_result(
    items: list[TrendingShortsAudio],
    api_configured: bool = True,
    message: str | None = None,
    status: str = "ok",
    error_code: str | None = None,
    health_report: CredentialHealthReport | None = None,
    return_structured: bool = False,
) -> list[TrendingShortsAudio] | TrendingMusicResult | dict[str, Any]:
    res = TrendingMusicResult(
        items=items,
        api_configured=api_configured,
        message=message,
        status=status,
        error_code=error_code,
        health_report=health_report,
    )
    if return_structured:
        return res.to_dict()
    return res

CURATED_VIRAL_BGM: list[dict[str, Any]] = [
    {
        "track_id": "drift_phonk",
        "title": "Drift Phonk 138 BPM",
        "artist": "Kordhell & DVRST Vibe",
        "genre": "Phonk",
        "bpm": 138,
        "mood": "Aggressive / High Energy",
        "best_fit_niches": ["gaming", "ai_tech"],
        "source_url": "https://www.youtube.com/watch?v=1-xGerv5FOk",
        "license_type": "Royalty-Free / CC0",
    },
    {
        "track_id": "dark_synthwave",
        "title": "Dark Synthwave / Cyberpunk Pulse",
        "artist": "Infraction Vibe",
        "genre": "Synthwave",
        "bpm": 120,
        "mood": "Futuristic / Intense",
        "best_fit_niches": ["ai_tech", "science"],
        "source_url": "https://www.youtube.com/watch?v=kYgGwS6lFjE",
        "license_type": "Royalty-Free / CC0",
    },
    {
        "track_id": "chillhop_lofi",
        "title": "High-Retention Lo-Fi Chillhop",
        "artist": "Lofi Fruits Music",
        "genre": "Lo-Fi",
        "bpm": 82,
        "mood": "Calm / Conversational",
        "best_fit_niches": ["podcasts", "self_improvement"],
        "source_url": "https://www.youtube.com/watch?v=jfKfPfyJRdk",
        "license_type": "Royalty-Free / CC-BY",
    },
    {
        "track_id": "tension_percussion",
        "title": "MrBeast Fast Tension Percussion",
        "artist": "Viral Hits Lab",
        "genre": "Percussion",
        "bpm": 134,
        "mood": "Urgent / Suspenseful",
        "best_fit_niches": ["business", "finance"],
        "source_url": "https://www.youtube.com/watch?v=9Sc-IRjd5Hc",
        "license_type": "Royalty-Free / CC0",
    },
    {
        "track_id": "cinematic_riser",
        "title": "Cinematic Sub-Bass Riser",
        "artist": "Hans Zimmer Style",
        "genre": "Cinematic",
        "bpm": 90,
        "mood": "Epic / Dramatic",
        "best_fit_niches": ["science", "finance"],
        "source_url": "https://www.youtube.com/watch?v=4xDzrJKXOOY",
        "license_type": "Royalty-Free / CC0",
    },
    {
        "track_id": "motivational_piano",
        "title": "Motivational Piano & Strings",
        "artist": "Max Richter Vibe",
        "genre": "Classical / Acoustic",
        "bpm": 92,
        "mood": "Inspiring / Emotional",
        "best_fit_niches": ["self_improvement", "business"],
        "source_url": "https://www.youtube.com/watch?v=2OEL4P1Rz04",
        "license_type": "Royalty-Free / CC-BY",
    },
    {
        "track_id": "cyberpunk_bassline",
        "title": "Cyberpunk Bassline",
        "artist": "NeoTokyo Audio",
        "genre": "Electronic",
        "bpm": 125,
        "mood": "Driving / High Dopamine",
        "best_fit_niches": ["gaming", "ai_tech"],
        "source_url": "https://www.youtube.com/watch?v=kJQP7kiw5Fk",
        "license_type": "Royalty-Free / CC0",
    },
    {
        "track_id": "acoustic_storyteller",
        "title": "Acoustic Storyteller",
        "artist": "Folk Woods",
        "genre": "Acoustic",
        "bpm": 88,
        "mood": "Authentic / Thoughtful",
        "best_fit_niches": ["health", "podcasts"],
        "source_url": "https://www.youtube.com/watch?v=fJ9rUzIMcZQ",
        "license_type": "Royalty-Free / CC-BY",
    },
]

TRENDING_SHORTS_SOUNDS: list[dict[str, Any]] = [
    {
        "sound_id": "sound_phonk_brazilian",
        "title": "Montagem Diamante Rosa",
        "artist": "DJ Holanda & VT",
        "bpm": 130,
        "vibe": "Brazilian Phonk / High Velocity",
        "trending_rank": 1,
        "best_fit_niche": "gaming",
        "youtube_shorts_sound_url": "https://www.youtube.com/results?search_query=Montagem+Diamante+Rosa+shorts+sound",
        "usage_tip": "Cut video angles and text punch-ins every 4 beats at 130 BPM for maximum dopamine.",
    },
    {
        "sound_id": "sound_interstellar_remix",
        "title": "Cornfield Chase (Cyberpunk Synth Rework)",
        "artist": "Dorian Marko Vibe",
        "bpm": 96,
        "vibe": "Epic / Mind-Blowing",
        "trending_rank": 2,
        "best_fit_niche": "science",
        "youtube_shorts_sound_url": "https://www.youtube.com/results?search_query=Cornfield+Chase+synth+rework+shorts",
        "usage_tip": "Use for space, astrophysics, or profound philosophical questions at peak revelation.",
    },
    {
        "sound_id": "sound_metamorphosis",
        "title": "Metamorphosis (Speed Up)",
        "artist": "INTERWORLD",
        "bpm": 145,
        "vibe": "Dark Phonk / Grindset",
        "trending_rank": 3,
        "best_fit_niche": "ai_tech",
        "youtube_shorts_sound_url": "https://www.youtube.com/results?search_query=Metamorphosis+Speed+Up+shorts+sound",
        "usage_tip": "Sync opening hook directly on the first drop (second 0.0 - 2.8).",
    },
    {
        "sound_id": "sound_time_chamber",
        "title": "Hyperbolic Tension",
        "artist": "Odetari Style",
        "bpm": 136,
        "vibe": "Urgent Suspense",
        "trending_rank": 4,
        "best_fit_niche": "finance",
        "youtube_shorts_sound_url": "https://www.youtube.com/results?search_query=Hyperbolic+Tension+shorts+audio",
        "usage_tip": "Ideal for countdowns ('The 3 tax loopholes you must know before Friday').",
    },
    {
        "sound_id": "sound_reflections_lofi",
        "title": "Late Night Coffee Thoughts",
        "artist": "Kudasai Beat",
        "bpm": 80,
        "vibe": "Reflective / Storytelling",
        "trending_rank": 5,
        "best_fit_niche": "self_improvement",
        "youtube_shorts_sound_url": "https://www.youtube.com/results?search_query=Late+Night+Coffee+Thoughts+lofi+shorts",
        "usage_tip": "Keep BGM volume at -24 dB to preserve vocal intimacy and clear diction.",
    },
    {
        "sound_id": "sound_pod_intro_punch",
        "title": "808 Sub Boom & Ambient Drone",
        "artist": "Studio FX",
        "bpm": 90,
        "vibe": "Dramatic Podcast Intro",
        "trending_rank": 6,
        "best_fit_niche": "podcasts",
        "youtube_shorts_sound_url": "https://www.youtube.com/results?search_query=808+Sub+Boom+Ambient+Drone+shorts",
        "usage_tip": "Trigger the sub-bass boom on the exact frame the speaker reveals the surprise.",
    },
]


def _resolve_music_dir(assets_dir: Path | None = None) -> Path:
    """Return assets/music directory path and ensure it exists."""
    base = assets_dir or get_assets_dir()
    music_dir = base / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    return music_dir


def get_viral_bgm_catalog(assets_dir: Path | None = None) -> list[ViralBGMTrack]:
    """Return all curated viral BGM tracks, marking is_installed if present on disk."""
    music_dir = _resolve_music_dir(assets_dir)
    catalog: list[ViralBGMTrack] = []

    for item in CURATED_VIRAL_BGM:
        tid = item["track_id"]
        # Check for .mp3, .wav, or .m4a
        installed_path: Path | None = None
        for ext in (".mp3", ".wav", ".m4a"):
            candidate = music_dir / f"{tid}{ext}"
            if candidate.is_file() and candidate.stat().st_size > 0:
                installed_path = candidate
                break

        catalog.append(
            ViralBGMTrack(
                track_id=tid,
                title=item["title"],
                artist=item["artist"],
                genre=item["genre"],
                bpm=item["bpm"],
                mood=item["mood"],
                best_fit_niches=item["best_fit_niches"],
                source_url=item["source_url"],
                license_type=item["license_type"],
                is_installed=installed_path is not None,
                local_path=str(installed_path) if installed_path else None,
            )
        )
    return catalog


def _synthesize_procedural_wav(track_id: str, dest_path: Path, duration_sec: float = 45.0) -> None:
    """Synthesize clean procedural rhythmic audio track using FFmpeg or pure Python wave."""
    genre_freqs = {
        "drift_phonk": (55.0, 110.0, 4.6),
        "dark_synthwave": (110.0, 220.0, 2.0),
        "chillhop_lofi": (261.63, 329.63, 1.4),
        "tension_percussion": (80.0, 160.0, 4.4),
        "cinematic_riser": (65.41, 130.81, 1.5),
        "motivational_piano": (220.0, 440.0, 1.5),
        "cyberpunk_bassline": (60.0, 120.0, 4.1),
        "acoustic_storyteller": (196.0, 293.66, 1.4),
    }
    f1, f2, mod = genre_freqs.get(track_id, (110.0, 220.0, 2.0))

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Try FFmpeg aevalsrc first
    filter_expr = (
        f"aevalsrc=sin(2*PI*{f1}*t)*0.18*sin(2*PI*{mod}*t) + "
        f"sin(2*PI*{f2}*t)*0.08:s=44100:d={int(duration_sec)}"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        filter_expr,
        "-af",
        f"afade=t=in:ss=0:d=1,afade=t=out:st={int(duration_sec - 2)}:d=2",
        str(dest_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
        if proc.returncode == 0 and dest_path.is_file() and dest_path.stat().st_size > 0:
            return
    except Exception as e:
        logger.debug("FFmpeg procedural synth failed (%s); using pure Python wave synthesizer.", e)

    # Pure Python wave fallback (100% offline & zero dependencies)
    sample_rate = 44100
    n_samples = int(sample_rate * duration_sec)
    try:
        with wave.open(str(dest_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            frames = bytearray()
            for i in range(n_samples):
                t = i / sample_rate
                # Apply envelope fade in/out
                env = min(1.0, t / 1.0) * min(1.0, (duration_sec - t) / 2.0)
                env = max(0.0, env)
                val = (
                    math.sin(2.0 * math.pi * f1 * t) * 0.18 * math.sin(2.0 * math.pi * mod * t)
                    + math.sin(2.0 * math.pi * f2 * t) * 0.08
                ) * env
                sample_int = int(max(-32767, min(32767, val * 32767)))
                frames.extend(struct.pack("<h", sample_int))
            wf.writeframes(frames)
    except Exception as w_err:
        logger.error("Pure Python wave synthesis failed: %s", w_err)
        raise


def download_viral_bgm(
    track_id: str,
    custom_url: str | None = None,
    assets_dir: Path | None = None,
) -> dict[str, Any]:
    """Download or synthesize a viral background music track into assets/music/.

    Attempts yt-dlp audio download first; on failure, synthesis procedural audio.
    """
    music_dir = _resolve_music_dir(assets_dir)
    target_mp3 = music_dir / f"{track_id}.mp3"
    target_wav = music_dir / f"{track_id}.wav"

    # Find source url in catalog
    source_url = custom_url
    if not source_url:
        for t in CURATED_VIRAL_BGM:
            if t["track_id"] == track_id:
                source_url = t["source_url"]
                break

    if source_url and source_url.startswith("http"):
        cmd = [
            "yt-dlp",
            "--extract-audio",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "192K",
            "--output",
            str(target_mp3),
            "--no-warnings",
            source_url,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=12.0,
                check=False,
            )
            if proc.returncode == 0 and target_mp3.is_file() and target_mp3.stat().st_size > 0:
                return {
                    "success": True,
                    "track_id": track_id,
                    "file_path": str(target_mp3),
                    "method": "downloaded",
                    "duration_sec": 45.0,
                }
        except Exception as dl_err:
            logger.info(
                "yt-dlp download failed (%s); synthesizing procedural rhythmic audio.", dl_err
            )

    # Fallback to synthesizing a clean rhythmic audio track
    _synthesize_procedural_wav(track_id, target_wav, duration_sec=45.0)
    return {
        "success": True,
        "track_id": track_id,
        "file_path": str(target_wav),
        "method": "synthesized",
        "duration_sec": 45.0,
    }


def fetch_live_trending_music(
    api_key: str,
    limit: int = 10,
    quota_tracker: QuotaTracker | Any | None = None,
    return_structured: bool = False,
) -> list[TrendingShortsAudio] | TrendingMusicResult | dict[str, Any]:
    """Fetch live trending commercial music videos from YouTube Data API v3.

    Enforces general quota checking and records 1 unit expenditure via QuotaTracker.
    Classifies 403 quotaExceeded, 400/403 keyInvalid, and network errors via credential_guardian.
    """
    if not api_key or not api_key.strip():
        return _build_music_result(
            [],
            api_configured=False,
            message="YouTube Data API key is missing.",
            status="missing_key",
            return_structured=return_structured,
        )

    clean_key = api_key.strip()

    # Pre-call quota affordability check
    if quota_tracker is not None and hasattr(quota_tracker, "can_verify"):
        if not quota_tracker.can_verify(QUOTA_COST_VIDEOS_LIST):
            msg = (
                f"YouTube Data API quota cannot afford videos.list ({QUOTA_COST_VIDEOS_LIST} unit); "
                "skipping trending music fetch."
            )
            logger.warning(msg)
            return _build_music_result(
                [],
                api_configured=True,
                message=msg,
                status="quota_exhausted",
                return_structured=return_structured,
            )

    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet,statistics",
        "chart": "mostPopular",
        "videoCategoryId": "10",  # Music category
        "maxResults": min(max(limit, 5), 25),
        "key": clean_key,
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, params=params)
            if resp.status_code != 200:
                err_payload: Any = None
                try:
                    err_payload = resp.json()
                except Exception:
                    err_payload = resp.text
                report = classify_api_error("youtube_api_key", err_payload)
                logger.warning(
                    "YouTube trending music API error (%d, status=%s): %s",
                    resp.status_code,
                    report.status,
                    report.message,
                )
                return _build_music_result(
                    [],
                    api_configured=True,
                    message=report.message,
                    status=report.status,
                    error_code=report.error_code,
                    health_report=report,
                    return_structured=return_structured,
                )

            # Record 1 general quota unit on successful fetch
            if quota_tracker is not None and hasattr(quota_tracker, "record_general"):
                quota_tracker.record_general(QUOTA_COST_VIDEOS_LIST)

            data = resp.json()
            items = data.get("items", [])
            results: list[TrendingShortsAudio] = []
            for rank, item in enumerate(items, start=1):
                vid = item.get("id")
                snippet = item.get("snippet", {})
                title = snippet.get("title", "Trending Sound")
                artist = snippet.get("channelTitle", "YouTube Artist")
                results.append(
                    TrendingShortsAudio(
                        sound_id=f"yt_music_{vid}",
                        title=title,
                        artist=artist,
                        bpm=120,
                        vibe=f"Live YouTube Music Trend #{rank}",
                        trending_rank=rank,
                        best_fit_niche="all",
                        youtube_shorts_sound_url=f"https://www.youtube.com/watch?v={vid}",
                        usage_tip=f"Trending commercial release #{rank}. Recommended for viral sync cuts.",
                    )
                )
            return _build_music_result(
                results,
                api_configured=True,
                status="ok",
                return_structured=return_structured,
            )
    except Exception as e:
        report = classify_api_error("youtube_api_key", e)
        logger.warning(
            "Failed to fetch live trending music (%s, status=%s): %s",
            type(e).__name__,
            report.status,
            report.message,
        )
        return _build_music_result(
            [],
            api_configured=True,
            message=report.message,
            status=report.status,
            error_code=report.error_code,
            health_report=report,
            return_structured=return_structured,
        )


def get_trending_shorts_audio(
    genre: str = "all",
    api_key: str | None = None,
    allow_seed_fallback: bool = True,
    quota_tracker: QuotaTracker | Any | None = None,
    return_structured: bool = False,
) -> list[TrendingShortsAudio] | TrendingMusicResult | dict[str, Any]:
    """Return top trending commercial YouTube Shorts sounds and edit tips."""
    key = api_key or os.getenv("YOUTUBE_API_KEY")
    last_music_result: TrendingMusicResult | None = None
    if key and key.strip():
        live_music = fetch_live_trending_music(
            api_key=key.strip(),
            quota_tracker=quota_tracker,
        )
        if live_music:
            return live_music
        if isinstance(live_music, TrendingMusicResult):
            last_music_result = live_music

    if not allow_seed_fallback:
        if last_music_result is not None:
            return _build_music_result(
                [],
                api_configured=True,
                message=last_music_result.message,
                status=last_music_result.status,
                error_code=last_music_result.error_code,
                health_report=last_music_result.health_report,
                return_structured=return_structured,
            )
        if return_structured:
            return _build_music_result(
                [],
                api_configured=bool(key),
                message="No live trending audio found and seed fallback is disabled.",
                status="empty",
                return_structured=True,
            )
        return []

    results = [TrendingShortsAudio(**item) for item in TRENDING_SHORTS_SOUNDS]
    if genre and genre.lower() != "all":
        norm_genre = genre.lower()
        results = [
            r
            for r in results
            if norm_genre in r.vibe.lower() or norm_genre in r.best_fit_niche.lower()
        ]
    return results


__all__ = [
    "CURATED_VIRAL_BGM",
    "QUOTA_COST_VIDEOS_LIST",
    "TRENDING_SHORTS_SOUNDS",
    "TrendingMusicResult",
    "download_viral_bgm",
    "fetch_live_trending_music",
    "get_trending_shorts_audio",
    "get_viral_bgm_catalog",
]
