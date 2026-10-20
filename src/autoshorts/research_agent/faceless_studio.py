"""Autonomous Faceless AI Shorts Studio module.

Re-exports core faceless Short generation functions and neural TTS synthesis.
"""

from __future__ import annotations

from autoshorts.research_agent.moneyprinter import (
    VOICE_CATALOG,
    create_faceless_short_job,
    generate_faceless_script,
    synthesize_neural_tts,
)

__all__ = [
    "VOICE_CATALOG",
    "create_faceless_short_job",
    "generate_faceless_script",
    "synthesize_neural_tts",
]
