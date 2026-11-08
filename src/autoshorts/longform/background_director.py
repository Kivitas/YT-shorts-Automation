"""Dynamic Background Director for AutoShorts long-form video engine.

Implements procedural sentence-driven environments with Kurzgesagt-style aesthetic,
deep cosmic void palettes, neon accents, radial glows, drifting particles,
smooth cubic Hermite crossfades, and continuous Ken-Burns camera drift.
"""

from __future__ import annotations

import logging
import math
import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

# Standard canvas dimensions
WIDTH = 1920
HEIGHT = 1080

# Crossfade length bounds from the spec (400-600 ms).
TRANSITION_MIN_SECONDS = 0.4
TRANSITION_MAX_SECONDS = 0.6

# ---------------------------------------------------------------------------
# Saturated Void Palette (Kurzgesagt-inspired aesthetic)
# ---------------------------------------------------------------------------
RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]

# Spec void bases (Kurzgesagt palette)
COSMIC_INDIGO: RGB = (8, 9, 20)  # #080914
VOID_OBSIDIAN: RGB = (11, 14, 27)  # #0B0E1B
TWILIGHT_DEEP: RGB = (18, 22, 43)  # #12162B

# Supporting tint outside the spec palette
VOID_PARCHMENT: RGB = (20, 15, 14)  # #140F0E (antiquarian sepia-obsidian void)

# Spec accents
NEON_YELLOW: RGB = (255, 230, 0)  # Solar Yellow #FFE600
NEON_CYAN: RGB = (0, 240, 255)  # Supernova Cyan #00F0FF
NEON_PINK: RGB = (255, 42, 133)  # Nebula Magenta #FF2A85
AURORA_GREEN: RGB = (0, 229, 153)  # Aurora Green #00E599

# Supporting accents outside the spec palette
NEON_PURPLE: RGB = (139, 92, 246)  # #8B5CF6 (electric violet)
AMBER_GOLD: RGB = (229, 169, 60)  # #E5A93C (parchment navigation gold)
WHITE: RGB = (255, 255, 255)
WARM_WHITE: RGB = (245, 248, 255)


# ---------------------------------------------------------------------------
# Thematic Environments
# ---------------------------------------------------------------------------
class Environment(StrEnum):
    """The 5 primary Kurzgesagt thematic environments."""

    SPACE_COSMIC = "space_cosmic"
    MOLECULAR_LAB = "molecular_lab"
    CYBER_MATRIX = "cyber_matrix"
    FINANCIAL_LEDGER = "financial_ledger"
    PARCHMENT_MAP = "parchment_map"


# Normalized alias lookups
ENVIRONMENT_ALIASES: dict[str, Environment] = {
    "space": Environment.SPACE_COSMIC,
    "cosmic": Environment.SPACE_COSMIC,
    "space_cosmic": Environment.SPACE_COSMIC,
    "astrophysics": Environment.SPACE_COSMIC,
    "lab": Environment.MOLECULAR_LAB,
    "molecular": Environment.MOLECULAR_LAB,
    "molecular_lab": Environment.MOLECULAR_LAB,
    "chemistry": Environment.MOLECULAR_LAB,
    "biology": Environment.MOLECULAR_LAB,
    "cyber": Environment.CYBER_MATRIX,
    "matrix": Environment.CYBER_MATRIX,
    "cyber_matrix": Environment.CYBER_MATRIX,
    "digital": Environment.CYBER_MATRIX,
    "blueprint": Environment.FINANCIAL_LEDGER,
    "financial_ledger": Environment.FINANCIAL_LEDGER,
    "finance": Environment.FINANCIAL_LEDGER,
    "financial": Environment.FINANCIAL_LEDGER,
    "ledger": Environment.FINANCIAL_LEDGER,
    "money": Environment.FINANCIAL_LEDGER,
    "parchment": Environment.PARCHMENT_MAP,
    "parchment_map": Environment.PARCHMENT_MAP,
    "history": Environment.PARCHMENT_MAP,
    "historical": Environment.PARCHMENT_MAP,
    "map": Environment.PARCHMENT_MAP,
}


@dataclass
class SentenceBeat:
    """Represents one sentence segment of narration and its environmental visual state."""

    sentence_idx: int
    text: str
    start_time: float
    end_time: float
    environment: Environment
    camera_drift_angle: float = 0.0  # radians for subtle Ken-Burns pan
    zoom_start: float = 1.02
    zoom_end: float = 1.06

    @property
    def duration(self) -> float:
        return max(0.01, self.end_time - self.start_time)


# ---------------------------------------------------------------------------
# Mathematical helpers: Cubic Hermite & Interpolation
# ---------------------------------------------------------------------------
def cubic_hermite(u: float) -> float:
    """Standard cubic Hermite smoothstep spline: 3u^2 - 2u^3 with zero slope at u=0 and u=1."""
    u = min(1.0, max(0.0, float(u)))
    return u * u * (3.0 - 2.0 * u)


def quintic_hermite(u: float) -> float:
    """Higher-order quintic Hermite smootherstep: 6u^5 - 15u^4 + 10u^3."""
    u = min(1.0, max(0.0, float(u)))
    return u * u * u * (u * (u * 6.0 - 15.0) + 10.0)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_color(a: RGB, b: RGB, t: float) -> RGB:
    t = min(1.0, max(0.0, float(t)))
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


# ---------------------------------------------------------------------------
# Semantic Classification & Sentence Parsing
# ---------------------------------------------------------------------------
_KEYWORD_PATTERNS: list[tuple[Environment, re.Pattern[str]]] = [
    (
        Environment.SPACE_COSMIC,
        re.compile(
            r"\b(space|cosmic|galaxy|galaxies|star|stars|universe|planet|planets|orbit|"
            r"black\s*hole|vacuum|nebula|astronomy|astrophysics|telescope|celestial|"
            r"dark\s*matter|infinite|infinity|light\s*year|alien|extraterrestrial|"
            r"comet|meteor|gravity|solar|abyss|void|interstellar|rocket|satellite)\b",
            re.IGNORECASE,
        ),
    ),
    (
        Environment.MOLECULAR_LAB,
        re.compile(
            r"\b(molecule|molecules|molecular|atom|atoms|atomic|dna|rna|protein|proteins|"
            r"cell|cells|cellular|chemistry|chemical|reaction|reactions|enzyme|enzymes|"
            r"gene|genes|genetic|genetics|lab|laboratory|microscope|microscopic|"
            r"electron|proton|neutron|compounds|virus|bacteria|quantum|element|"
            r"bonds|covalent|vesicle|organism|bio|biology|biological)\b",
            re.IGNORECASE,
        ),
    ),
    (
        Environment.CYBER_MATRIX,
        re.compile(
            r"\b(cyber|matrix|code|coding|digital|data|network|networks|internet|"
            r"algorithm|algorithms|ai|artificial\s*intelligence|software|hardware|"
            r"binary|computer|neural|neural\s*network|crypto|blockchain|hacker|"
            r"virtual|stream|streams|processor|server|servers|cloud|protocol|"
            r"simulation|glitch|tech|technology|interface|database|logic|cyberpunk)\b",
            re.IGNORECASE,
        ),
    ),
    (
        Environment.FINANCIAL_LEDGER,
        re.compile(
            r"\b(money|finance|financial|finances|ledger|ledgers|bank|banks|banking|budget|"
            r"budgets|budgeting|dollar|dollars|cash|cents|price|prices|pricing|profit|profits|"
            r"revenue|income|salary|salaries|wage|wages|invest|investor|investors|investment|"
            r"investments|investing|stock|stocks|market|markets|interest|inflation|debt|debts|"
            r"credit|loan|loans|tax|taxes|wealth|economy|economic|economics|accounting|invoice|"
            r"invoices|trading|trader|traders|capital|ratio|ratios|metric|metrics|calculation|"
            r"calculations|spending|savings|fund|funds|bond|bonds|asset|assets|portfolio|cost|costs)\b",
            re.IGNORECASE,
        ),
    ),
    (
        Environment.PARCHMENT_MAP,
        re.compile(
            r"\b(history|historical|ancient|map|maps|cartography|empire|empires|"
            r"war|battle|kingdom|kingdoms|civilization|civilizations|century|centuries|"
            r"parchment|voyage|voyages|explorer|explorers|navigation|compass|dynasty|"
            r"relic|relics|territory|medieval|monarch|ruler|chronicle|continent|"
            r"conquest|heritage|tradition|scroll|expedition)\b",
            re.IGNORECASE,
        ),
    ),
]

_GENRE_DEFAULTS: dict[str, list[Environment]] = {
    "history": [Environment.PARCHMENT_MAP, Environment.FINANCIAL_LEDGER],
    "science": [Environment.MOLECULAR_LAB, Environment.SPACE_COSMIC],
    "comparison": [Environment.FINANCIAL_LEDGER, Environment.CYBER_MATRIX],
    "finance": [Environment.FINANCIAL_LEDGER, Environment.CYBER_MATRIX],
    "motivation": [Environment.SPACE_COSMIC, Environment.CYBER_MATRIX],
    "self_improvement": [Environment.SPACE_COSMIC, Environment.MOLECULAR_LAB],
    "parody": [Environment.CYBER_MATRIX, Environment.SPACE_COSMIC],
}

_ALL_ENVIRONMENTS: tuple[Environment, ...] = (
    Environment.SPACE_COSMIC,
    Environment.MOLECULAR_LAB,
    Environment.CYBER_MATRIX,
    Environment.FINANCIAL_LEDGER,
    Environment.PARCHMENT_MAP,
)


def classify_sentence_environment(
    sentence: str,
    genre: str = "",
    sentence_idx: int = 0,
    section_idx: int = 0,
    prev_environment: Environment | None = None,
) -> Environment:
    """Classify a sentence into one of the 5 thematic environments.

    Uses keyword pattern scoring, falling back to genre context and deterministic cycling
    to ensure the environment never feels static.
    """
    for env, pattern in _KEYWORD_PATTERNS:
        if pattern.search(sentence):
            return env

    # Fallback to genre context
    genre_clean = genre.lower().strip()
    if genre_clean in _GENRE_DEFAULTS:
        options = _GENRE_DEFAULTS[genre_clean]
        chosen = options[(sentence_idx + section_idx) % len(options)]
        return chosen

    # Deterministic rotation across environments, avoiding immediate repeats if possible
    candidates = [e for e in _ALL_ENVIRONMENTS if e != prev_environment] or list(_ALL_ENVIRONMENTS)
    return candidates[(sentence_idx + section_idx * 2) % len(candidates)]


def split_sentences(text: str) -> list[str]:
    """Split narration text into distinct sentences based on punctuation."""
    cleaned = text.strip()
    if not cleaned:
        return []
    # Split on terminal punctuation (. ! ?) followed by whitespace or string end
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    results = [p.strip() for p in parts if p.strip()]
    return results or [cleaned]


def build_sentence_beats(
    narration: str,
    duration: float,
    words: Sequence[dict[str, Any]] | None = None,
    section_idx: int = 0,
    genre: str = "",
) -> list[SentenceBeat]:
    """Segment narration into sentence beats with timestamps and assigned environments."""
    sentences = split_sentences(narration)
    total_duration = max(0.1, float(duration))
    if not sentences:
        # Fallback single empty beat
        env = classify_sentence_environment("", genre, 0, section_idx)
        return [
            SentenceBeat(
                sentence_idx=0,
                text="",
                start_time=0.0,
                end_time=total_duration,
                environment=env,
                camera_drift_angle=0.0,
            )
        ]

    # Attempt word alignment if word timestamps are available
    word_list = list(words or [])
    beats: list[SentenceBeat] = []
    prev_env: Environment | None = None

    if word_list and len(word_list) >= len(sentences):
        # Match words sequentially to sentences
        w_idx = 0
        total_words = len(word_list)
        for s_idx, sentence in enumerate(sentences):
            # Token count approximation
            tokens = [t.lower() for t in re.findall(r"\b\w+\b", sentence)]
            tok_count = max(1, len(tokens))

            start_t = float(word_list[w_idx].get("start", 0.0)) if w_idx < total_words else 0.0
            end_w_idx = min(w_idx + tok_count - 1, total_words - 1)
            end_t = float(word_list[end_w_idx].get("end", start_t + 1.0))

            w_idx = min(end_w_idx + 1, total_words)

            env = classify_sentence_environment(sentence, genre, s_idx, section_idx, prev_env)
            prev_env = env

            # Unique drift angle per sentence (golden ratio distribution)
            drift_angle = ((s_idx + section_idx * 5) * 2.39996) % (2.0 * math.pi)

            beats.append(
                SentenceBeat(
                    sentence_idx=s_idx,
                    text=sentence,
                    start_time=start_t,
                    end_time=end_t,
                    environment=env,
                    camera_drift_angle=drift_angle,
                )
            )

        # Normalize last beat end to total_duration
        if beats:
            beats[-1].end_time = max(beats[-1].end_time, total_duration)
            # Ensure strictly non-decreasing contiguous timestamps
            for i in range(len(beats) - 1):
                if beats[i].end_time > beats[i + 1].start_time:
                    mid = (beats[i].end_time + beats[i + 1].start_time) / 2.0
                    beats[i].end_time = mid
                    beats[i + 1].start_time = mid
    else:
        # Proportional duration distribution based on character count
        char_counts = [max(1, len(s)) for s in sentences]
        total_chars = sum(char_counts)
        cur_t = 0.0

        for s_idx, (sentence, count) in enumerate(zip(sentences, char_counts, strict=False)):
            frac = count / total_chars
            dur = frac * total_duration
            start_t = cur_t
            end_t = total_duration if s_idx == len(sentences) - 1 else cur_t + dur
            cur_t = end_t

            env = classify_sentence_environment(sentence, genre, s_idx, section_idx, prev_env)
            prev_env = env

            drift_angle = ((s_idx + section_idx * 5) * 2.39996) % (2.0 * math.pi)

            beats.append(
                SentenceBeat(
                    sentence_idx=s_idx,
                    text=sentence,
                    start_time=start_t,
                    end_time=end_t,
                    environment=env,
                    camera_drift_angle=drift_angle,
                )
            )

    return beats


# ---------------------------------------------------------------------------
# Procedural Drawing Helpers: Radial Glows, Grids & Diamonds
# ---------------------------------------------------------------------------
@lru_cache(maxsize=32)
def _radial_mask(radius: int, peak_alpha: int = 255) -> Image.Image:
    """Precompute and cache radial falloff alpha masks."""
    r = max(4, int(radius))
    d = r * 2
    mask = Image.new("L", (d, d), 0)
    draw = ImageDraw.Draw(mask)
    # Step-wise concentric falloff for high performance
    steps = min(64, r // 4)
    for i in range(steps, 0, -1):
        frac = i / steps
        cur_r = int(r * frac)
        alpha = int(peak_alpha * ((1.0 - frac) ** 1.8))
        draw.ellipse([r - cur_r, r - cur_r, r + cur_r, r + cur_r], fill=alpha)
    return mask


def draw_radial_glow(
    img: Image.Image,
    center: tuple[int, int],
    radius: int,
    color: RGB,
    intensity: float = 0.5,
) -> None:
    """Fast procedural radial atmospheric glow composited onto ``img``."""
    cx, cy = center
    r = max(8, int(radius))
    peak_alpha = int(255 * max(0.0, min(1.0, intensity)))
    mask = _radial_mask(r, peak_alpha)

    # Calculate bounding box on canvas
    x0, y0 = cx - r, cy - r
    w, h = img.size

    # Crop mask if clipping canvas boundaries
    mx0 = max(0, -x0)
    my0 = max(0, -y0)
    mx1 = mask.width - max(0, (x0 + mask.width) - w)
    my1 = mask.height - max(0, (y0 + mask.height) - h)

    cx0 = max(0, x0)
    cy0 = max(0, y0)
    cx1 = min(w, x0 + mask.width)
    cy1 = min(h, y0 + mask.height)

    if cx1 <= cx0 or cy1 <= cy0:
        return

    cropped_mask = mask.crop((mx0, my0, mx1, my1))
    solid = Image.new("RGB", (cx1 - cx0, cy1 - cy0), color)
    img.paste(solid, (cx0, cy0), mask=cropped_mask)


def draw_star_diamond(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    size: float,
    color: RGB,
    core_color: RGB = WHITE,
) -> None:
    """Draw a classic Kurzgesagt-style 4-pointed diamond star flare."""
    cx, cy = center
    s = max(2.0, float(size))
    w_spike = max(1.0, s * 0.16)

    # Horizontal diamond flare
    draw.polygon(
        [(cx - s, cy), (cx, cy - w_spike), (cx + s, cy), (cx, cy + w_spike)],
        fill=color,
    )
    # Vertical diamond flare
    draw.polygon(
        [(cx, cy - s), (cx + w_spike, cy), (cx, cy + s), (cx - w_spike, cy)],
        fill=color,
    )
    # Central intense core
    core_r = max(1.0, s * 0.22)
    draw.ellipse(
        [cx - core_r, cy - core_r, cx + core_r, cy + core_r],
        fill=core_color,
    )


# ---------------------------------------------------------------------------
# Base Gradient Cache (Ultra-Fast 60fps+ Rendering)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=16)
def _base_gradient(env: Environment) -> Image.Image:
    """Precompute deep cosmic gradient backplates per environment."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)

    if env == Environment.SPACE_COSMIC:
        top_c = TWILIGHT_DEEP
        bot_c = COSMIC_INDIGO
        for y in range(HEIGHT):
            k = y / HEIGHT
            c = lerp_color(top_c, bot_c, k)
            draw.line([(0, y), (WIDTH, y)], fill=c)
        draw_radial_glow(img, (int(WIDTH * 0.32), int(HEIGHT * 0.42)), 540, NEON_PINK, 0.28)
        draw_radial_glow(img, (int(WIDTH * 0.75), int(HEIGHT * 0.58)), 480, NEON_CYAN, 0.24)

    elif env == Environment.MOLECULAR_LAB:
        top_c = (18, 26, 52)
        bot_c = VOID_OBSIDIAN
        for y in range(HEIGHT):
            k = y / HEIGHT
            c = lerp_color(top_c, bot_c, k)
            draw.line([(0, y), (WIDTH, y)], fill=c)
        draw_radial_glow(img, (int(WIDTH * 0.28), int(HEIGHT * 0.35)), 520, NEON_CYAN, 0.26)
        draw_radial_glow(img, (int(WIDTH * 0.82), int(HEIGHT * 0.65)), 460, AURORA_GREEN, 0.22)

    elif env == Environment.CYBER_MATRIX:
        top_c = (12, 10, 26)
        bot_c = COSMIC_INDIGO
        for y in range(HEIGHT):
            k = y / HEIGHT
            c = lerp_color(top_c, bot_c, k)
            draw.line([(0, y), (WIDTH, y)], fill=c)
        # Horizon neon band glow
        draw_radial_glow(img, (int(WIDTH * 0.5), 650), 620, NEON_CYAN, 0.28)
        draw_radial_glow(img, (int(WIDTH * 0.5), 650), 380, NEON_PINK, 0.22)

    elif env == Environment.FINANCIAL_LEDGER:
        top_c = TWILIGHT_DEEP
        bot_c = VOID_OBSIDIAN
        for y in range(HEIGHT):
            k = y / HEIGHT
            c = lerp_color(top_c, bot_c, k)
            draw.line([(0, y), (WIDTH, y)], fill=c)
        draw_radial_glow(img, (int(WIDTH * 0.3), int(HEIGHT * 0.7)), 600, AURORA_GREEN, 0.18)

    elif env == Environment.PARCHMENT_MAP:
        top_c = (32, 22, 18)
        bot_c = VOID_PARCHMENT
        for y in range(HEIGHT):
            k = y / HEIGHT
            c = lerp_color(top_c, bot_c, k)
            draw.line([(0, y), (WIDTH, y)], fill=c)
        draw_radial_glow(img, (int(WIDTH * 0.45), int(HEIGHT * 0.46)), 580, AMBER_GOLD, 0.30)
        draw_radial_glow(img, (int(WIDTH * 0.72), int(HEIGHT * 0.38)), 420, NEON_YELLOW, 0.18)

    return img


# ---------------------------------------------------------------------------
# Procedural Environment Painters
# ---------------------------------------------------------------------------
def paint_space_cosmic(img: Image.Image, t: float, seed: int = 101) -> None:
    """Procedural Kurzgesagt Space / Cosmic Void environment.

    Features drifting multi-tier starfield, Kurzgesagt 4-pointed diamond flares,
    celestial planetary limb arc, and glowing orbital rings.
    """
    # 1. Base void and radial nebula
    base = _base_gradient(Environment.SPACE_COSMIC)
    img.paste(base)
    draw = ImageDraw.Draw(img)

    # 2. Celestial planetary limb in lower-right with glowing atmosphere arc
    limb_cx, limb_cy = 1520, 1580
    limb_r = 940
    draw.ellipse(
        [limb_cx - limb_r, limb_cy - limb_r, limb_cx + limb_r, limb_cy + limb_r],
        fill=(14, 18, 42),
        outline=NEON_CYAN,
        width=2,
    )

    # 3. Concentric tilted orbital rings
    orbit_cx, orbit_cy = WIDTH // 2 + 80, HEIGHT // 2 - 40
    for r_major, r_minor in [(420, 160), (740, 280)]:
        # Draw dashed elliptical orbit arc
        points: list[tuple[float, float]] = []
        for deg in range(0, 360, 4):
            rad = math.radians(deg)
            # Tilt by 20 degrees
            ox = r_major * math.cos(rad)
            oy = r_minor * math.sin(rad)
            tilt = math.radians(18.0)
            rx = orbit_cx + ox * math.cos(tilt) - oy * math.sin(tilt)
            ry = orbit_cy + ox * math.sin(tilt) + oy * math.cos(tilt)
            points.append((rx, ry))
        for i in range(0, len(points) - 1, 2):
            draw.line([points[i], points[i + 1]], fill=(0, 240, 255), width=1)

    # 4. Multi-tier drifting starfield
    rng = random.Random(seed)
    # Layer 1: distant faint background stars
    for _i in range(60):
        sx = (rng.uniform(0, WIDTH) + t * 8.0) % WIDTH
        sy = (rng.uniform(0, HEIGHT) + t * 2.0) % HEIGHT
        r_star = rng.uniform(0.8, 1.8)
        alpha = rng.uniform(120, 240)
        draw.ellipse(
            [sx - r_star, sy - r_star, sx + r_star, sy + r_star], fill=(int(alpha), int(alpha), 255)
        )

    # Layer 2: medium twinkling stars
    for i in range(18):
        sx = (rng.uniform(40, WIDTH - 40) + t * 14.0) % WIDTH
        sy = (rng.uniform(40, HEIGHT - 40) + t * 4.0) % HEIGHT
        twinkle = 0.5 + 0.5 * math.sin(2.0 * math.pi * 0.4 * t + i * 1.3)
        r_star = 2.0 + 1.2 * twinkle
        draw.ellipse([sx - r_star, sy - r_star, sx + r_star, sy + r_star], fill=WHITE)

    # Layer 3: Kurzgesagt 4-pointed diamond flare stars
    diamond_coords = [
        (240, 210, 28.0, NEON_YELLOW),
        (580, 160, 22.0, NEON_CYAN),
        (1380, 220, 32.0, NEON_YELLOW),
        (880, 340, 20.0, NEON_PINK),
        (340, 720, 24.0, NEON_CYAN),
        (1680, 680, 26.0, NEON_YELLOW),
    ]
    for dx, dy, size, color in diamond_coords:
        # Subtle drift
        drift_x = dx + 12.0 * math.sin(0.3 * t + dx)
        drift_y = dy + 8.0 * math.cos(0.25 * t + dy)
        pulse_size = size * (0.9 + 0.15 * math.sin(2.0 * math.pi * 0.5 * t + dx))
        draw_star_diamond(draw, (drift_x, drift_y), pulse_size, color)

    # Constellation connection lines
    c_pts = [(240, 210), (380, 290), (580, 160)]
    for i in range(len(c_pts) - 1):
        draw.line([c_pts[i], c_pts[i + 1]], fill=(0, 240, 255), width=1)
        draw.ellipse(
            [c_pts[i][0] - 3, c_pts[i][1] - 3, c_pts[i][0] + 3, c_pts[i][1] + 3],
            outline=NEON_CYAN,
            width=1,
        )


def paint_molecular_lab(img: Image.Image, t: float, seed: int = 202) -> None:
    """Procedural Kurzgesagt Molecular Lab environment.

    Features drifting hexagonal lattice (benzene grid), animated rotating molecular
    compound structures, and rising bio-ion micro-particles.
    """
    base = _base_gradient(Environment.MOLECULAR_LAB)
    img.paste(base)
    draw = ImageDraw.Draw(img)

    # 1. Subtle hexagonal lattice background (benzene rings)
    hex_r = 50.0
    hex_w = hex_r * math.sqrt(3)
    hex_h = hex_r * 1.5
    offset_x = (t * 16.0) % (hex_w * 2)

    for row in range(-1, int(HEIGHT / hex_h) + 2):
        for col in range(-1, int(WIDTH / hex_w) + 2):
            cx = col * hex_w + (hex_w / 2 if row % 2 else 0) - offset_x
            cy = row * hex_h
            if -hex_r <= cx <= WIDTH + hex_r and -hex_r <= cy <= HEIGHT + hex_r:
                corners = [
                    (cx + hex_r * math.sin(math.radians(a)), cy + hex_r * math.cos(math.radians(a)))
                    for a in range(0, 360, 60)
                ]
                draw.polygon(corners, outline=(22, 48, 88), width=1)

    # 2. Floating molecular compound complexes
    # Compound 1: Central atom with 3 satellite covalent bonds
    c1_x = 460 + 20.0 * math.sin(0.2 * t)
    c1_y = 380 + 15.0 * math.cos(0.25 * t)
    rot1 = 0.15 * t

    # Central nucleus
    draw.ellipse(
        [c1_x - 16, c1_y - 16, c1_x + 16, c1_y + 16], fill=NEON_CYAN, outline=WHITE, width=2
    )
    # Concentric orbital ring
    draw.ellipse([c1_x - 34, c1_y - 34, c1_x + 34, c1_y + 34], outline=(0, 240, 255), width=1)

    # Satellites
    for i, angle_deg in enumerate([30, 150, 270]):
        rad = math.radians(angle_deg) + rot1
        sat_x = c1_x + 85.0 * math.cos(rad)
        sat_y = c1_y + 85.0 * math.sin(rad)
        draw.line([(c1_x, c1_y), (sat_x, sat_y)], fill=(120, 220, 255), width=3)
        col = NEON_PINK if i == 0 else AURORA_GREEN
        draw.ellipse(
            [sat_x - 10, sat_y - 10, sat_x + 10, sat_y + 10], fill=col, outline=WHITE, width=1
        )

    # Compound 2: Double-bonded carbon chain
    c2_x = 1440 - 18.0 * math.cos(0.18 * t)
    c2_y = 340 + 20.0 * math.sin(0.22 * t)
    rot2 = -0.12 * t
    for offset, color in [(-50, NEON_PINK), (50, NEON_CYAN)]:
        rad = rot2
        node_x = c2_x + offset * math.cos(rad)
        node_y = c2_y + offset * math.sin(rad)
        draw.ellipse(
            [node_x - 14, node_y - 14, node_x + 14, node_y + 14], fill=color, outline=WHITE, width=2
        )
    # Double bond lines
    perp = rot2 + math.pi / 2
    d_x = 5.0 * math.cos(perp)
    d_y = 5.0 * math.sin(perp)
    p1 = (c2_x - 50 * math.cos(rot2), c2_y - 50 * math.sin(rot2))
    p2 = (c2_x + 50 * math.cos(rot2), c2_y + 50 * math.sin(rot2))
    draw.line([(p1[0] + d_x, p1[1] + d_y), (p2[0] + d_x, p2[1] + d_y)], fill=WHITE, width=2)
    draw.line([(p1[0] - d_x, p1[1] - d_y), (p2[0] - d_x, p2[1] - d_y)], fill=WHITE, width=2)

    # 3. Rising bio-ion particles (rising micro-bubbles with soft glow)
    rng = random.Random(seed)
    for i in range(30):
        bx = rng.uniform(80, WIDTH - 80)
        speed = rng.uniform(35.0, 75.0)
        by = (rng.uniform(0, HEIGHT) - t * speed) % HEIGHT
        r_bubble = rng.uniform(2.0, 4.5)
        color = NEON_CYAN if i % 2 == 0 else AURORA_GREEN
        draw.ellipse([bx - r_bubble, by - r_bubble, bx + r_bubble, by + r_bubble], fill=color)


def paint_cyber_matrix(img: Image.Image, t: float, seed: int = 303) -> None:
    """Procedural Kurzgesagt Cyber Matrix environment.

    Features 3D perspective digital grid floor, neon horizon glow band,
    circuit trace vectors with 45-degree bends, and cascading data packet pulses.
    """
    base = _base_gradient(Environment.CYBER_MATRIX)
    img.paste(base)
    draw = ImageDraw.Draw(img)

    horizon_y = 640
    vp_x = WIDTH // 2

    # 1. Perspective grid ground plane
    # Receding radial lines toward vanishing point
    for x_step in range(-800, WIDTH + 800, 120):
        draw.line([(vp_x, horizon_y), (x_step, HEIGHT)], fill=(20, 50, 90), width=1)

    # Transverse moving horizontal lines with logarithmic perspective
    grid_speed = (t * 1.2) % 1.0
    for i in range(1, 14):
        k = ((i + grid_speed) / 14.0) ** 2.2
        y = horizon_y + int((HEIGHT - horizon_y) * k)
        alpha_val = int(255 * (0.3 + 0.6 * k))
        draw.line([(0, y), (WIDTH, y)], fill=(0, min(240, alpha_val), min(255, alpha_val)), width=1)

    # 2. Glowing horizon accent line
    draw.line([(0, horizon_y), (WIDTH, horizon_y)], fill=NEON_CYAN, width=2)
    draw.line([(0, horizon_y + 2), (WIDTH, horizon_y + 2)], fill=NEON_PINK, width=1)

    # 3. Circuit trace vectors in upper void
    traces: list[list[tuple[int, int]]] = [
        [(180, 80), (180, 240), (280, 340), (460, 340)],
        [(580, 60), (700, 180), (700, 320)],
        [(1240, 90), (1380, 230), (1380, 360), (1520, 360)],
        [(1720, 120), (1720, 280), (1640, 360)],
    ]
    for trace in traces:
        for i in range(len(trace) - 1):
            draw.line([trace[i], trace[i + 1]], fill=(30, 80, 140), width=2)
        # Solder pad at end
        end_pt = trace[-1]
        draw.ellipse(
            [end_pt[0] - 6, end_pt[1] - 6, end_pt[0] + 6, end_pt[1] + 6],
            fill=NEON_CYAN,
            outline=WHITE,
            width=1,
        )

    # 4. Cascading digital data packets
    rng = random.Random(seed)
    for i in range(16):
        px = rng.choice([280, 460, 700, 960, 1240, 1380, 1640])
        speed = rng.uniform(180.0, 360.0)
        py = (rng.uniform(0, horizon_y) + t * speed) % horizon_y
        pw, ph = rng.uniform(6.0, 12.0), rng.uniform(14.0, 28.0)
        draw.rectangle(
            [px - pw / 2, py, px + pw / 2, py + ph], fill=NEON_PINK if i % 3 == 0 else NEON_CYAN
        )

    # 5. Technical alignment crosshairs [+]
    crosshairs = [(320, 480), (960, 280), (1600, 460)]
    for ch_x, ch_y in crosshairs:
        s = 8
        draw.line([(ch_x - s, ch_y), (ch_x + s, ch_y)], fill=NEON_YELLOW, width=2)
        draw.line([(ch_x, ch_y - s), (ch_x, ch_y + s)], fill=NEON_YELLOW, width=2)


def paint_financial_ledger(img: Image.Image, t: float, seed: int = 404) -> None:
    """Procedural Kurzgesagt financial ledger environment.

    Features ruled ledger paper scrolling upward, column rules, a scrolling candlestick
    chart with a cyan trend line, and a Solar Yellow total rule with diamond flares.
    """
    base = _base_gradient(Environment.FINANCIAL_LEDGER)
    img.paste(base)
    draw = ImageDraw.Draw(img)
    phase = (seed % 97) * 0.01

    # 1. Ruled ledger paper scrolling upward
    row_h = 54.0
    y = -row_h + (t * 10.0) % row_h
    while y < HEIGHT + row_h:
        draw.line([(0, y), (WIDTH, y)], fill=(22, 40, 72), width=1)
        y += row_h
    for x in (300, 760, 1220, 1680):
        draw.line([(x, 0), (x, HEIGHT)], fill=(30, 56, 96), width=2)

    # 2. Candlestick chart scrolling leftwards; prices are a deterministic wave per candle index
    spacing = 62.0
    n0 = int((t * 30.0) // spacing)
    frac = (t * 30.0) % spacing
    trend: list[tuple[float, float]] = []
    for j in range(-1, 24):
        i = n0 + j
        x = 1000.0 + j * spacing - frac
        open_p = 540.0 + 60.0 * math.sin(0.7 * i + phase) + 30.0 * math.sin(1.9 * i)
        close_p = open_p + 22.0 * math.sin(1.3 * i + 2.0)
        high_p = max(open_p, close_p) + 10.0 + 6.0 * abs(math.cos(i))
        low_p = min(open_p, close_p) - 10.0 - 6.0 * abs(math.sin(i))
        color = AURORA_GREEN if close_p < open_p else NEON_PINK  # pixel y grows downwards
        draw.line([(x, high_p), (x, low_p)], fill=color, width=2)
        top, bot = sorted((open_p, close_p))
        draw.rectangle([x - 14, top, x + 14, max(bot, top + 4.0)], fill=color)
        trend.append((x, close_p))
    draw.line(trend, fill=NEON_CYAN, width=3)

    # 3. Solar Yellow total rule and pulsing ledger flares
    total_y = 930
    draw.line([(1100, total_y), (1820, total_y)], fill=NEON_YELLOW, width=3)
    draw.line([(1100, total_y + 9), (1820, total_y + 9)], fill=NEON_YELLOW, width=1)
    for k, (fx, fy, size) in enumerate(
        [(240.0, 200.0, 24.0), (620.0, 150.0, 18.0), (1480.0, 210.0, 22.0)]
    ):
        pulse = size * (0.9 + 0.15 * math.sin(2.0 * math.pi * 0.5 * t + k))
        draw_star_diamond(draw, (fx, fy + 6.0 * math.cos(0.25 * t + k)), pulse, NEON_YELLOW)


def paint_parchment_map(img: Image.Image, t: float, seed: int = 505) -> None:
    """Procedural Kurzgesagt Historical Parchment Map environment.

    Features warm lantern illumination, 16-point wind rose nautical compass,
    flowing sinusoidal topographic elevation contours, and radiating rhumb lines.
    """
    base = _base_gradient(Environment.PARCHMENT_MAP)
    img.paste(base)
    draw = ImageDraw.Draw(img)

    # 1. 16-point Wind Rose / Nautical Navigation Compass
    rose_cx, rose_cy = int(WIDTH * 0.74), int(HEIGHT * 0.44)

    # Radiating Rhumb Lines spanning the canvas
    for deg in range(0, 360, 15):
        rad = math.radians(deg)
        rx = rose_cx + 1800.0 * math.cos(rad)
        ry = rose_cy + 1800.0 * math.sin(rad)
        draw.line([(rose_cx, rose_cy), (rx, ry)], fill=(75, 52, 36), width=1)

    # Outer decorative compass rings
    for r in [90, 150, 175]:
        draw.ellipse(
            [rose_cx - r, rose_cy - r, rose_cx + r, rose_cy + r],
            outline=AMBER_GOLD,
            width=1 if r < 170 else 2,
        )

    # Faceted 8-point navigational star points
    num_points = 8
    outer_r = 140.0
    inner_r = 45.0
    for i in range(num_points):
        angle = i * (2.0 * math.pi / num_points)
        angle_next = (i + 0.5) * (2.0 * math.pi / num_points)
        angle_prev = (i - 0.5) * (2.0 * math.pi / num_points)

        tip_x = rose_cx + outer_r * math.cos(angle)
        tip_y = rose_cy + outer_r * math.sin(angle)
        right_x = rose_cx + inner_r * math.cos(angle_next)
        right_y = rose_cy + inner_r * math.sin(angle_next)
        left_x = rose_cx + inner_r * math.cos(angle_prev)
        left_y = rose_cy + inner_r * math.sin(angle_prev)

        # Light face
        draw.polygon(
            [(rose_cx, rose_cy), (tip_x, tip_y), (right_x, right_y)],
            fill=NEON_YELLOW if i % 2 == 0 else AMBER_GOLD,
        )
        # Shaded face
        draw.polygon([(rose_cx, rose_cy), (tip_x, tip_y), (left_x, left_y)], fill=(60, 42, 28))

    # Center jewel of compass
    draw.ellipse(
        [rose_cx - 12, rose_cy - 12, rose_cx + 12, rose_cy + 12],
        fill=NEON_YELLOW,
        outline=WHITE,
        width=2,
    )

    # 2. Flowing Topographic Contour Lines
    contour_base_y = 680
    for idx, y_offset in enumerate([0, 55, 110, 165, 220]):
        pts: list[tuple[float, float]] = []
        for x in range(0, WIDTH + 20, 20):
            # Harmonic elevation waves with slow drift
            y = (
                contour_base_y
                + y_offset
                + 35.0 * math.sin(0.003 * x + 0.1 * t + idx * 0.8)
                + 18.0 * math.cos(0.007 * x - 0.15 * t + idx * 1.2)
            )
            pts.append((x, y))
        draw.line(pts, fill=AMBER_GOLD if idx == 0 else (110, 78, 48), width=2 if idx == 0 else 1)

    # 3. Nautical star constellation markers
    rng = random.Random(seed)
    for _ in range(12):
        nx = rng.uniform(80, WIDTH * 0.55)
        ny = rng.uniform(80, HEIGHT * 0.65)
        draw_star_diamond(draw, (nx, ny), rng.uniform(10.0, 18.0), AMBER_GOLD, WHITE)


ENVIRONMENT_PAINTERS: dict[Environment, Callable[[Image.Image, float, int], None]] = {
    Environment.SPACE_COSMIC: paint_space_cosmic,
    Environment.MOLECULAR_LAB: paint_molecular_lab,
    Environment.CYBER_MATRIX: paint_cyber_matrix,
    Environment.FINANCIAL_LEDGER: paint_financial_ledger,
    Environment.PARCHMENT_MAP: paint_parchment_map,
}


# ---------------------------------------------------------------------------
# Ken-Burns Camera Drift Engine
# ---------------------------------------------------------------------------
def _ken_burns_params(
    progress: float,
    zoom_min: float = 1.02,
    zoom_max: float = 1.06,
    drift_angle: float = 0.0,
    drift_distance: float = 24.0,
) -> tuple[float, float, float]:
    """Return ``(zoom, dx, dy)`` for the Ken-Burns camera at ``progress`` through a scene.

    Zoom and pan are linear in progress, so the camera keeps moving across the whole scene
    (1.02x to 1.06x by default) instead of stalling at each sentence boundary.
    """
    p = min(1.0, max(0.0, float(progress)))
    zoom = lerp(zoom_min, zoom_max, p)
    offset = drift_distance * (p - 0.5)
    return zoom, offset * math.cos(drift_angle), offset * math.sin(drift_angle)


def apply_camera_transform(
    img: Image.Image, zoom: float, dx: float = 0.0, dy: float = 0.0
) -> Image.Image:
    """Crop ``img`` to ``1/zoom`` of its size around the drifted centre and scale back to full frame."""
    w, h = img.size
    zoom = max(1.0, float(zoom))
    crop_w = max(1, int(round(w / zoom)))
    crop_h = max(1, int(round(h / zoom)))
    x0 = int(round(w / 2.0 + dx - crop_w / 2.0))
    y0 = int(round(h / 2.0 + dy - crop_h / 2.0))
    x0 = max(0, min(w - crop_w, x0))
    y0 = max(0, min(h - crop_h, y0))
    # Resample straight from the source box (no crop copy). BILINEAR, not BICUBIC: the bicubic
    # full-frame resize alone cost ~55 ms per 1080p frame on the reference machine.
    return img.resize((w, h), Image.BILINEAR, box=(x0, y0, x0 + crop_w, y0 + crop_h))


def apply_ken_burns(
    img: Image.Image,
    progress: float,
    zoom_min: float = 1.02,
    zoom_max: float = 1.06,
    drift_angle: float = 0.0,
    drift_distance: float = 24.0,
) -> Image.Image:
    """Apply the continuous Ken-Burns camera (1.02x to 1.06x zoom plus a pan) at ``progress``."""
    zoom, dx, dy = _ken_burns_params(progress, zoom_min, zoom_max, drift_angle, drift_distance)
    return apply_camera_transform(img, zoom, dx, dy)


def _tint(img: Image.Image, color: RGB, amount: float) -> Image.Image:
    """Blend ``img`` towards a flat ``color`` by ``amount`` through a per-channel lookup table.

    Equivalent to ``Image.blend(img, solid_color, amount)`` but avoids allocating a full-frame
    solid image and a second full-frame blend on every transition frame.
    """
    keep = 1.0 - amount
    lut: list[int] = []
    for c in color:
        lut.extend(round(v * keep + c * amount) for v in range(256))
    return img.point(lut)


def _beat_camera(beat: SentenceBeat, t: float) -> tuple[float, float, float]:
    """Camera ``(zoom, dx, dy)`` for ``beat`` at section time ``t``, measured over the beat's own span."""
    progress = (t - beat.start_time) / beat.duration
    return _ken_burns_params(progress, beat.zoom_start, beat.zoom_end, beat.camera_drift_angle)


# ---------------------------------------------------------------------------
# Background Director Class
# ---------------------------------------------------------------------------
class BackgroundDirector:
    """Orchestrates dynamic, sentence-driven background transitions and continuous camera drift."""

    def __init__(
        self,
        transition_duration: float = 0.5,
        enable_ken_burns: bool = True,
        enable_transition_glow: bool = True,
    ) -> None:
        """Initialize the background director.

        Args:
            transition_duration: Crossfade duration in seconds, clamped to 400-600ms (default 0.5s).
            enable_ken_burns: Whether to apply continuous 1.02x-1.06x camera drift.
            enable_transition_glow: Whether to inject Kurzgesagt atmospheric glow during transitions.
        """
        self.transition_duration = max(
            TRANSITION_MIN_SECONDS, min(TRANSITION_MAX_SECONDS, float(transition_duration))
        )
        self.enable_ken_burns = enable_ken_burns
        self.enable_transition_glow = enable_transition_glow

    def plan_section(
        self,
        narration: str,
        duration: float,
        words: Sequence[dict[str, Any]] | None = None,
        section_idx: int = 0,
        genre: str = "",
    ) -> list[SentenceBeat]:
        """Generate sentence beats and assign environments for a section."""
        return build_sentence_beats(
            narration=narration,
            duration=duration,
            words=words,
            section_idx=section_idx,
            genre=genre,
        )

    def render_environment(
        self,
        env: Environment | str,
        t: float,
        seed: int = 42,
    ) -> Image.Image:
        """Render a single standalone environment at time ``t``."""
        resolved = ENVIRONMENT_ALIASES.get(str(env).lower(), Environment.SPACE_COSMIC)
        img = Image.new("RGB", (WIDTH, HEIGHT))
        painter = ENVIRONMENT_PAINTERS.get(resolved, paint_space_cosmic)
        painter(img, t, seed)
        return img

    @staticmethod
    def _beat_index(t: float, beats: Sequence[SentenceBeat]) -> int:
        """Index of the beat containing ``t``; times past the last beat resolve to the last beat."""
        cur_idx = 0
        for i, beat in enumerate(beats):
            if beat.start_time <= t <= beat.end_time:
                return i
            if t > beat.end_time:
                cur_idx = i
        return cur_idx

    def render_frame(
        self,
        t: float,
        beats: Sequence[SentenceBeat],
        apply_camera: bool = True,
    ) -> Image.Image:
        """Render the background at section time ``t``: sentence environment, crossfade and camera.

        Each beat runs one Ken-Burns zoom from ``zoom_start`` to ``zoom_end`` across its own span.
        Inside a crossfade window the two beats' cameras are blended with the same eased ``k`` as
        the palette, so the camera stays continuous across the boundary.

        Args:
            t: Current time in seconds relative to section start.
            beats: Planned sentence beats for this section.
            apply_camera: Whether to apply continuous Ken-Burns drift.
        """
        camera = apply_camera and self.enable_ken_burns
        if not beats:
            frame = self.render_environment(Environment.SPACE_COSMIC, t)
            if camera:
                return apply_ken_burns(frame, (t % 4.0) / 4.0)
            return frame

        cur_idx = self._beat_index(t, beats)
        cur = beats[cur_idx]
        half = self.transition_duration / 2.0

        first_idx = last_idx = cur_idx
        boundary = 0.0
        if cur_idx < len(beats) - 1 and cur.end_time - half <= t <= cur.end_time + half:
            last_idx = cur_idx + 1
            boundary = cur.end_time
        elif cur_idx > 0 and cur.start_time - half <= t <= cur.start_time + half:
            first_idx = cur_idx - 1
            boundary = cur.start_time

        if first_idx == last_idx:
            frame = self.render_environment(cur.environment, t, seed=100 + cur_idx)
            if camera:
                zoom, dx, dy = _beat_camera(cur, t)
                frame = apply_camera_transform(frame, zoom, dx, dy)
            return frame

        # Crossfade window centred on the boundary: u runs 0 to 1 across transition_duration.
        a, b = beats[first_idx], beats[last_idx]
        u = min(1.0, max(0.0, (t - (boundary - half)) / self.transition_duration))
        k = cubic_hermite(u)
        frame = Image.blend(
            self.render_environment(a.environment, t, seed=100 + first_idx),
            self.render_environment(b.environment, t, seed=100 + last_idx),
            k,
        )

        if self.enable_transition_glow:
            peak = 4.0 * u * (1.0 - u)  # parabola peaking at 1.0 when u=0.5
            if peak > 0.05:
                flash_color = (
                    NEON_CYAN
                    if b.environment in (Environment.SPACE_COSMIC, Environment.CYBER_MATRIX)
                    else AMBER_GOLD
                )
                frame = _tint(frame, flash_color, peak * 0.12)

        if camera:
            za, dxa, dya = _beat_camera(a, t)
            zb, dxb, dyb = _beat_camera(b, t)
            frame = apply_camera_transform(
                frame, lerp(za, zb, k), lerp(dxa, dxb, k), lerp(dya, dyb, k)
            )
        return frame


# Global singleton instance for easy import and reuse
_DEFAULT_DIRECTOR: BackgroundDirector | None = None


def get_background_director() -> BackgroundDirector:
    """Return the shared global BackgroundDirector instance."""
    global _DEFAULT_DIRECTOR
    if _DEFAULT_DIRECTOR is None:
        _DEFAULT_DIRECTOR = BackgroundDirector()
    return _DEFAULT_DIRECTOR
