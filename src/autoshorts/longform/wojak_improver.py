"""Wojak Script & Staging Improver Agent.

Critiques and automatically optimizes Wojak video scripts and scenes for:
1. Comedic punchline brevity (<120 chars for fast-paced viral meme delivery).
2. Character diversity (genders: male, female, nonna, tradwife; 7 racial skin tones; height contrasts).
3. Expressive props & emotional storytelling.
4. Dynamic staging (alternating left/right character placement, architectural background alignment).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WojakImprovementReport:
    """Detailed audit report and refined scene deck produced by the Improver."""

    topic: str
    original_scene_count: int
    improved_scenes: list[dict[str, Any]]
    improvements_made: list[str] = field(default_factory=list)
    diversity_score: float = 1.0  # 0.0 to 1.0
    readability_score: float = 1.0  # 0.0 to 1.0


# ---------------------------------------------------------------------------
# Cultural Archetype & Staging Optimization Rules
# ---------------------------------------------------------------------------

COUNTRY_OPTIMIZATIONS: dict[str, dict[str, Any]] = {
    "usa": {
        "gender": "male",
        "race": "fair",
        "height": "tall",
        "emotion": "crying",
        "prop": "usa_bill",
        "clothing": "hospital_gown",
    },
    "uk": {
        "gender": "female",  # Tired NHS nurse adds variety
        "race": "fair",
        "height": "average",
        "emotion": "npc",
        "prop": "uk_trolley_tea",
        "clothing": "nurse_scrubs",
    },
    "south korea": {
        "gender": "female",
        "race": "east_asian",
        "height": "average",
        "emotion": "bandaged",
        "prop": "korea_bandages",
        "clothing": "hospital_gown",
    },
    "korea": {
        "gender": "female",
        "race": "east_asian",
        "height": "average",
        "emotion": "bandaged",
        "prop": "korea_bandages",
        "clothing": "hospital_gown",
    },
    "india": {
        "gender": "male",
        "race": "south_asian",
        "height": "tall",
        "emotion": "chad",
        "prop": "india_stethoscope",
        "clothing": "doctor_coat",
    },
    "italy": {
        "gender": "nonna",
        "race": "olive",
        "height": "short",
        "emotion": "happy",
        "prop": "italy_pasta_pot",
        "clothing": "apron",
    },
    "canada": {
        "gender": "female",
        "race": "fair",
        "height": "average",
        "emotion": "doomer",
        "prop": "canada_calendar",
        "clothing": "casual_hoodie",
    },
    "germany": {
        "gender": "male",
        "race": "fair",
        "height": "tall",
        "emotion": "npc",
        "prop": "germany_fax_clipboard",
        "clothing": "business_suit",
    },
    "sweden": {
        "gender": "male",
        "race": "fair",
        "height": "tall",
        "emotion": "doomer",
        "prop": "sweden_phone_queue",
        "clothing": "casual_hoodie",
    },
    "france": {
        "gender": "male",
        "race": "fair",
        "height": "average",
        "emotion": "smug",
        "prop": "france_coffee_cigarette",
        "clothing": "trench_coat",
    },
    "japan": {
        "gender": "female",
        "race": "east_asian",
        "height": "short",
        "emotion": "npc",
        "prop": "generic_clipboard",
        "clothing": "business_suit",
    },
}


def improve_wojak_script(
    scenes: list[dict[str, Any]],
    topic: str = "",
) -> WojakImprovementReport:
    """Analyze and refine Wojak scene deck for comedy timing, diversity, and staging."""
    improvements: list[str] = []
    refined: list[dict[str, Any]] = []

    # Track diversity metrics
    genders_used: set[str] = set()
    races_used: set[str] = set()
    heights_used: set[str] = set()

    for idx, sc in enumerate(scenes):
        sc_out = dict(sc)
        heading = (sc_out.get("heading") or sc_out.get("country") or f"Scene {idx + 1}").strip()
        h_lower = heading.lower()
        punchline = (sc_out.get("punchline") or sc_out.get("caption") or "").strip()

        # 1. Punchline Length Audit (<120 chars for rapid meme reading)
        if len(punchline) > 120:
            shortened = _condense_punchline(punchline, max_len=115)
            improvements.append(
                f"[{heading}] Condensed punchline from {len(punchline)} to {len(shortened)} chars for punchier comedy timing."
            )
            sc_out["caption"] = shortened
            sc_out["punchline"] = shortened
        else:
            sc_out["caption"] = punchline
            sc_out["punchline"] = punchline

        # 2. Dynamic Stage Placement (Alternating Left / Right for visual rhythm)
        expected_side = "left" if idx % 2 == 0 else "right"
        if sc_out.get("character_side") != expected_side:
            sc_out["character_side"] = expected_side
            improvements.append(
                f"[{heading}] Set character placement to {expected_side} to maintain dynamic visual rhythm."
            )

        # 3. Country & Archetype Diversity Optimization
        country_opt = None
        for c_key, opt in COUNTRY_OPTIMIZATIONS.items():
            if c_key in h_lower:
                country_opt = opt
                break

        if country_opt:
            for field_name, opt_val in country_opt.items():
                curr = sc_out.get(field_name)
                if (
                    not curr
                    or curr in ("generic", "none", "male")
                    and field_name == "gender"
                    and opt_val != "male"
                ):
                    sc_out[field_name] = opt_val
                    improvements.append(
                        f"[{heading}] Optimized {field_name} -> {opt_val} for authentic cultural archetype."
                    )
        else:
            # Procedural diversity balancing for non-country archetypes
            if not sc_out.get("gender"):
                # Distribute genders
                g_cycle = ["male", "female", "tradwife", "male", "female"]
                sc_out["gender"] = g_cycle[idx % len(g_cycle)]
            if not sc_out.get("height"):
                h_cycle = ["tall", "average", "short", "tall", "average"]
                sc_out["height"] = h_cycle[idx % len(h_cycle)]
            if not sc_out.get("race"):
                r_cycle = ["fair", "south_asian", "east_asian", "olive", "latino", "black"]
                sc_out["race"] = r_cycle[idx % len(r_cycle)]

        # Track usage
        genders_used.add(sc_out.get("gender", "male"))
        races_used.add(sc_out.get("race", "fair"))
        heights_used.add(sc_out.get("height", "average"))

        refined.append(sc_out)

    # 4. Score calculation
    diversity_score = min(
        1.0, (len(genders_used) * 0.35 + len(races_used) * 0.40 + len(heights_used) * 0.25)
    )
    readability_score = 1.0 if all(len(s.get("caption", "")) <= 120 for s in refined) else 0.8

    if len(improvements) == 0:
        improvements.append(
            "Scene deck already satisfies all punchline length, diversity, and staging standards."
        )

    return WojakImprovementReport(
        topic=topic or "Wojak Video",
        original_scene_count=len(scenes),
        improved_scenes=refined,
        improvements_made=improvements,
        diversity_score=round(diversity_score, 2),
        readability_score=round(readability_score, 2),
    )


def _condense_punchline(text: str, max_len: int = 115) -> str:
    """Intelligently condense text to under max_len preserving comedy punch."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    accum = ""
    for s in sentences:
        candidate = f"{accum} {s}".strip() if accum else s
        if len(candidate) <= max_len:
            accum = candidate
        else:
            break
    if accum and len(accum) >= 20:
        return accum
    # Truncate at word boundary
    words = text.split()
    accum = ""
    for w in words:
        if len(f"{accum} {w}".strip()) <= max_len - 3:
            accum = f"{accum} {w}".strip()
        else:
            break
    return f"{accum}..."
