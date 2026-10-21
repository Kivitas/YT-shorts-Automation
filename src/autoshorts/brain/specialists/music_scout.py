"""Music Scout Specialist Agent.

Analyzes vocal cadence (Words Per Second), determines optimal BGM tempo (BPM)
and musical mood, matches against the viral BGM catalog, and resolves local music assets.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from autoshorts.brain.schemas import AudioPlan
from autoshorts.core.paths import get_assets_dir
from autoshorts.research_agent.song_recommender import get_viral_bgm_catalog

logger = logging.getLogger("autoshorts.brain.music_scout")


class MusicScoutAgent:
    """Specialist agent that calculates speech cadence and selects rhythm-locked BGM."""

    def __init__(self, music_dir: Path | None = None) -> None:
        self.music_dir = music_dir or (get_assets_dir() / "music")

    async def scout(
        self,
        script_or_transcript: str,
        duration_seconds: float | None = None,
        niche: str = "general",
    ) -> AudioPlan:
        """Derive cadence and assemble an AudioPlan."""
        # Clean and split text
        words = [w for w in (script_or_transcript or "").split() if w.strip()]
        word_count = len(words)

        # Estimate duration if absent
        if duration_seconds and duration_seconds > 1.0:
            eff_duration = duration_seconds
        else:
            # Short-form baseline: ~40s if 100+ words, else proportional to conversational 2.6 wps
            eff_duration = max(15.0, min(59.0, word_count / 2.6 if word_count > 0 else 40.0))

        wps = round(word_count / eff_duration, 2) if eff_duration > 0 else 2.5

        # Cadence to BPM & Mood Mapping
        if wps < 2.2:
            target_bpm = 100
            mood = "contemplative_ambient"
            base_volume = 0.22
        elif wps <= 3.0:
            target_bpm = 120
            mood = "synthwave_pulse"
            base_volume = 0.18
        else:
            target_bpm = 140
            mood = "high_octane_phonk"
            base_volume = 0.15

        # Rhythmic eighth-note sidechain release: 30000 / BPM (in ms)
        ducking_release_ms = round(30000.0 / target_bpm)

        # Query catalog
        catalog = get_viral_bgm_catalog()
        best_track = None
        min_distance = 999

        norm_niche = (niche or "").strip().lower()
        for track in catalog:
            bpm_diff = abs(track.bpm - target_bpm)
            niche_bonus = -15 if (norm_niche and norm_niche in track.best_fit_niches) else 0
            score = bpm_diff + niche_bonus
            if score < min_distance:
                min_distance = score
                best_track = track

        if not best_track and catalog:
            best_track = catalog[0]

        # Resolve local music file path
        resolved_path = self._resolve_local_audio_path(best_track)

        track_id = best_track.track_id if best_track else "default_dark_synthwave"
        track_title = best_track.title if best_track else "Dark Synthwave"
        track_bpm = best_track.bpm if best_track else target_bpm

        return AudioPlan(
            bgm_track_id=track_id,
            bgm_title=track_title,
            bgm_path=str(resolved_path) if resolved_path else None,
            tempo_bpm=track_bpm,
            target_bpm=target_bpm,
            words_per_second=wps,
            mood=mood,
            volume=base_volume,
            ducking_release_ms=ducking_release_ms,
            confidence=0.88,
        )

    def _resolve_local_audio_path(self, track: Any) -> Path | None:
        """Resolve physical .wav or .mp3 file in assets/music/."""
        if not self.music_dir.is_dir():
            return None

        # 1. Check track.local_path if specified
        if track and getattr(track, "local_path", None):
            candidate = Path(track.local_path)
            if candidate.is_file():
                return candidate
            rel_candidate = self.music_dir / candidate.name
            if rel_candidate.is_file():
                return rel_candidate

        # 2. Check track title slug
        if track and getattr(track, "title", None):
            slug = track.title.lower().replace(" ", "_").replace("-", "_")
            for ext in (".wav", ".mp3", ".ogg"):
                p = self.music_dir / f"{slug}{ext}"
                if p.is_file():
                    return p

        # 3. Fallback to dark_synthwave.wav or any existing audio in assets/music/
        default_synth = self.music_dir / "dark_synthwave.wav"
        if default_synth.is_file():
            return default_synth

        for ext in ("*.wav", "*.mp3"):
            matches = list(self.music_dir.glob(ext))
            if matches:
                return matches[0]

        return None
