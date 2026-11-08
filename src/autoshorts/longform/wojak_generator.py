"""Procedural Wojak generator for long-form country comparison parody videos.

Generates authentic, hand-drawn vector-style Wojak characters on an 800x1200 RGBA canvas
with full parametric control over age, build/height, race/skin tone, gender, facial
expression, hair/headwear, clothing, and country/scenario-specific props.

Inspired by classic Wojak satire and country comparison parody videos in the style of SapX.
Every character is rendered deterministically or procedurally on the spot using pure Pillow,
with thick hand-drawn ink contours, nuanced facial wrinkle and expression systems, and
rich context props.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Constants and Canonical Options
# ---------------------------------------------------------------------------

CANVAS_WIDTH = 800
CANVAS_HEIGHT = 1200

AGES = ("youth", "young_adult", "middle_aged", "elderly")
HEIGHTS = ("short", "average", "tall")
BUILDS = ("slim", "average", "broad", "buff")
RACES = ("fair", "olive", "east_asian", "south_asian", "middle_eastern", "black", "latino")
GENDERS = ("male", "female", "nonna", "tradwife")
EXPRESSIONS = (
    "crying",
    "npc",
    "chad",
    "doomer",
    "smug",
    "shocked",
    "bandaged",
    "happy",
)
HAIR_STYLES = (
    "bald",
    "messy_dark_hair",
    "blonde_parted",
    "ginger_curls_beard",
    "grey_bun",
    "headscarf",
    "beret",
    "doctor_surgical_cap",
    "nurse_cap",
    "cosmetic_bandages",
    "headband",
)
CLOTHINGS = (
    "hospital_gown",
    "doctor_coat",
    "nurse_scrubs",
    "business_suit",
    "casual_hoodie",
    "apron",
    "trench_coat",
)
PROPS = (
    "none",
    "usa_bill",
    "uk_trolley_tea",
    "sweden_phone_queue",
    "india_stethoscope",
    "korea_bandages",
    "portugal_tart_beret",
    "italy_pasta_pot",
    "netherlands_paracetamol",
    "germany_fax_clipboard",
    "canada_calendar",
    "ireland_bed_thumbsup",
    "france_coffee_cigarette",
    "turkey_hair_transplant",
    "generic_bill",
    "generic_phone",
    "generic_clipboard",
    "generic_cup",
)

# Authentic ink and palette tokens
INK_BLACK = (18, 18, 22, 255)
INK_THIN = (25, 25, 30, 230)
WHITE = (255, 255, 255, 255)
TEAR_BLUE = (56, 189, 248, 255)
TEAR_LIGHT = (186, 230, 253, 240)
TEAR_OUTLINE = (2, 132, 199, 255)
BLOOD_RED = (220, 38, 38, 255)
BRUISE_PURPLE = (147, 51, 234, 90)
BRUISE_YELLOW = (234, 179, 8, 80)
BLUSH_PINK = (244, 114, 182, 70)

# Race / Ethnicity skin tones
SKIN_TONES: dict[str, tuple[int, int, int, int]] = {
    "fair": (252, 236, 222, 255),
    "olive": (232, 196, 158, 255),
    "east_asian": (250, 230, 202, 255),
    "south_asian": (178, 126, 88, 255),
    "middle_eastern": (214, 168, 126, 255),
    "black": (108, 68, 46, 255),
    "latino": (216, 162, 118, 255),
}

NPC_GREY_TONE = (212, 216, 220, 255)

# Hair colors
HAIR_COLORS: dict[str, tuple[int, int, int, int]] = {
    "bald": (0, 0, 0, 0),
    "messy_dark_hair": (32, 28, 28, 255),
    "blonde_parted": (245, 208, 92, 255),
    "ginger_curls_beard": (217, 85, 34, 255),
    "grey_bun": (205, 212, 220, 255),
    "headscarf": (185, 28, 28, 255),  # deep warm red babushka
    "beret": (24, 24, 27, 255),
    "doctor_surgical_cap": (2, 132, 199, 255),  # medical sky blue
    "nurse_cap": (250, 250, 252, 255),
    "cosmetic_bandages": (244, 244, 248, 255),
    "headband": (28, 28, 32, 255),
}

# Font candidates for crisp in-image text
_FONT_CANDIDATES = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/Montserrat-ExtraBold.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)


@lru_cache(maxsize=32)
def _get_font(size: int, bold: bool = True) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Retrieve cached TrueType font or fallback to default."""
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)  # type: ignore[call-arg]
    except TypeError:
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Data Specification
# ---------------------------------------------------------------------------


@dataclass
class WojakSpec:
    """Complete parametric specification for procedural Wojak generation."""

    country: str = "generic"
    setting: str = "hospital"
    scenario: str = "waiting"
    age: str = "young_adult"  # youth, young_adult, middle_aged, elderly
    gender: str = "male"  # male, female, nonna, tradwife
    race: str = "fair"  # fair, olive, east_asian, south_asian, middle_eastern, black, latino
    height: str = "average"  # short, average, tall
    build: str = "average"  # slim, average, broad, buff
    expression: str = "npc"  # crying, npc, chad, doomer, smug, shocked, bandaged, happy
    hair: str = "bald"
    clothing: str = "hospital_gown"
    prop: str = "none"
    skin_tone: tuple[int, int, int, int] | None = None
    hair_color: tuple[int, int, int, int] | None = None
    clothing_color: tuple[int, int, int, int] | None = None
    seed: int = 42
    custom_text: str | None = None


# ---------------------------------------------------------------------------
# Synonyms and Normalization
# ---------------------------------------------------------------------------

_AGE_SYNONYMS = {
    "young": "youth",
    "teen": "youth",
    "kid": "youth",
    "child": "youth",
    "young_adult": "young_adult",
    "adult": "young_adult",
    "middle": "middle_aged",
    "middle_age": "middle_aged",
    "middle_aged": "middle_aged",
    "mid": "middle_aged",
    "old": "elderly",
    "elderly": "elderly",
    "senior": "elderly",
    "grandpa": "elderly",
    "grandma": "elderly",
    "babushka": "elderly",
}

_BUILD_SYNONYMS = {
    "skinny": "slim",
    "thin": "slim",
    "slim": "slim",
    "regular": "average",
    "normal": "average",
    "average": "average",
    "wide": "broad",
    "large": "broad",
    "broad": "broad",
    "heavy": "broad",
    "buff": "buff",
    "muscular": "buff",
    "chad": "buff",
    "gigachad": "buff",
    "jacked": "buff",
}

_HEIGHT_SYNONYMS = {
    "short": "short",
    "small": "short",
    "medium": "average",
    "average": "average",
    "tall": "tall",
    "huge": "tall",
}

_RACE_SYNONYMS = {
    "white": "fair",
    "pale": "fair",
    "caucasian": "fair",
    "fair": "fair",
    "olive": "olive",
    "mediterranean": "olive",
    "greek": "olive",
    "italian": "olive",
    "asian": "east_asian",
    "east_asian": "east_asian",
    "chinese": "east_asian",
    "japanese": "east_asian",
    "korean": "east_asian",
    "south_asian": "south_asian",
    "indian": "south_asian",
    "desi": "south_asian",
    "middle_eastern": "middle_eastern",
    "arab": "middle_eastern",
    "persian": "middle_eastern",
    "turkish": "middle_eastern",
    "black": "black",
    "african": "black",
    "dark": "black",
    "latino": "latino",
    "hispanic": "latino",
    "amber": "latino",
}

_GENDER_SYNONYMS = {
    "male": "male",
    "man": "male",
    "boy": "male",
    "guy": "male",
    "female": "female",
    "woman": "female",
    "girl": "female",
    "lady": "female",
    "nonna": "nonna",
    "grandma": "nonna",
    "grandmother": "nonna",
    "babushka": "nonna",
    "tradwife": "tradwife",
    "trad": "tradwife",
    "wife": "tradwife",
}

_EXPRESSION_SYNONYMS = {
    "crying": "crying",
    "cry": "crying",
    "tears": "crying",
    "weeping": "crying",
    "sad": "crying",
    "npc": "npc",
    "calm": "npc",
    "deadpan": "npc",
    "blank": "npc",
    "stoic": "npc",
    "neutral": "npc",
    "chad": "chad",
    "confident": "chad",
    "gigachad": "chad",
    "smirk": "smug",
    "smug": "smug",
    "arrogant": "smug",
    "doomer": "doomer",
    "tired": "doomer",
    "exhausted": "doomer",
    "depressed": "doomer",
    "shocked": "shocked",
    "screaming": "shocked",
    "soyjak": "shocked",
    "panic": "shocked",
    "scared": "shocked",
    "bandaged": "bandaged",
    "bruised": "bandaged",
    "surgery": "bandaged",
    "cosmetic": "bandaged",
    "happy": "happy",
    "smiling": "happy",
    "smile": "happy",
    "cheerful": "happy",
    "thumbsup": "happy",
}

_CLOTHING_SYNONYMS = {
    "gown": "hospital_gown",
    "patient_gown": "hospital_gown",
    "hospital_gown": "hospital_gown",
    "patient": "hospital_gown",
    "doctor": "doctor_coat",
    "lab_coat": "doctor_coat",
    "doctor_coat": "doctor_coat",
    "coat": "doctor_coat",
    "scrubs": "nurse_scrubs",
    "nurse_scrubs": "nurse_scrubs",
    "nurse": "nurse_scrubs",
    "suit": "business_suit",
    "business_suit": "business_suit",
    "formal": "business_suit",
    "hoodie": "casual_hoodie",
    "casual_hoodie": "casual_hoodie",
    "jacket": "casual_hoodie",
    "casual": "casual_hoodie",
    "apron": "apron",
    "cooking": "apron",
    "chef": "apron",
    "trench": "trench_coat",
    "trench_coat": "trench_coat",
}

_PROP_SYNONYMS = {
    "usa_bill": "usa_bill",
    "bill": "usa_bill",
    "medical_bill": "usa_bill",
    "invoice": "usa_bill",
    "scroll": "usa_bill",
    "uk_trolley_tea": "uk_trolley_tea",
    "tea": "uk_trolley_tea",
    "trolley": "uk_trolley_tea",
    "nhs": "uk_trolley_tea",
    "sweden_phone_queue": "sweden_phone_queue",
    "phone": "sweden_phone_queue",
    "queue": "sweden_phone_queue",
    "hold": "sweden_phone_queue",
    "india_stethoscope": "india_stethoscope",
    "stethoscope": "india_stethoscope",
    "korea_bandages": "korea_bandages",
    "bandages": "korea_bandages",
    "cosmetics": "korea_bandages",
    "mirror": "korea_bandages",
    "portugal_tart_beret": "portugal_tart_beret",
    "tart": "portugal_tart_beret",
    "pastel": "portugal_tart_beret",
    "pastel_de_nata": "portugal_tart_beret",
    "italy_pasta_pot": "italy_pasta_pot",
    "pasta": "italy_pasta_pot",
    "pot": "italy_pasta_pot",
    "spaghetti": "italy_pasta_pot",
    "lasagna": "italy_pasta_pot",
    "netherlands_paracetamol": "netherlands_paracetamol",
    "paracetamol": "netherlands_paracetamol",
    "pills": "netherlands_paracetamol",
    "germany_fax_clipboard": "germany_fax_clipboard",
    "clipboard": "germany_fax_clipboard",
    "fax": "germany_fax_clipboard",
    "din": "germany_fax_clipboard",
    "canada_calendar": "canada_calendar",
    "calendar": "canada_calendar",
    "2029": "canada_calendar",
    "mri": "canada_calendar",
    "ireland_bed_thumbsup": "ireland_bed_thumbsup",
    "thumbsup": "ireland_bed_thumbsup",
    "thumbs_up": "ireland_bed_thumbsup",
    "bed": "ireland_bed_thumbsup",
    "france_coffee_cigarette": "france_coffee_cigarette",
    "coffee": "france_coffee_cigarette",
    "espresso": "france_coffee_cigarette",
    "cigarette": "france_coffee_cigarette",
    "strike": "france_coffee_cigarette",
    "turkey_hair_transplant": "turkey_hair_transplant",
    "hair_transplant": "turkey_hair_transplant",
    "transplant": "turkey_hair_transplant",
    "generic_bill": "generic_bill",
    "generic_phone": "generic_phone",
    "generic_clipboard": "generic_clipboard",
    "generic_cup": "generic_cup",
    "none": "none",
}


def _norm(text: Any, synonyms: dict[str, str], default: str) -> str:
    """Normalize input string against a synonym table with fallback."""
    if text is None:
        return default
    if not isinstance(text, str):
        text = str(text)
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", text.strip().lower()).strip("_")
    if clean in synonyms:
        return synonyms[clean]
    for key, target in synonyms.items():
        if key in clean:
            return target
    return default


def _calc_shadow(rgb: tuple[int, int, int, int], factor: float = 0.8) -> tuple[int, int, int, int]:
    """Calculate shadow color by darkening and slightly shifting tint."""
    r, g, b, a = rgb
    return (
        max(0, int(r * factor * 0.95)),
        max(0, int(g * factor * 0.92)),
        max(0, int(b * factor * 0.90)),
        a,
    )


# ---------------------------------------------------------------------------
# Dynamic Context Parser
# ---------------------------------------------------------------------------


def spec_from_country_and_context(
    country: str,
    setting: str,
    scenario: str,
    age: str | None = None,
    gender: str | None = None,
    race: str | None = None,
    height: str | None = None,
    build: str | None = None,
    expression: str | None = None,
    clothing: str | None = None,
    prop: str | None = None,
    hair: str | None = None,
) -> WojakSpec:
    """Build a procedural WojakSpec adapted to country, scene setting, and comedic scenario.

    Explicit parameter overrides take top priority over context defaults.
    """
    c_lower = (country or "").lower().strip()
    s_lower = (setting or "").lower().strip()
    sc_lower = (scenario or "").lower().strip()
    all_text = f"{c_lower} {s_lower} {sc_lower}"

    # Default archetypes
    spec = WojakSpec(
        country=country or "generic",
        setting=setting or "hospital",
        scenario=scenario or "standard",
    )

    # 1. Country & Archetype recognition
    if any(k in c_lower for k in ("usa", "united states", "america")):
        spec.expression = "crying"
        spec.prop = "usa_bill"
        spec.clothing = (
            "hospital_gown"
            if any(w in all_text for w in ("hospital", "er", "clinic", "bill"))
            else "casual_hoodie"
        )
        spec.race = "fair"
        spec.build = "broad"
    elif any(k in c_lower for k in ("uk", "united kingdom", "britain", "england", "scotland")):
        spec.expression = "npc"
        spec.prop = "uk_trolley_tea"
        spec.clothing = "hospital_gown" if "hospital" in all_text else "casual_hoodie"
        spec.race = "fair"
    elif "sweden" in c_lower:
        spec.expression = "doomer"
        spec.prop = "sweden_phone_queue"
        spec.clothing = "casual_hoodie"
        spec.race = "fair"
    elif "india" in c_lower:
        spec.expression = "chad"
        spec.prop = "india_stethoscope"
        spec.clothing = "doctor_coat"
        spec.race = "south_asian"
        spec.build = "buff"
    elif any(k in c_lower for k in ("korea", "south korea")):
        spec.expression = "bandaged"
        spec.prop = "korea_bandages"
        spec.hair = "cosmetic_bandages"
        spec.clothing = "hospital_gown"
        spec.race = "east_asian"
    elif "portugal" in c_lower:
        spec.expression = "smug"
        spec.prop = "portugal_tart_beret"
        spec.hair = "beret"
        spec.clothing = "casual_hoodie"
        spec.race = "olive"
    elif "italy" in c_lower:
        spec.expression = "happy"
        spec.prop = "italy_pasta_pot"
        spec.gender = (
            "nonna"
            if any(w in all_text for w in ("nonna", "grandma", "pasta", "cooking", "mama"))
            else "male"
        )
        spec.clothing = "apron"
        spec.hair = "grey_bun" if spec.gender == "nonna" else "bald"
        spec.race = "olive"
    elif any(k in c_lower for k in ("netherlands", "holland", "dutch")):
        spec.expression = "smug"
        spec.prop = "netherlands_paracetamol"
        spec.clothing = "doctor_coat" if "doctor" in all_text else "casual_hoodie"
        spec.race = "fair"
    elif "germany" in c_lower:
        spec.expression = "npc"
        spec.prop = "germany_fax_clipboard"
        spec.clothing = "business_suit"
        spec.race = "fair"
    elif "canada" in c_lower:
        spec.expression = "doomer"
        spec.prop = "canada_calendar"
        spec.clothing = "casual_hoodie"
        spec.race = "fair"
    elif "ireland" in c_lower:
        spec.expression = "happy"
        spec.prop = "ireland_bed_thumbsup"
        spec.hair = "ginger_curls_beard"
        spec.clothing = "hospital_gown"
        spec.race = "fair"
    elif "france" in c_lower:
        spec.expression = "smug"
        spec.prop = "france_coffee_cigarette"
        spec.hair = "beret"
        spec.clothing = "trench_coat"
        spec.race = "fair"
    elif "turkey" in c_lower:
        spec.expression = "bandaged"
        spec.prop = "turkey_hair_transplant"
        spec.hair = "headband"
        spec.clothing = "casual_hoodie"
        spec.race = "middle_eastern"
    elif any(k in c_lower for k in ("japan", "china")):
        spec.race = "east_asian"
        spec.clothing = "business_suit"
    elif any(k in c_lower for k in ("nigeria", "ghana", "kenya", "south africa")):
        spec.race = "black"
        spec.clothing = "doctor_coat" if "doctor" in all_text else "casual_hoodie"
    elif any(k in c_lower for k in ("mexico", "brazil", "colombia", "argentina", "spain")):
        spec.race = "latino"
        spec.clothing = "casual_hoodie"

    # 2. Scenario & Keyword overrides
    if any(w in sc_lower for w in ("bill", "cost", "debt", "bankrupt", "expensive")):
        spec.expression = "crying"
        spec.prop = "usa_bill"
    elif any(w in sc_lower for w in ("tea", "trolley", "wait_list", "18h", "24h")):
        spec.prop = "uk_trolley_tea"
        spec.expression = "npc"
    elif any(w in sc_lower for w in ("phone", "hold", "queue", "calling")):
        spec.prop = "sweden_phone_queue"
        spec.expression = "doomer"
    elif any(w in sc_lower for w in ("surgery", "chad", "doctor", "cure", "cheap")):
        if spec.country.lower() not in ("usa", "korea", "south korea"):
            spec.expression = "chad"
            spec.clothing = "doctor_coat"
            spec.prop = "india_stethoscope"
    elif any(w in sc_lower for w in ("cosmetic", "plastic", "bandage", "rhinoplasty")):
        spec.expression = "bandaged"
        spec.hair = "cosmetic_bandages"
        spec.prop = "korea_bandages"
    elif any(w in sc_lower for w in ("pasta", "lasagna", "pot", "grandma")):
        spec.prop = "italy_pasta_pot"
        spec.clothing = "apron"
    elif any(w in sc_lower for w in ("paracetamol", "cycling", "bike")):
        spec.prop = "netherlands_paracetamol"
    elif any(w in sc_lower for w in ("calendar", "mri", "2029")):
        spec.prop = "canada_calendar"
    elif any(w in sc_lower for w in ("strike", "protest", "greve", "coffee", "cigarette")):
        spec.prop = "france_coffee_cigarette"
    elif any(w in sc_lower for w in ("hair", "transplant", "follicle", "graft")):
        spec.prop = "turkey_hair_transplant"
        spec.hair = "headband"
    elif any(w in sc_lower for w in ("thumbs_up", "recovery", "happy")):
        spec.prop = "ireland_bed_thumbsup"
        spec.expression = "happy"

    # 3. Setting influences
    if any(w in s_lower for w in ("icu", "er", "ward", "clinic", "hospital", "surgery")):
        if spec.clothing in ("hospital_gown", "casual_hoodie"):
            if spec.expression in ("chad", "smug") and "doctor" in all_text:
                spec.clothing = "doctor_coat"
            elif "nurse" in all_text:
                spec.clothing = "nurse_scrubs"
                spec.hair = "nurse_cap"
            else:
                spec.clothing = "hospital_gown"
    elif any(w in s_lower for w in ("office", "bureaucracy", "government", "bank")):
        spec.clothing = "business_suit"
    elif any(w in s_lower for w in ("kitchen", "restaurant", "bakery")):
        spec.clothing = "apron"

    # 4. Explicit parameter overrides (highest priority)
    if age is not None:
        spec.age = _norm(age, _AGE_SYNONYMS, "young_adult")
        if spec.age == "elderly" and spec.gender == "female":
            spec.hair = "grey_bun"
    if gender is not None:
        spec.gender = _norm(gender, _GENDER_SYNONYMS, "male")
        if spec.gender == "tradwife":
            spec.hair = "blonde_parted"
        elif spec.gender == "nonna":
            spec.hair = "grey_bun"
            spec.age = "elderly"
            spec.clothing = "apron"
    if race is not None:
        spec.race = _norm(race, _RACE_SYNONYMS, "fair")
    if height is not None:
        spec.height = _norm(height, _HEIGHT_SYNONYMS, "average")
    if build is not None:
        spec.build = _norm(build, _BUILD_SYNONYMS, "average")
    if expression is not None:
        spec.expression = _norm(expression, _EXPRESSION_SYNONYMS, "npc")
    if clothing is not None:
        spec.clothing = _norm(clothing, _CLOTHING_SYNONYMS, "hospital_gown")
    if prop is not None:
        spec.prop = _norm(prop, _PROP_SYNONYMS, "none")
    if hair is not None:
        clean_hair = re.sub(r"[^a-zA-Z0-9_]+", "_", hair.lower().strip())
        spec.hair = clean_hair if clean_hair in HAIR_STYLES else spec.hair

    # Dynamic seed for minor visual organic variations
    seed_str = (
        f"{spec.country}:{spec.setting}:{spec.scenario}:{spec.age}:{spec.gender}:"
        f"{spec.race}:{spec.height}:{spec.build}:{spec.expression}:{spec.clothing}:{spec.prop}"
    )
    spec.seed = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest()[:8], 16)

    return spec


# ---------------------------------------------------------------------------
# Vector Geometry & Drawing Primitives
# ---------------------------------------------------------------------------


def _draw_polyline(
    draw: ImageDraw.ImageDraw,
    pts: list[tuple[float, float]],
    color: tuple[int, int, int, int] = INK_BLACK,
    width: int = 4,
) -> None:
    """Draw smooth connected ink line."""
    if len(pts) >= 2:
        draw.line(pts, fill=color, width=width, joint="curve")


def _draw_polygon_smooth(
    draw: ImageDraw.ImageDraw,
    pts: list[tuple[float, float]],
    fill: tuple[int, int, int, int] | None = None,
    outline: tuple[int, int, int, int] | None = INK_BLACK,
    width: int = 4,
) -> None:
    """Fill polygon and stroke its perimeter with smooth line."""
    if len(pts) >= 3:
        if fill is not None:
            draw.polygon(pts, fill=fill)
        if outline is not None and width > 0:
            closed = pts + [pts[0]]
            draw.line(closed, fill=outline, width=width, joint="curve")


# ---------------------------------------------------------------------------
# Procedural Drawing Modules
# ---------------------------------------------------------------------------


def _draw_background_props(draw: ImageDraw.ImageDraw, spec: WojakSpec, y_shift: float = 0.0) -> None:
    """Draw background elements behind the character (e.g. hospital bed, placard, trolley)."""
    if spec.prop == "ireland_bed_thumbsup":
        # Hospital bed frame and comfortable pillow behind head
        pillow_pts = [
            (180, 260 + y_shift),
            (380, 220 + y_shift),
            (560, 240 + y_shift),
            (540, 480 + y_shift),
            (190, 470 + y_shift),
        ]
        _draw_polygon_smooth(draw, pillow_pts, fill=(245, 245, 250, 255), outline=INK_BLACK, width=4)
        _draw_polyline(
            draw,
            [(230, 310 + y_shift), (320, 340 + y_shift), (370, 330 + y_shift)],
            color=INK_THIN,
            width=2,
        )
        _draw_polyline(draw, [(430, 320 + y_shift), (490, 345 + y_shift)], color=INK_THIN, width=2)
        # Bed tubular steel headboard
        draw.line([(140, 160 + y_shift), (660, 160 + y_shift)], fill=(160, 170, 185, 255), width=12)
        draw.line([(140, 160 + y_shift), (660, 160 + y_shift)], fill=INK_BLACK, width=2)
        for post_x in (160, 300, 500, 640):
            draw.line([(post_x, 160 + y_shift), (post_x, 500 + y_shift)], fill=(180, 190, 205, 255), width=8)
            draw.line([(post_x - 4, 160 + y_shift), (post_x - 4, 500 + y_shift)], fill=INK_BLACK, width=2)
            draw.line([(post_x + 4, 160 + y_shift), (post_x + 4, 500 + y_shift)], fill=INK_BLACK, width=2)

    elif spec.prop == "uk_trolley_tea":
        # Stainless steel trolley rail running behind
        draw.line([(80, 520 + y_shift), (740, 520 + y_shift)], fill=(200, 210, 225, 255), width=16)
        draw.line([(80, 512 + y_shift), (740, 512 + y_shift)], fill=INK_BLACK, width=3)
        draw.line([(80, 528 + y_shift), (740, 528 + y_shift)], fill=INK_BLACK, width=3)
        for px in (120, 680):
            draw.line([(px, 520 + y_shift), (px, 1100)], fill=(185, 195, 210, 255), width=14)
            draw.line([(px - 7, 520 + y_shift), (px - 7, 1100)], fill=INK_BLACK, width=3)
            draw.line([(px + 7, 520 + y_shift), (px + 7, 1100)], fill=INK_BLACK, width=3)

    elif spec.prop == "france_coffee_cigarette":
        # Strike placard pole resting behind left shoulder
        draw.line([(240, 320 + y_shift), (280, 1100)], fill=(160, 110, 70, 255), width=14)
        draw.line([(233, 320 + y_shift), (273, 1100)], fill=INK_BLACK, width=3)
        draw.line([(247, 320 + y_shift), (287, 1100)], fill=INK_BLACK, width=3)
        # Placard board
        placard = [
            (100, 100 + y_shift),
            (330, 80 + y_shift),
            (340, 250 + y_shift),
            (110, 270 + y_shift),
        ]
        _draw_polygon_smooth(draw, placard, fill=(245, 240, 230, 255), outline=INK_BLACK, width=4)
        f_strike = _get_font(28, bold=True)
        f_sub = _get_font(16, bold=True)
        draw.text((120, 110 + y_shift), "EN GRÈVE!", fill=BLOOD_RED, font=f_strike)
        draw.text((120, 160 + y_shift), "MANIF REFORME", fill=INK_BLACK, font=f_sub)
        draw.text((120, 195 + y_shift), "(ON STRIKE)", fill=BLOOD_RED, font=f_sub)


def _draw_torso_and_clothing(
    draw: ImageDraw.ImageDraw,
    spec: WojakSpec,
    neck_left: float,
    neck_right: float,
    skin_tone: tuple[int, int, int, int],
    y_shift: float = 0.0,
) -> None:
    """Draw shoulders, chest, arms, and clothing with hand-drawn aesthetic."""
    build = spec.build
    is_buff = build == "buff" or spec.expression == "chad"
    is_broad = build == "broad"
    is_slim = build == "slim"

    if is_buff:
        shoulder_l = 85.0
        shoulder_r = 715.0
        waist_l = 210.0
        waist_r = 590.0
    elif is_broad:
        shoulder_l = 120.0
        shoulder_r = 680.0
        waist_l = 180.0
        waist_r = 620.0
    elif is_slim:
        shoulder_l = 195.0
        shoulder_r = 605.0
        waist_l = 230.0
        waist_r = 570.0
    else:  # average
        shoulder_l = 150.0
        shoulder_r = 650.0
        waist_l = 200.0
        waist_r = 600.0

    shoulder_y = (660.0 if not is_buff else 630.0) + y_shift
    bottom_y = 1200.0

    # Torso base silhouette points
    torso_pts = [
        (neck_left, 590.0 + y_shift),
        (neck_left - 30, 610.0 + y_shift),
        (shoulder_l, shoulder_y),
        (shoulder_l - 20, 840.0 + y_shift),
        (waist_l, bottom_y),
        (waist_r, bottom_y),
        (shoulder_r + 20, 840.0 + y_shift),
        (shoulder_r, shoulder_y),
        (neck_right + 30, 610.0 + y_shift),
        (neck_right, 590.0 + y_shift),
    ]

    clothing = spec.clothing

    # Determine outfit colors
    if clothing == "hospital_gown":
        fill_color = (186, 230, 253, 255)  # Light cyan medical gown
    elif clothing == "doctor_coat":
        fill_color = (250, 250, 252, 255)  # White lab coat
    elif clothing == "nurse_scrubs":
        fill_color = (15, 118, 110, 255)  # Deep teal scrubs
    elif clothing == "business_suit":
        fill_color = (30, 41, 59, 255)  # Charcoal navy suit
    elif clothing == "apron":
        fill_color = (254, 242, 242, 255)  # White/cream culinary apron
    elif clothing == "trench_coat":
        fill_color = (180, 140, 100, 255)  # Detective khaki tan
    else:  # casual_hoodie
        fill_color = (71, 85, 105, 255)  # Slate grey casual hoodie

    if spec.clothing_color is not None:
        fill_color = spec.clothing_color

    # Draw base body/torso
    _draw_polygon_smooth(draw, torso_pts, fill=fill_color, outline=INK_BLACK, width=4)

    # Shading and detail lines per clothing type
    if clothing == "hospital_gown":
        # Hospital gown snowflake / cross pattern
        for py in range(int(700 + y_shift), 1150, 80):
            for px in range(int(waist_l) - 20, int(waist_r) + 20, 90):
                draw.line([(px - 6, py), (px + 6, py)], fill=(125, 211, 252, 200), width=2)
                draw.line([(px, py - 6), (px, py + 6)], fill=(125, 211, 252, 200), width=2)
        # Round hospital collar line
        collar = [(neck_left - 10, 610 + y_shift), (400, 680 + y_shift), (neck_right + 10, 610 + y_shift)]
        _draw_polyline(draw, collar, color=INK_BLACK, width=4)
        # Gown side tie strings
        _draw_polyline(
            draw,
            [(waist_l + 30, 780 + y_shift), (waist_l + 10, 820 + y_shift), (waist_l + 25, 860 + y_shift)],
            color=INK_BLACK,
            width=3,
        )

    elif clothing == "doctor_coat":
        # Collared shirt and tie underneath
        shirt_pts = [(neck_left + 10, 600 + y_shift), (400, 670 + y_shift), (neck_right - 10, 600 + y_shift)]
        _draw_polygon_smooth(draw, shirt_pts, fill=(225, 235, 245, 255), outline=INK_BLACK, width=3)
        # Necktie (crimson or deep blue)
        tie_pts = [
            (390, 670 + y_shift),
            (410, 670 + y_shift),
            (416, 810 + y_shift),
            (400, 850 + y_shift),
            (384, 810 + y_shift),
        ]
        _draw_polygon_smooth(draw, tie_pts, fill=(185, 28, 28, 255), outline=INK_BLACK, width=3)
        # Lab coat lapels (left and right)
        lapel_l = [(neck_left - 5, 605 + y_shift), (350, 750 + y_shift), (395, 870 + y_shift), (390, 1200)]
        lapel_r = [(neck_right + 5, 605 + y_shift), (450, 750 + y_shift), (405, 870 + y_shift), (410, 1200)]
        _draw_polyline(draw, lapel_l, color=INK_BLACK, width=4)
        _draw_polyline(draw, lapel_r, color=INK_BLACK, width=4)
        # Left breast pocket with pens
        pocket_pts = [
            (280, 760 + y_shift),
            (330, 760 + y_shift),
            (330, 820 + y_shift),
            (280, 820 + y_shift),
        ]
        _draw_polygon_smooth(draw, pocket_pts, fill=(245, 245, 250, 255), outline=INK_BLACK, width=3)
        draw.line([(292, 742 + y_shift), (292, 765 + y_shift)], fill=(37, 99, 235, 255), width=4)
        draw.line([(310, 740 + y_shift), (310, 765 + y_shift)], fill=(220, 38, 38, 255), width=4)

    elif clothing == "nurse_scrubs":
        v_neck = [(neck_left - 5, 600 + y_shift), (400, 720 + y_shift), (neck_right + 5, 600 + y_shift)]
        _draw_polyline(draw, v_neck, color=INK_BLACK, width=4)
        inner_v = [(neck_left + 5, 600 + y_shift), (400, 705 + y_shift), (neck_right - 5, 600 + y_shift)]
        _draw_polygon_smooth(draw, inner_v, fill=skin_tone, outline=None)
        pocket_pts = [
            (270, 790 + y_shift),
            (330, 790 + y_shift),
            (330, 860 + y_shift),
            (270, 860 + y_shift),
        ]
        _draw_polygon_smooth(draw, pocket_pts, fill=(13, 148, 136, 255), outline=INK_BLACK, width=3)

    elif clothing == "business_suit":
        shirt_pts = [(neck_left + 10, 600 + y_shift), (400, 660 + y_shift), (neck_right - 10, 600 + y_shift)]
        _draw_polygon_smooth(draw, shirt_pts, fill=WHITE, outline=INK_BLACK, width=3)
        tie_pts = [
            (388, 660 + y_shift),
            (412, 660 + y_shift),
            (418, 860 + y_shift),
            (400, 910 + y_shift),
            (382, 860 + y_shift),
        ]
        _draw_polygon_smooth(draw, tie_pts, fill=(220, 38, 38, 255), outline=INK_BLACK, width=3)
        lapel_l = [(neck_left - 5, 605 + y_shift), (340, 740 + y_shift), (390, 880 + y_shift), (390, 1200)]
        lapel_r = [(neck_right + 5, 605 + y_shift), (460, 740 + y_shift), (410, 880 + y_shift), (410, 1200)]
        _draw_polyline(draw, lapel_l, color=INK_BLACK, width=4)
        _draw_polyline(draw, lapel_r, color=INK_BLACK, width=4)
        draw.ellipse([(394, 930 + y_shift), (406, 942 + y_shift)], fill=INK_BLACK)
        draw.ellipse([(394, 1010 + y_shift), (406, 1022 + y_shift)], fill=INK_BLACK)

    elif clothing == "apron":
        bib_pts = [(320, 660 + y_shift), (480, 660 + y_shift), (510, 1200), (290, 1200)]
        _draw_polygon_smooth(draw, bib_pts, fill=(255, 255, 255, 255), outline=INK_BLACK, width=4)
        draw.line([(320, 660 + y_shift), (neck_left, 595 + y_shift)], fill=INK_BLACK, width=4)
        draw.line([(480, 660 + y_shift), (neck_right, 595 + y_shift)], fill=INK_BLACK, width=4)
        pocket_pts = [
            (340, 820 + y_shift),
            (460, 820 + y_shift),
            (460, 940 + y_shift),
            (340, 940 + y_shift),
        ]
        _draw_polygon_smooth(draw, pocket_pts, fill=(245, 245, 245, 255), outline=INK_BLACK, width=3)
        for py in (850 + y_shift, 880 + y_shift, 910 + y_shift):
            draw.line([(345, py), (455, py)], fill=(239, 68, 68, 120), width=2)

    elif clothing == "casual_hoodie":
        hoodie_rim = [
            (neck_left - 15, 600 + y_shift),
            (360, 660 + y_shift),
            (440, 660 + y_shift),
            (neck_right + 15, 600 + y_shift),
        ]
        _draw_polyline(draw, hoodie_rim, color=INK_BLACK, width=4)
        _draw_polyline(
            draw,
            [(380, 660 + y_shift), (376, 730 + y_shift), (382, 780 + y_shift)],
            color=WHITE,
            width=4,
        )
        _draw_polyline(
            draw,
            [(380, 660 + y_shift), (376, 730 + y_shift), (382, 780 + y_shift)],
            color=INK_BLACK,
            width=1,
        )
        _draw_polyline(
            draw,
            [(420, 660 + y_shift), (424, 725 + y_shift), (418, 775 + y_shift)],
            color=WHITE,
            width=4,
        )
        _draw_polyline(
            draw,
            [(420, 660 + y_shift), (424, 725 + y_shift), (418, 775 + y_shift)],
            color=INK_BLACK,
            width=1,
        )
        pocket = [
            (waist_l + 40, 940 + y_shift),
            (waist_l + 80, 880 + y_shift),
            (waist_r - 80, 880 + y_shift),
            (waist_r - 40, 940 + y_shift),
        ]
        _draw_polyline(draw, pocket, color=INK_BLACK, width=3)

    elif clothing == "trench_coat":
        collar_l = [(neck_left - 20, 590 + y_shift), (310, 690 + y_shift), (385, 780 + y_shift), (385, 1200)]
        collar_r = [(neck_right + 20, 590 + y_shift), (490, 690 + y_shift), (415, 780 + y_shift), (415, 1200)]
        _draw_polyline(draw, collar_l, color=INK_BLACK, width=5)
        _draw_polyline(draw, collar_r, color=INK_BLACK, width=5)


def _draw_neck_and_muscles(
    draw: ImageDraw.ImageDraw,
    spec: WojakSpec,
    neck_left: float,
    neck_right: float,
    chin_y: float,
    skin_tone: tuple[int, int, int, int],
    y_shift: float = 0.0,
) -> None:
    """Draw neck with muscular lines (Chad) or sagging folds (elderly/Wojak)."""
    shadow_tone = _calc_shadow(skin_tone, factor=0.76)

    neck_pts = [
        (neck_left, chin_y - 20),
        (neck_left, 600 + y_shift),
        (neck_right, 600 + y_shift),
        (neck_right, chin_y - 20),
    ]
    _draw_polygon_smooth(draw, neck_pts, fill=skin_tone, outline=None)

    shadow_pts = [
        (neck_left + 5, chin_y - 15),
        (400, chin_y + 35),
        (neck_right - 5, chin_y - 15),
    ]
    _draw_polygon_smooth(draw, shadow_pts, fill=shadow_tone, outline=None)

    draw.line([(neck_left, chin_y - 20), (neck_left, 600 + y_shift)], fill=INK_BLACK, width=4)
    draw.line([(neck_right, chin_y - 20), (neck_right, 600 + y_shift)], fill=INK_BLACK, width=4)

    is_chad = spec.expression == "chad" or spec.build == "buff"
    is_elderly = spec.age == "elderly" or spec.gender == "nonna"

    if is_chad:
        scm_l = [(neck_left + 25, chin_y + 10), (385, 590 + y_shift)]
        scm_r = [(neck_right - 25, chin_y + 10), (415, 590 + y_shift)]
        _draw_polyline(draw, scm_l, color=INK_BLACK, width=3)
        _draw_polyline(draw, scm_r, color=INK_BLACK, width=3)
        adams = [(390, chin_y + 40), (400, chin_y + 52), (410, chin_y + 40)]
        _draw_polyline(draw, adams, color=INK_BLACK, width=3)
    elif is_elderly:
        _draw_polyline(
            draw,
            [(neck_left + 15, chin_y + 25), (400, chin_y + 35), (neck_right - 15, chin_y + 25)],
            color=INK_THIN,
            width=2,
        )
        _draw_polyline(
            draw,
            [(neck_left + 25, chin_y + 50), (400, chin_y + 60), (neck_right - 25, chin_y + 50)],
            color=INK_THIN,
            width=2,
        )
    else:
        _draw_polyline(
            draw,
            [(neck_left + 20, chin_y + 30), (400, chin_y + 38), (neck_right - 20, chin_y + 30)],
            color=INK_THIN,
            width=2,
        )


def _draw_head_silhouette(
    draw: ImageDraw.ImageDraw,
    spec: WojakSpec,
    skin_tone: tuple[int, int, int, int],
    y_shift: float = 0.0,
) -> tuple[float, float, float]:
    """Draw authentic Wojak cranium, 3/4 hooked profile, jaw, and return neck anchor points."""
    is_chad = spec.expression == "chad" or spec.build == "buff"
    is_female = spec.gender in ("female", "tradwife")
    is_nonna = spec.gender == "nonna" or spec.age == "elderly"

    if is_chad:
        chin_y = 545.0 + y_shift
        jaw_pts = [
            (260.0, 420.0 + y_shift),
            (315.0, 515.0 + y_shift),
            (380.0, 550.0 + y_shift),
            (415.0, 555.0 + y_shift),
            (445.0, 550.0 + y_shift),
            (475.0, 530.0 + y_shift),
            (485.0, 495.0 + y_shift),
            (492.0, 480.0 + y_shift),
            (482.0, 465.0 + y_shift),
            (494.0, 450.0 + y_shift),
            (486.0, 435.0 + y_shift),
            (528.0, 385.0 + y_shift),
            (488.0, 325.0 + y_shift),
            (502.0, 275.0 + y_shift),
            (460.0, 205.0 + y_shift),
            (365.0, 185.0 + y_shift),
            (275.0, 245.0 + y_shift),
            (240.0, 360.0 + y_shift),
            (260.0, 420.0 + y_shift),
        ]
        neck_left, neck_right = 310.0, 490.0

    elif is_female:
        chin_y = 520.0 + y_shift
        jaw_pts = [
            (270.0, 420.0 + y_shift),
            (330.0, 495.0 + y_shift),
            (385.0, 525.0 + y_shift),
            (430.0, 520.0 + y_shift),
            (465.0, 490.0 + y_shift),
            (475.0, 475.0 + y_shift),
            (468.0, 465.0 + y_shift),
            (478.0, 452.0 + y_shift),
            (470.0, 438.0 + y_shift),
            (512.0, 390.0 + y_shift),
            (475.0, 335.0 + y_shift),
            (485.0, 285.0 + y_shift),
            (450.0, 215.0 + y_shift),
            (365.0, 195.0 + y_shift),
            (285.0, 255.0 + y_shift),
            (250.0, 365.0 + y_shift),
            (270.0, 420.0 + y_shift),
        ]
        neck_left, neck_right = 345.0, 455.0

    elif is_nonna:
        chin_y = 540.0 + y_shift
        jaw_pts = [
            (265.0, 420.0 + y_shift),
            (320.0, 490.0 + y_shift),
            (380.0, 535.0 + y_shift),
            (445.0, 540.0 + y_shift),
            (475.0, 505.0 + y_shift),
            (480.0, 485.0 + y_shift),
            (470.0, 470.0 + y_shift),
            (480.0, 455.0 + y_shift),
            (472.0, 438.0 + y_shift),
            (525.0, 395.0 + y_shift),
            (480.0, 330.0 + y_shift),
            (490.0, 280.0 + y_shift),
            (455.0, 205.0 + y_shift),
            (365.0, 185.0 + y_shift),
            (275.0, 245.0 + y_shift),
            (245.0, 360.0 + y_shift),
            (265.0, 420.0 + y_shift),
        ]
        neck_left, neck_right = 330.0, 470.0

    else:
        chin_y = 532.0 + y_shift
        jaw_pts = [
            (260.0, 420.0 + y_shift),
            (320.0, 500.0 + y_shift),
            (380.0, 530.0 + y_shift),
            (445.0, 532.0 + y_shift),
            (468.0, 500.0 + y_shift),
            (478.0, 485.0 + y_shift),
            (468.0, 470.0 + y_shift),
            (478.0, 455.0 + y_shift),
            (470.0, 440.0 + y_shift),
            (528.0, 390.0 + y_shift),
            (480.0, 330.0 + y_shift),
            (492.0, 280.0 + y_shift),
            (455.0, 205.0 + y_shift),
            (365.0, 185.0 + y_shift),
            (275.0, 245.0 + y_shift),
            (240.0, 360.0 + y_shift),
            (260.0, 420.0 + y_shift),
        ]
        neck_left, neck_right = 335.0, 465.0

    _draw_polygon_smooth(draw, jaw_pts, fill=skin_tone, outline=INK_BLACK, width=4)

    # Classic Wojak Ear at (260, 400 + y_shift)
    ear_pts = [
        (260, 375 + y_shift),
        (242, 385 + y_shift),
        (238, 415 + y_shift),
        (252, 440 + y_shift),
        (268, 435 + y_shift),
    ]
    _draw_polygon_smooth(draw, ear_pts, fill=skin_tone, outline=INK_BLACK, width=4)
    _draw_polyline(
        draw,
        [(248, 392 + y_shift), (254, 405 + y_shift), (248, 422 + y_shift)],
        color=INK_BLACK,
        width=3,
    )
    _draw_polyline(draw, [(254, 405 + y_shift), (260, 408 + y_shift)], color=INK_BLACK, width=2)

    if is_nonna:
        draw.ellipse([(248, 435 + y_shift), (262, 455 + y_shift)], outline=(234, 179, 8, 255), width=3)

    if is_chad:
        draw.line([(280, 410 + y_shift), (370, 455 + y_shift)], fill=INK_THIN, width=3)
        draw.line([(320, 515 + y_shift), (380, 545 + y_shift)], fill=INK_THIN, width=2)

    return neck_left, neck_right, chin_y


def _draw_facial_features(
    draw: ImageDraw.ImageDraw,
    spec: WojakSpec,
    skin_tone: tuple[int, int, int, int],
    y_shift: float = 0.0,
) -> None:
    """Draw eyebrows, eyes, pupils, nose, mouth, wrinkles, stubble, and expressions."""
    age = spec.age
    expr = spec.expression
    gender = spec.gender
    is_chad = expr == "chad" or spec.build == "buff"
    is_npc = expr == "npc"
    is_elderly = age == "elderly" or gender == "nonna"
    is_mid = age == "middle_aged"
    is_youth = age == "youth"

    # 1. Forehead wrinkles (canonical Wojak 3 curved lines)
    if not is_youth and not is_npc:
        num_lines = 4 if is_elderly else (2 if is_mid else 1)
        wrinkle_y = 230 + y_shift
        for i in range(num_lines):
            wy = wrinkle_y + i * 18
            pts = [(305 + i * 4, wy), (365, wy - 6), (425 - i * 2, wy + 2)]
            _draw_polyline(draw, pts, color=INK_THIN, width=2)

    # 2. Brow furrow lines (vertical worry creases between eyebrows)
    if not is_npc and not is_youth:
        if expr in ("crying", "doomer", "shocked") or is_elderly:
            draw.line([(388, 310 + y_shift), (386, 335 + y_shift)], fill=INK_THIN, width=2)
            draw.line([(396, 308 + y_shift), (398, 332 + y_shift)], fill=INK_THIN, width=2)

    # 3. Eyebrows
    if expr == "crying":
        brow_l = [(315, 330 + y_shift), (350, 312 + y_shift), (378, 308 + y_shift)]
        brow_r = [(405, 308 + y_shift), (435, 314 + y_shift), (465, 332 + y_shift)]
        _draw_polyline(draw, brow_l, color=INK_BLACK, width=4)
        _draw_polyline(draw, brow_r, color=INK_BLACK, width=4)
    elif expr == "chad":
        brow_l = [(310, 305 + y_shift), (350, 315 + y_shift), (380, 322 + y_shift)]
        brow_r = [(405, 322 + y_shift), (435, 315 + y_shift), (470, 305 + y_shift)]
        _draw_polyline(draw, brow_l, color=INK_BLACK, width=5)
        _draw_polyline(draw, brow_r, color=INK_BLACK, width=5)
    elif expr == "smug":
        brow_l = [(315, 320 + y_shift), (350, 318 + y_shift), (378, 322 + y_shift)]
        brow_r = [(405, 300 + y_shift), (435, 290 + y_shift), (470, 298 + y_shift)]
        _draw_polyline(draw, brow_l, color=INK_BLACK, width=4)
        _draw_polyline(draw, brow_r, color=INK_BLACK, width=4)
    elif expr == "shocked":
        brow_l = [(310, 295 + y_shift), (345, 280 + y_shift), (375, 288 + y_shift)]
        brow_r = [(405, 288 + y_shift), (435, 280 + y_shift), (470, 295 + y_shift)]
        _draw_polyline(draw, brow_l, color=INK_BLACK, width=4)
        _draw_polyline(draw, brow_r, color=INK_BLACK, width=4)
    elif is_npc:
        draw.line([(320, 318 + y_shift), (375, 318 + y_shift)], fill=INK_BLACK, width=3)
        draw.line([(410, 318 + y_shift), (465, 318 + y_shift)], fill=INK_BLACK, width=3)
    else:  # doomer / happy / bandaged
        brow_l = [(318, 320 + y_shift), (350, 316 + y_shift), (378, 320 + y_shift)]
        brow_r = [(408, 320 + y_shift), (438, 316 + y_shift), (468, 320 + y_shift)]
        _draw_polyline(draw, brow_l, color=INK_BLACK, width=4)
        _draw_polyline(draw, brow_r, color=INK_BLACK, width=4)

    # 4. Eyes & Pupils
    if is_npc:
        draw.ellipse([(328, 332 + y_shift), (362, 356 + y_shift)], fill=WHITE, outline=INK_BLACK, width=3)
        draw.ellipse([(342, 341 + y_shift), (348, 347 + y_shift)], fill=INK_BLACK)
        draw.ellipse([(420, 332 + y_shift), (454, 356 + y_shift)], fill=WHITE, outline=INK_BLACK, width=3)
        draw.ellipse([(434, 341 + y_shift), (440, 347 + y_shift)], fill=INK_BLACK)

    elif expr == "crying":
        eye_l = [(322, 346 + y_shift), (345, 334 + y_shift), (368, 346 + y_shift)]
        eye_r = [(412, 346 + y_shift), (438, 334 + y_shift), (464, 346 + y_shift)]
        _draw_polyline(draw, eye_l, color=INK_BLACK, width=4)
        _draw_polyline(draw, eye_r, color=INK_BLACK, width=4)
        draw.arc([(322, 338 + y_shift), (368, 360 + y_shift)], 0, 180, fill=(239, 68, 68, 200), width=3)
        draw.arc([(412, 338 + y_shift), (464, 360 + y_shift)], 0, 180, fill=(239, 68, 68, 200), width=3)
        draw.ellipse([(341, 340 + y_shift), (351, 350 + y_shift)], fill=INK_BLACK)
        draw.ellipse([(433, 340 + y_shift), (443, 350 + y_shift)], fill=INK_BLACK)

    elif expr == "shocked":
        draw.ellipse([(322, 326 + y_shift), (368, 366 + y_shift)], fill=WHITE, outline=INK_BLACK, width=4)
        draw.ellipse([(343, 344 + y_shift), (347, 348 + y_shift)], fill=INK_BLACK)
        draw.ellipse([(412, 326 + y_shift), (458, 366 + y_shift)], fill=WHITE, outline=INK_BLACK, width=4)
        draw.ellipse([(433, 344 + y_shift), (437, 348 + y_shift)], fill=INK_BLACK)
        _draw_polyline(draw, [(305, 300 + y_shift), (295, 305 + y_shift)], color=TEAR_BLUE, width=3)
        _draw_polyline(draw, [(475, 285 + y_shift), (485, 290 + y_shift)], color=TEAR_BLUE, width=3)

    elif expr == "chad":
        eye_l = [(322, 346 + y_shift), (346, 338 + y_shift), (370, 346 + y_shift)]
        eye_r = [(414, 346 + y_shift), (438, 338 + y_shift), (462, 346 + y_shift)]
        _draw_polyline(draw, eye_l, color=INK_BLACK, width=4)
        _draw_polyline(draw, eye_r, color=INK_BLACK, width=4)
        draw.line([(326, 348 + y_shift), (366, 348 + y_shift)], fill=INK_THIN, width=2)
        draw.line([(418, 348 + y_shift), (458, 348 + y_shift)], fill=INK_THIN, width=2)
        draw.ellipse([(342, 340 + y_shift), (350, 348 + y_shift)], fill=INK_BLACK)
        draw.ellipse([(434, 340 + y_shift), (442, 348 + y_shift)], fill=INK_BLACK)

    elif expr == "doomer":
        draw.ellipse([(324, 334 + y_shift), (366, 358 + y_shift)], fill=WHITE, outline=INK_BLACK, width=3)
        draw.ellipse([(416, 334 + y_shift), (458, 358 + y_shift)], fill=WHITE, outline=INK_BLACK, width=3)
        draw.line([(322, 344 + y_shift), (368, 344 + y_shift)], fill=INK_BLACK, width=4)
        draw.line([(414, 344 + y_shift), (460, 344 + y_shift)], fill=INK_BLACK, width=4)
        draw.ellipse([(342, 343 + y_shift), (350, 351 + y_shift)], fill=INK_BLACK)
        draw.ellipse([(434, 343 + y_shift), (442, 351 + y_shift)], fill=INK_BLACK)
        draw.arc([(322, 346 + y_shift), (368, 368 + y_shift)], 0, 180, fill=INK_THIN, width=3)
        draw.arc([(414, 346 + y_shift), (460, 368 + y_shift)], 0, 180, fill=INK_THIN, width=3)

    elif expr == "happy":
        eye_l = [(322, 348 + y_shift), (345, 336 + y_shift), (368, 348 + y_shift)]
        eye_r = [(414, 348 + y_shift), (438, 336 + y_shift), (462, 348 + y_shift)]
        _draw_polyline(draw, eye_l, color=INK_BLACK, width=4)
        _draw_polyline(draw, eye_r, color=INK_BLACK, width=4)
        draw.line([(314, 344 + y_shift), (322, 348 + y_shift)], fill=INK_THIN, width=2)
        draw.line([(464, 348 + y_shift), (472, 344 + y_shift)], fill=INK_THIN, width=2)
        draw.ellipse([(305, 375 + y_shift), (345, 405 + y_shift)], fill=BLUSH_PINK)
        draw.ellipse([(435, 375 + y_shift), (475, 405 + y_shift)], fill=BLUSH_PINK)

    else:
        draw.ellipse([(324, 334 + y_shift), (366, 358 + y_shift)], fill=WHITE, outline=INK_BLACK, width=3)
        draw.ellipse([(416, 334 + y_shift), (458, 358 + y_shift)], fill=WHITE, outline=INK_BLACK, width=3)
        draw.ellipse([(342, 342 + y_shift), (350, 350 + y_shift)], fill=INK_BLACK)
        draw.ellipse([(434, 342 + y_shift), (442, 350 + y_shift)], fill=INK_BLACK)
        draw.arc([(324, 346 + y_shift), (366, 366 + y_shift)], 0, 180, fill=INK_THIN, width=2)
        draw.arc([(416, 346 + y_shift), (458, 366 + y_shift)], 0, 180, fill=INK_THIN, width=2)

    # 5. Eyelashes for female / tradwife
    if gender in ("female", "tradwife"):
        draw.line([(356, 334 + y_shift), (364, 326 + y_shift)], fill=INK_BLACK, width=2)
        draw.line([(366, 338 + y_shift), (376, 332 + y_shift)], fill=INK_BLACK, width=2)
        draw.line([(448, 334 + y_shift), (456, 326 + y_shift)], fill=INK_BLACK, width=2)
        draw.line([(458, 338 + y_shift), (468, 332 + y_shift)], fill=INK_BLACK, width=2)

    # 6. Eye bags / crow's feet for elderly
    if is_elderly:
        for offset_x, base_x in ((0, 320), (90, 465)):
            draw.line([(base_x, 346 + y_shift), (base_x - 10, 340 + y_shift)], fill=INK_THIN, width=2)
            draw.line([(base_x, 350 + y_shift), (base_x - 12, 350 + y_shift)], fill=INK_THIN, width=2)
            draw.line([(base_x, 354 + y_shift), (base_x - 10, 360 + y_shift)], fill=INK_THIN, width=2)

    # 7. Nose nostril fold (inner nose definition)
    if not is_npc:
        nostril = [(478, 412 + y_shift), (490, 408 + y_shift), (496, 414 + y_shift)]
        _draw_polyline(draw, nostril, color=INK_BLACK, width=3)

    # 8. Nasolabial folds (smile/frown lines beside nose to mouth)
    if not is_npc and not is_youth:
        line_w = 3 if is_elderly else 2
        fold_l = [(372, 395 + y_shift), (385, 440 + y_shift), (395, 465 + y_shift)]
        fold_r = [(468, 395 + y_shift), (462, 440 + y_shift), (455, 465 + y_shift)]
        _draw_polyline(draw, fold_l, color=INK_THIN, width=line_w)
        _draw_polyline(draw, fold_r, color=INK_THIN, width=line_w)

    # 9. Mouth
    if is_npc:
        draw.line([(390, 470 + y_shift), (452, 470 + y_shift)], fill=INK_BLACK, width=3)

    elif expr == "shocked":
        mouth_pts = [
            (395, 452 + y_shift),
            (455, 452 + y_shift),
            (450, 510 + y_shift),
            (400, 510 + y_shift),
        ]
        _draw_polygon_smooth(draw, mouth_pts, fill=(120, 20, 20, 255), outline=INK_BLACK, width=4)
        draw.rectangle([(404, 453 + y_shift), (446, 465 + y_shift)], fill=WHITE, outline=INK_BLACK, width=2)
        draw.ellipse([(406, 488 + y_shift), (444, 512 + y_shift)], fill=(244, 114, 182, 255))

    elif expr == "crying":
        mouth_pts = [
            (390, 462 + y_shift),
            (454, 462 + y_shift),
            (446, 492 + y_shift),
            (398, 492 + y_shift),
        ]
        _draw_polygon_smooth(draw, mouth_pts, fill=(60, 20, 20, 255), outline=INK_BLACK, width=4)
        _draw_polyline(
            draw,
            [(394, 498 + y_shift), (420, 504 + y_shift), (448, 498 + y_shift)],
            color=INK_BLACK,
            width=3,
        )

    elif expr == "chad":
        mouth = [(388, 476 + y_shift), (425, 472 + y_shift), (458, 460 + y_shift)]
        _draw_polyline(draw, mouth, color=INK_BLACK, width=4)
        draw.line([(456, 455 + y_shift), (462, 465 + y_shift)], fill=INK_BLACK, width=3)

    elif expr == "smug":
        mouth = [(385, 478 + y_shift), (420, 474 + y_shift), (462, 454 + y_shift)]
        _draw_polyline(draw, mouth, color=INK_BLACK, width=4)
        draw.line([(460, 450 + y_shift), (466, 460 + y_shift)], fill=INK_BLACK, width=3)

    elif expr == "happy":
        mouth = [(386, 465 + y_shift), (422, 486 + y_shift), (458, 465 + y_shift)]
        _draw_polyline(draw, mouth, color=INK_BLACK, width=4)
        draw.line([(382, 460 + y_shift), (388, 468 + y_shift)], fill=INK_BLACK, width=3)
        draw.line([(456, 468 + y_shift), (462, 460 + y_shift)], fill=INK_BLACK, width=3)

    elif expr == "doomer":
        mouth = [(385, 464 + y_shift), (420, 472 + y_shift), (455, 476 + y_shift)]
        _draw_polyline(draw, mouth, color=INK_BLACK, width=4)
        draw.rectangle([(445, 468 + y_shift), (490, 476 + y_shift)], fill=WHITE, outline=INK_BLACK, width=2)
        draw.rectangle([(485, 468 + y_shift), (490, 476 + y_shift)], fill=(234, 88, 12, 255))
        smoke = [
            (495, 470 + y_shift),
            (510, 450 + y_shift),
            (500, 420 + y_shift),
            (520, 390 + y_shift),
        ]
        _draw_polyline(draw, smoke, color=(200, 205, 215, 180), width=3)

    else:
        mouth = [(388, 468 + y_shift), (422, 474 + y_shift), (456, 470 + y_shift)]
        _draw_polyline(draw, mouth, color=INK_BLACK, width=4)

    # 10. Chin indentation line
    if not is_npc:
        draw.line([(415, 498 + y_shift), (435, 498 + y_shift)], fill=INK_THIN, width=2)

    # 11. Stubble / 5 o'clock shadow
    if (expr == "doomer" or is_mid) and gender == "male":
        for sy in range(int(480 + y_shift), int(525 + y_shift), 8):
            for sx in range(350, 450, 10):
                if random.Random(sx * 100 + sy).random() > 0.4:
                    draw.point((sx, sy), fill=(50, 50, 55, 160))

    # 12. Nonna wire-frame spectacles
    if gender == "nonna":
        draw.ellipse([(322, 330 + y_shift), (368, 368 + y_shift)], outline=(200, 160, 60, 255), width=3)
        draw.ellipse([(412, 330 + y_shift), (458, 368 + y_shift)], outline=(200, 160, 60, 255), width=3)
        draw.line([(368, 346 + y_shift), (412, 346 + y_shift)], fill=(200, 160, 60, 255), width=3)
        draw.line([(322, 346 + y_shift), (265, 385 + y_shift)], fill=(200, 160, 60, 255), width=2)


def _draw_tears_layer(draw: ImageDraw.ImageDraw, spec: WojakSpec, y_shift: float = 0.0) -> None:
    """Draw massive streaming bright cyan tears cascading down cheeks and chin."""
    if spec.expression != "crying":
        return

    stream_l = [
        (338, 355 + y_shift),
        (332, 395 + y_shift),
        (342, 435 + y_shift),
        (336, 480 + y_shift),
        (345, 520 + y_shift),
        (338, 555 + y_shift),
        (330, 580 + y_shift),
    ]
    stream_r = [
        (444, 355 + y_shift),
        (452, 400 + y_shift),
        (446, 445 + y_shift),
        (456, 490 + y_shift),
        (448, 535 + y_shift),
        (455, 565 + y_shift),
    ]

    for stream in (stream_l, stream_r):
        _draw_polyline(draw, stream, color=TEAR_OUTLINE, width=12)
        _draw_polyline(draw, stream, color=TEAR_BLUE, width=8)
        _draw_polyline(draw, stream, color=TEAR_LIGHT, width=3)

    drops = [
        ((335, 600 + y_shift), (345, 616 + y_shift)),
        ((328, 630 + y_shift), (336, 642 + y_shift)),
        ((452, 585 + y_shift), (460, 598 + y_shift)),
        ((458, 618 + y_shift), (464, 628 + y_shift)),
    ]
    for top, bot in drops:
        draw.ellipse([top, bot], fill=TEAR_BLUE, outline=TEAR_OUTLINE, width=2)
        draw.point((top[0] + 2, top[1] + 3), fill=WHITE)


def _draw_hair_and_headwear(
    draw: ImageDraw.ImageDraw,
    spec: WojakSpec,
    neck_left: float,
    neck_right: float,
    skin_tone: tuple[int, int, int, int],
    y_shift: float = 0.0,
) -> None:
    """Draw hair or headwear (bald dome, bun, beret, surgical cap, bandages, etc.)."""
    hair = spec.hair

    if hair == "bald":
        return

    color = HAIR_COLORS.get(hair, (40, 40, 40, 255))
    if spec.hair_color is not None:
        color = spec.hair_color

    if hair == "messy_dark_hair":
        locks = [
            (250, 360 + y_shift),
            (270, 250 + y_shift),
            (360, 180 + y_shift),
            (460, 200 + y_shift),
            (490, 260 + y_shift),
            (465, 300 + y_shift),
            (445, 275 + y_shift),
            (420, 310 + y_shift),
            (395, 280 + y_shift),
            (370, 315 + y_shift),
            (345, 285 + y_shift),
            (315, 320 + y_shift),
            (280, 305 + y_shift),
            (255, 360 + y_shift),
        ]
        _draw_polygon_smooth(draw, locks, fill=color, outline=INK_BLACK, width=4)
        _draw_polyline(draw, [(310, 240 + y_shift), (370, 290 + y_shift)], color=INK_THIN, width=2)
        _draw_polyline(draw, [(380, 210 + y_shift), (430, 280 + y_shift)], color=INK_THIN, width=2)

    elif hair == "blonde_parted":
        hair_pts = [
            (260, 380 + y_shift),
            (270, 240 + y_shift),
            (360, 180 + y_shift),
            (470, 200 + y_shift),
            (505, 270 + y_shift),
            (485, 320 + y_shift),
            (455, 300 + y_shift),
            (430, 330 + y_shift),
            (370, 260 + y_shift),
            (285, 340 + y_shift),
            (265, 410 + y_shift),
            (260, 470 + y_shift),
            (290, 460 + y_shift),
            (275, 400 + y_shift),
        ]
        _draw_polygon_smooth(draw, hair_pts, fill=color, outline=INK_BLACK, width=4)
        if spec.gender == "tradwife":
            band = [(290, 260 + y_shift), (370, 195 + y_shift), (460, 230 + y_shift)]
            _draw_polyline(draw, band, color=(59, 130, 246, 255), width=10)
            _draw_polyline(draw, band, color=INK_BLACK, width=2)

    elif hair == "ginger_curls_beard":
        curls = [
            (255, 360 + y_shift),
            (260, 240 + y_shift),
            (350, 175 + y_shift),
            (460, 195 + y_shift),
            (495, 260 + y_shift),
            (460, 295 + y_shift),
            (430, 280 + y_shift),
            (400, 305 + y_shift),
            (360, 275 + y_shift),
            (320, 305 + y_shift),
            (270, 325 + y_shift),
        ]
        _draw_polygon_smooth(draw, curls, fill=color, outline=INK_BLACK, width=4)
        beard_pts = [
            (265, 420 + y_shift),
            (275, 470 + y_shift),
            (320, 545 + y_shift),
            (380, 585 + y_shift),
            (450, 580 + y_shift),
            (485, 540 + y_shift),
            (490, 480 + y_shift),
            (460, 490 + y_shift),
            (440, 545 + y_shift),
            (380, 540 + y_shift),
            (325, 495 + y_shift),
            (280, 440 + y_shift),
        ]
        _draw_polygon_smooth(draw, beard_pts, fill=color, outline=INK_BLACK, width=4)
        _draw_polyline(
            draw,
            [(340, 520 + y_shift), (370, 560 + y_shift), (410, 545 + y_shift)],
            color=INK_THIN,
            width=2,
        )

    elif hair == "grey_bun":
        hair_pts = [
            (260, 390 + y_shift),
            (265, 250 + y_shift),
            (355, 190 + y_shift),
            (465, 210 + y_shift),
            (495, 275 + y_shift),
            (475, 320 + y_shift),
            (440, 285 + y_shift),
            (380, 245 + y_shift),
            (300, 310 + y_shift),
            (265, 410 + y_shift),
        ]
        _draw_polygon_smooth(draw, hair_pts, fill=color, outline=INK_BLACK, width=4)
        draw.ellipse(
            [(345, 130 + y_shift), (415, 195 + y_shift)],
            fill=color,
            outline=INK_BLACK,
            width=4,
        )
        draw.line([(335, 175 + y_shift), (425, 145 + y_shift)], fill=(160, 120, 60, 255), width=4)

    elif hair == "headscarf":
        scarf_outer = [
            (230, 440 + y_shift),
            (240, 250 + y_shift),
            (350, 165 + y_shift),
            (475, 190 + y_shift),
            (515, 280 + y_shift),
            (505, 440 + y_shift),
            (445, 540 + y_shift),
            (390, 545 + y_shift),
            (345, 515 + y_shift),
            (280, 490 + y_shift),
        ]
        _draw_polygon_smooth(draw, scarf_outer, fill=color, outline=INK_BLACK, width=4)
        knot = [
            (380, 535 + y_shift),
            (410, 535 + y_shift),
            (415, 565 + y_shift),
            (395, 575 + y_shift),
            (375, 560 + y_shift),
        ]
        _draw_polygon_smooth(draw, knot, fill=(153, 27, 27, 255), outline=INK_BLACK, width=3)

    elif hair == "beret":
        beret_pts = [
            (235, 300 + y_shift),
            (280, 190 + y_shift),
            (410, 170 + y_shift),
            (495, 205 + y_shift),
            (515, 270 + y_shift),
            (460, 260 + y_shift),
            (360, 230 + y_shift),
            (275, 270 + y_shift),
        ]
        _draw_polygon_smooth(draw, beret_pts, fill=color, outline=INK_BLACK, width=4)
        draw.line([(385, 170 + y_shift), (385, 155 + y_shift)], fill=INK_BLACK, width=4)

    elif hair == "doctor_surgical_cap":
        cap_pts = [
            (245, 360 + y_shift),
            (255, 220 + y_shift),
            (355, 165 + y_shift),
            (470, 185 + y_shift),
            (505, 260 + y_shift),
            (495, 305 + y_shift),
            (430, 270 + y_shift),
            (340, 265 + y_shift),
            (265, 330 + y_shift),
        ]
        _draw_polygon_smooth(draw, cap_pts, fill=color, outline=INK_BLACK, width=4)
        _draw_polyline(
            draw,
            [(265, 330 + y_shift), (360, 270 + y_shift), (495, 305 + y_shift)],
            color=INK_BLACK,
            width=3,
        )

    elif hair == "nurse_cap":
        cap_pts = [
            (330, 210 + y_shift),
            (430, 200 + y_shift),
            (460, 240 + y_shift),
            (320, 255 + y_shift),
        ]
        _draw_polygon_smooth(draw, cap_pts, fill=WHITE, outline=INK_BLACK, width=3)
        draw.line([(390, 222 + y_shift), (402, 222 + y_shift)], fill=BLOOD_RED, width=4)
        draw.line([(396, 216 + y_shift), (396, 228 + y_shift)], fill=BLOOD_RED, width=4)

    elif hair == "cosmetic_bandages":
        wrap_head = [
            (245, 350 + y_shift),
            (265, 210 + y_shift),
            (365, 175 + y_shift),
            (480, 200 + y_shift),
            (510, 270 + y_shift),
            (470, 310 + y_shift),
            (350, 255 + y_shift),
            (265, 330 + y_shift),
        ]
        _draw_polygon_smooth(draw, wrap_head, fill=(245, 245, 248, 255), outline=INK_BLACK, width=3)
        draw.line([(280, 270 + y_shift), (450, 220 + y_shift)], fill=INK_THIN, width=2)
        draw.line([(300, 295 + y_shift), (470, 250 + y_shift)], fill=INK_THIN, width=2)
        strap = [
            (265, 410 + y_shift),
            (320, 500 + y_shift),
            (380, 545 + y_shift),
            (445, 545 + y_shift),
            (475, 490 + y_shift),
            (460, 480 + y_shift),
            (435, 530 + y_shift),
            (380, 530 + y_shift),
            (325, 485 + y_shift),
            (275, 400 + y_shift),
        ]
        _draw_polygon_smooth(draw, strap, fill=(245, 245, 248, 255), outline=INK_BLACK, width=3)

    elif hair == "headband":
        band_pts = [
            (260, 330 + y_shift),
            (370, 260 + y_shift),
            (485, 290 + y_shift),
            (475, 325 + y_shift),
            (370, 295 + y_shift),
            (265, 360 + y_shift),
        ]
        _draw_polygon_smooth(draw, band_pts, fill=color, outline=INK_BLACK, width=4)
        for dot_x in range(300, 470, 10):
            for dot_y in range(int(215 + y_shift), int(265 + y_shift), 8):
                if random.Random(dot_x * 50 + dot_y).random() > 0.35:
                    draw.point((dot_x, dot_y), fill=BLOOD_RED)
                    draw.point((dot_x + 1, dot_y), fill=BLOOD_RED)


def _draw_props_and_hands(
    draw: ImageDraw.ImageDraw,
    spec: WojakSpec,
    skin_tone: tuple[int, int, int, int],
    y_shift: float = 0.0,
) -> None:
    """Draw context & nationality props with authentic comedic details."""
    prop = spec.prop
    if prop == "none":
        return

    # 1. USA: Giant printed paper bill scroll unfurling down to floor
    if prop in ("usa_bill", "generic_bill"):
        bill_pts = [
            (230, 720 + y_shift),
            (560, 690 + y_shift),
            (580, 1180),
            (530, 1200),
            (210, 1200),
            (190, 750 + y_shift),
        ]
        _draw_polygon_smooth(draw, bill_pts, fill=(255, 254, 245, 255), outline=INK_BLACK, width=4)
        _draw_polygon_smooth(
            draw,
            [(190, 720 + y_shift), (240, 700 + y_shift), (250, 740 + y_shift), (200, 760 + y_shift)],
            fill=skin_tone,
            outline=INK_BLACK,
            width=3,
        )
        _draw_polygon_smooth(
            draw,
            [(550, 690 + y_shift), (600, 710 + y_shift), (590, 750 + y_shift), (540, 730 + y_shift)],
            fill=skin_tone,
            outline=INK_BLACK,
            width=3,
        )

        f_head = _get_font(22, bold=True)
        f_body = _get_font(15, bold=True)
        f_tot = _get_font(26, bold=True)
        f_stamp = _get_font(20, bold=True)

        draw.text((250, 735 + y_shift), "HOSPITAL INVOICE", fill=INK_BLACK, font=f_head)
        draw.line([(240, 765 + y_shift), (540, 765 + y_shift)], fill=INK_BLACK, width=2)
        charges = [
            ("BED (1 NIGHT):", "$42,500.00"),
            ("TYLENOL (2 PILLS):", "$1,850.00"),
            ("BAND-AID (1x):", "$1,200.00"),
            ("OXYGEN (BREATH):", "$3,400.00"),
            ("DOCTOR SMILE:", "$5,000.00"),
        ]
        for idx, (item, price) in enumerate(charges):
            y = 780 + idx * 45 + y_shift
            draw.text((230, y), item, fill=INK_BLACK, font=f_body)
            draw.text((430, y), price, fill=INK_BLACK, font=f_body)
            draw.line([(230, y + 25), (540, y + 25)], fill=(220, 220, 225, 255), width=1)

        draw.line([(230, 1010 + y_shift), (540, 1010 + y_shift)], fill=INK_BLACK, width=3)
        draw.text((230, 1025 + y_shift), "TOTAL:", fill=BLOOD_RED, font=f_tot)
        draw.text((350, 1025 + y_shift), "$184,650.00", fill=BLOOD_RED, font=f_tot)

        stamp_box = [(240, 1075 + y_shift), (530, 1075 + y_shift), (530, 1145 + y_shift), (240, 1145 + y_shift)]
        _draw_polygon_smooth(draw, stamp_box, fill=(254, 226, 226, 200), outline=BLOOD_RED, width=4)
        draw.text((260, 1095 + y_shift), "PAST DUE - PAY NOW", fill=BLOOD_RED, font=f_stamp)

    # 2. UK: Hospital Trolley Rail & Steaming British Tea
    elif prop == "uk_trolley_tea":
        sign_pts = [
            (160, 560 + y_shift),
            (370, 560 + y_shift),
            (370, 670 + y_shift),
            (160, 670 + y_shift),
        ]
        _draw_polygon_smooth(draw, sign_pts, fill=(254, 240, 138, 255), outline=INK_BLACK, width=3)
        f_sign = _get_font(18, bold=True)
        f_wait = _get_font(26, bold=True)
        draw.text((180, 575 + y_shift), "NHS A&E WAIT", fill=INK_BLACK, font=f_sign)
        draw.text((195, 615 + y_shift), "18 HOURS", fill=BLOOD_RED, font=f_wait)

        mug_pts = [
            (450, 710 + y_shift),
            (540, 710 + y_shift),
            (530, 810 + y_shift),
            (460, 810 + y_shift),
        ]
        _draw_polygon_smooth(draw, mug_pts, fill=WHITE, outline=INK_BLACK, width=4)
        draw.arc([(525, 725 + y_shift), (565, 785 + y_shift)], -90, 90, fill=INK_BLACK, width=4)
        draw.line([(500, 710 + y_shift), (515, 750 + y_shift)], fill=(120, 53, 15, 255), width=2)
        draw.rectangle([(510, 750 + y_shift), (525, 768 + y_shift)], fill=BLOOD_RED, outline=INK_BLACK, width=2)
        draw.text((475, 745 + y_shift), "TEA", fill=INK_BLACK, font=_get_font(18, bold=True))
        for sx in (470, 500, 525):
            steam = [
                (sx, 695 + y_shift),
                (sx + 8, 665 + y_shift),
                (sx - 5, 635 + y_shift),
                (sx + 5, 605 + y_shift),
            ]
            _draw_polyline(draw, steam, color=(220, 225, 235, 180), width=3)
        _draw_polygon_smooth(
            draw,
            [(430, 730 + y_shift), (460, 730 + y_shift), (460, 780 + y_shift), (430, 780 + y_shift)],
            fill=skin_tone,
            outline=INK_BLACK,
            width=3,
        )

    # 3. Sweden: Smartphone to ear in phone queue
    elif prop in ("sweden_phone_queue", "generic_phone"):
        phone_pts = [
            (505, 330 + y_shift),
            (555, 320 + y_shift),
            (575, 450 + y_shift),
            (525, 460 + y_shift),
        ]
        _draw_polygon_smooth(draw, phone_pts, fill=(24, 24, 27, 255), outline=INK_BLACK, width=4)
        screen_pts = [
            (512, 340 + y_shift),
            (550, 332 + y_shift),
            (568, 440 + y_shift),
            (530, 448 + y_shift),
        ]
        _draw_polygon_smooth(draw, screen_pts, fill=(59, 130, 246, 255), outline=None)
        f_phone = _get_font(14, bold=True)
        draw.text((518, 350 + y_shift), "1177", fill=WHITE, font=f_phone)
        draw.text((518, 375 + y_shift), "QUEUE", fill=WHITE, font=f_phone)
        draw.text((518, 400 + y_shift), "#142", fill=(254, 240, 138, 255), font=f_phone)
        hand_pts = [
            (485, 390 + y_shift),
            (530, 380 + y_shift),
            (535, 435 + y_shift),
            (490, 440 + y_shift),
        ]
        _draw_polygon_smooth(draw, hand_pts, fill=skin_tone, outline=INK_BLACK, width=3)
        draw.text((575, 320 + y_shift), "♪", fill=(147, 51, 234, 255), font=_get_font(24, bold=True))
        draw.text((595, 360 + y_shift), "♫", fill=(59, 130, 246, 255), font=_get_font(24, bold=True))
        draw.text((580, 410 + y_shift), "♩", fill=(147, 51, 234, 255), font=_get_font(20, bold=True))

    # 4. India: Stethoscope around neck, crossed arms, $3 prescription pad
    elif prop == "india_stethoscope":
        tube_l = [(340, 580 + y_shift), (330, 680 + y_shift), (380, 770 + y_shift)]
        tube_r = [(460, 580 + y_shift), (470, 680 + y_shift), (415, 770 + y_shift)]
        _draw_polyline(draw, tube_l, color=INK_BLACK, width=6)
        _draw_polyline(draw, tube_r, color=INK_BLACK, width=6)
        draw.ellipse([(380, 760 + y_shift), (415, 795 + y_shift)], fill=(203, 213, 225, 255), outline=INK_BLACK, width=3)
        draw.ellipse([(388, 768 + y_shift), (407, 787 + y_shift)], fill=(148, 163, 184, 255))
        _draw_polyline(draw, [(180, 780 + y_shift), (450, 850 + y_shift)], color=INK_BLACK, width=4)
        _draw_polyline(draw, [(620, 780 + y_shift), (350, 850 + y_shift)], color=INK_BLACK, width=4)
        pad = [
            (460, 800 + y_shift),
            (560, 780 + y_shift),
            (575, 890 + y_shift),
            (475, 910 + y_shift),
        ]
        _draw_polygon_smooth(draw, pad, fill=WHITE, outline=INK_BLACK, width=3)
        f_pad = _get_font(14, bold=True)
        draw.text((475, 805 + y_shift), "Rx CLINIC", fill=INK_BLACK, font=f_pad)
        draw.text((475, 830 + y_shift), "SURGERY: $200", fill=BLOOD_RED, font=f_pad)
        draw.text((475, 855 + y_shift), "MEDS: $0.10", fill=INK_BLACK, font=f_pad)

    # 5. South Korea: Cosmetic surgery bandages, nose plaster, hand mirror
    elif prop == "korea_bandages":
        plaster = [
            (455, 365 + y_shift),
            (510, 355 + y_shift),
            (515, 385 + y_shift),
            (460, 395 + y_shift),
        ]
        _draw_polygon_smooth(draw, plaster, fill=(245, 245, 250, 255), outline=INK_BLACK, width=3)
        draw.ellipse([(330, 360 + y_shift), (365, 380 + y_shift)], fill=BRUISE_PURPLE)
        draw.ellipse([(332, 362 + y_shift), (362, 378 + y_shift)], fill=BRUISE_YELLOW)
        draw.ellipse([(420, 360 + y_shift), (455, 380 + y_shift)], fill=BRUISE_PURPLE)
        draw.ellipse([(422, 362 + y_shift), (452, 378 + y_shift)], fill=BRUISE_YELLOW)
        mirror_glass = [
            (490, 680 + y_shift),
            (580, 680 + y_shift),
            (580, 790 + y_shift),
            (490, 790 + y_shift),
        ]
        _draw_polygon_smooth(draw, mirror_glass, fill=(244, 114, 182, 255), outline=INK_BLACK, width=4)
        draw.ellipse([(505, 695 + y_shift), (565, 775 + y_shift)], fill=(224, 242, 254, 255), outline=INK_BLACK, width=2)
        draw.rectangle([(525, 790 + y_shift), (545, 870 + y_shift)], fill=(244, 114, 182, 255), outline=INK_BLACK, width=3)
        _draw_polygon_smooth(
            draw,
            [(510, 810 + y_shift), (560, 810 + y_shift), (560, 850 + y_shift), (510, 850 + y_shift)],
            fill=skin_tone,
            outline=INK_BLACK,
            width=3,
        )

    # 6. Portugal: Black beret & Pastel de Nata custard tart
    elif prop == "portugal_tart_beret":
        plate = [
            (430, 760 + y_shift),
            (570, 760 + y_shift),
            (550, 805 + y_shift),
            (450, 805 + y_shift),
        ]
        _draw_polygon_smooth(draw, plate, fill=WHITE, outline=INK_BLACK, width=3)
        tart_crust = [
            (465, 740 + y_shift),
            (535, 740 + y_shift),
            (525, 775 + y_shift),
            (475, 775 + y_shift),
        ]
        _draw_polygon_smooth(draw, tart_crust, fill=(217, 119, 6, 255), outline=INK_BLACK, width=3)
        tart_top = [
            (470, 735 + y_shift),
            (530, 735 + y_shift),
            (525, 755 + y_shift),
            (475, 755 + y_shift),
        ]
        _draw_polygon_smooth(draw, tart_top, fill=(251, 191, 36, 255), outline=None)
        draw.ellipse([(485, 740 + y_shift), (495, 748 + y_shift)], fill=(120, 53, 15, 255))
        draw.ellipse([(508, 742 + y_shift), (520, 750 + y_shift)], fill=(120, 53, 15, 255))
        _draw_polyline(
            draw,
            [(495, 725 + y_shift), (490, 695 + y_shift), (505, 670 + y_shift)],
            color=(220, 225, 235, 180),
            width=2,
        )
        _draw_polygon_smooth(
            draw,
            [(410, 765 + y_shift), (450, 765 + y_shift), (450, 800 + y_shift), (410, 800 + y_shift)],
            fill=skin_tone,
            outline=INK_BLACK,
            width=3,
        )

    # 7. Italy: Giant steaming pot of pasta / lasagna
    elif prop == "italy_pasta_pot":
        pot_pts = [
            (240, 750 + y_shift),
            (560, 750 + y_shift),
            (530, 960 + y_shift),
            (270, 960 + y_shift),
        ]
        _draw_polygon_smooth(draw, pot_pts, fill=(226, 232, 240, 255), outline=INK_BLACK, width=5)
        draw.arc([(205, 770 + y_shift), (245, 830 + y_shift)], 90, 270, fill=INK_BLACK, width=4)
        draw.arc([(555, 770 + y_shift), (595, 830 + y_shift)], -90, 90, fill=INK_BLACK, width=4)
        pasta_pts = [
            (245, 750 + y_shift),
            (555, 750 + y_shift),
            (540, 785 + y_shift),
            (260, 785 + y_shift),
        ]
        _draw_polygon_smooth(draw, pasta_pts, fill=(220, 38, 38, 255), outline=INK_BLACK, width=3)
        for py in (755 + y_shift, 765 + y_shift, 775 + y_shift):
            _draw_polyline(
                draw,
                [(270, py), (340, py + 6), (420, py - 4), (530, py + 4)],
                color=(254, 240, 138, 255),
                width=3,
            )
        draw.ellipse([(385, 742 + y_shift), (415, 758 + y_shift)], fill=(22, 163, 74, 255), outline=INK_BLACK, width=2)
        for sx in (300, 400, 500):
            steam = [
                (sx, 735 + y_shift),
                (sx - 15, 690 + y_shift),
                (sx + 15, 645 + y_shift),
                (sx, 600 + y_shift),
            ]
            _draw_polyline(draw, steam, color=(240, 240, 250, 180), width=4)
        _draw_polygon_smooth(
            draw,
            [(195, 780 + y_shift), (235, 780 + y_shift), (235, 825 + y_shift), (195, 825 + y_shift)],
            fill=skin_tone,
            outline=INK_BLACK,
            width=3,
        )
        _draw_polygon_smooth(
            draw,
            [(565, 780 + y_shift), (605, 780 + y_shift), (605, 825 + y_shift), (565, 825 + y_shift)],
            fill=skin_tone,
            outline=INK_BLACK,
            width=3,
        )

    # 8. Netherlands: Big box of Paracetamol 500mg
    elif prop == "netherlands_paracetamol":
        box_pts = [
            (250, 730 + y_shift),
            (550, 730 + y_shift),
            (550, 910 + y_shift),
            (250, 910 + y_shift),
        ]
        _draw_polygon_smooth(draw, box_pts, fill=WHITE, outline=INK_BLACK, width=4)
        banner_pts = [
            (250, 730 + y_shift),
            (550, 730 + y_shift),
            (550, 790 + y_shift),
            (250, 790 + y_shift),
        ]
        _draw_polygon_smooth(draw, banner_pts, fill=(2, 132, 199, 255), outline=INK_BLACK, width=3)
        f_para = _get_font(22, bold=True)
        f_para_sub = _get_font(18, bold=True)
        draw.text((270, 742 + y_shift), "PARACETAMOL", fill=WHITE, font=f_para)
        draw.text((450, 742 + y_shift), "500 mg", fill=WHITE, font=f_para)
        draw.text((270, 810 + y_shift), "Neem 2 tabletten", fill=INK_BLACK, font=f_para_sub)
        draw.text((270, 845 + y_shift), "en ga fietsen!", fill=INK_BLACK, font=f_para_sub)
        draw.line([(480, 835 + y_shift), (510, 835 + y_shift)], fill=BLOOD_RED, width=6)
        draw.line([(495, 820 + y_shift), (495, 850 + y_shift)], fill=BLOOD_RED, width=6)
        _draw_polygon_smooth(
            draw,
            [(225, 800 + y_shift), (265, 800 + y_shift), (265, 860 + y_shift), (225, 860 + y_shift)],
            fill=skin_tone,
            outline=INK_BLACK,
            width=3,
        )
        _draw_polygon_smooth(
            draw,
            [(535, 800 + y_shift), (575, 800 + y_shift), (575, 860 + y_shift), (535, 860 + y_shift)],
            fill=skin_tone,
            outline=INK_BLACK,
            width=3,
        )

    # 9. Germany: DIN-spec aluminium clipboard & fax
    elif prop in ("germany_fax_clipboard", "generic_clipboard"):
        board = [
            (230, 700 + y_shift),
            (570, 700 + y_shift),
            (560, 1020 + y_shift),
            (240, 1020 + y_shift),
        ]
        _draw_polygon_smooth(draw, board, fill=(148, 163, 184, 255), outline=INK_BLACK, width=4)
        paper = [
            (250, 720 + y_shift),
            (550, 720 + y_shift),
            (540, 1000 + y_shift),
            (260, 1000 + y_shift),
        ]
        _draw_polygon_smooth(draw, paper, fill=WHITE, outline=INK_BLACK, width=2)
        clip = [
            (350, 685 + y_shift),
            (450, 685 + y_shift),
            (450, 725 + y_shift),
            (350, 725 + y_shift),
        ]
        _draw_polygon_smooth(draw, clip, fill=(71, 85, 105, 255), outline=INK_BLACK, width=3)
        f_din = _get_font(16, bold=True)
        f_din_sub = _get_font(13, bold=True)
        draw.text((270, 740 + y_shift), "DIN EN ISO 9001 - ANTRAG 34-b", fill=INK_BLACK, font=f_din)
        draw.text((270, 770 + y_shift), "KRANKENKASSE BESTÄTIGUNG", fill=INK_BLACK, font=f_din_sub)
        for ly in range(int(810 + y_shift), int(970 + y_shift), 30):
            draw.line([(270, ly), (530, ly)], fill=(203, 213, 225, 255), width=2)
        draw.rectangle([(420, 910 + y_shift), (510, 955 + y_shift)], outline=BLOOD_RED, width=3)
        draw.text((430, 922 + y_shift), "GENEHMIGT", fill=BLOOD_RED, font=_get_font(14, bold=True))
        fax_strip = [
            (510, 960 + y_shift),
            (580, 980 + y_shift),
            (610, 1080 + y_shift),
            (540, 1060 + y_shift),
        ]
        _draw_polygon_smooth(draw, fax_strip, fill=(255, 255, 240, 255), outline=INK_BLACK, width=2)
        draw.text((540, 1000 + y_shift), "FAX: 1994", fill=INK_BLACK, font=_get_font(13, bold=True))

    # 10. Canada: Calendar pointing to year 2029 for MRI
    elif prop == "canada_calendar":
        cal = [
            (260, 710 + y_shift),
            (540, 710 + y_shift),
            (530, 960 + y_shift),
            (270, 960 + y_shift),
        ]
        _draw_polygon_smooth(draw, cal, fill=WHITE, outline=INK_BLACK, width=4)
        banner = [
            (260, 710 + y_shift),
            (540, 710 + y_shift),
            (540, 770 + y_shift),
            (260, 770 + y_shift),
        ]
        _draw_polygon_smooth(draw, banner, fill=BLOOD_RED, outline=INK_BLACK, width=3)
        f_cal = _get_font(20, bold=True)
        f_yr = _get_font(28, bold=True)
        f_mri = _get_font(18, bold=True)
        draw.text((320, 725 + y_shift), "CALENDAR", fill=WHITE, font=f_cal)
        draw.text((310, 790 + y_shift), "YEAR: 2029", fill=INK_BLACK, font=f_yr)
        draw.text((295, 835 + y_shift), "JULY 14, 2029", fill=BLOOD_RED, font=f_mri)
        draw.ellipse([(280, 825 + y_shift), (490, 875 + y_shift)], outline=BLOOD_RED, width=5)
        draw.text((290, 895 + y_shift), "MRI APPOINTMENT", fill=INK_BLACK, font=f_mri)
        draw.text((295, 925 + y_shift), "(EST. WAIT: 5 YRS)", fill=(100, 100, 100, 255), font=_get_font(14, bold=True))

    # 11. Ireland: Hospital Bed & Big Thumbs Up
    elif prop == "ireland_bed_thumbsup":
        thumb_pts = [
            (480, 730 + y_shift),
            (500, 620 + y_shift),
            (535, 620 + y_shift),
            (530, 710 + y_shift),
            (570, 720 + y_shift),
            (565, 800 + y_shift),
            (490, 820 + y_shift),
        ]
        _draw_polygon_smooth(draw, thumb_pts, fill=skin_tone, outline=INK_BLACK, width=4)
        draw.line([(515, 740 + y_shift), (555, 740 + y_shift)], fill=INK_BLACK, width=3)
        draw.line([(515, 765 + y_shift), (555, 765 + y_shift)], fill=INK_BLACK, width=3)
        draw.rectangle([(160, 780 + y_shift), (240, 860 + y_shift)], fill=WHITE, outline=INK_BLACK, width=3)
        draw.text((180, 795 + y_shift), "FREE", fill=(22, 163, 74, 255), font=_get_font(18, bold=True))
        draw.text((170, 825 + y_shift), "CARE", fill=(22, 163, 74, 255), font=_get_font(18, bold=True))

    # 12. France: Espresso cup & cigarette, strike placard
    elif prop == "france_coffee_cigarette":
        saucer = [
            (440, 760 + y_shift),
            (560, 760 + y_shift),
            (540, 790 + y_shift),
            (460, 790 + y_shift),
        ]
        _draw_polygon_smooth(draw, saucer, fill=WHITE, outline=INK_BLACK, width=3)
        cup = [
            (465, 715 + y_shift),
            (535, 715 + y_shift),
            (525, 765 + y_shift),
            (475, 765 + y_shift),
        ]
        _draw_polygon_smooth(draw, cup, fill=WHITE, outline=INK_BLACK, width=3)
        draw.arc([(525, 725 + y_shift), (555, 755 + y_shift)], -90, 90, fill=INK_BLACK, width=3)
        draw.ellipse([(475, 715 + y_shift), (525, 728 + y_shift)], fill=(120, 53, 15, 255))
        _draw_polyline(
            draw,
            [(500, 705 + y_shift), (495, 675 + y_shift), (510, 645 + y_shift)],
            color=(220, 225, 235, 180),
            width=2,
        )
        _draw_polygon_smooth(
            draw,
            [(420, 760 + y_shift), (460, 760 + y_shift), (460, 800 + y_shift), (420, 800 + y_shift)],
            fill=skin_tone,
            outline=INK_BLACK,
            width=3,
        )

    # 13. Turkey: Hair transplant headwrap & mirror
    elif prop == "turkey_hair_transplant":
        mirror = [
            (480, 640 + y_shift),
            (570, 640 + y_shift),
            (560, 760 + y_shift),
            (470, 760 + y_shift),
        ]
        _draw_polygon_smooth(draw, mirror, fill=(30, 41, 59, 255), outline=INK_BLACK, width=4)
        draw.ellipse([(485, 650 + y_shift), (555, 740 + y_shift)], fill=(224, 242, 254, 255), outline=INK_BLACK, width=2)
        draw.rectangle([(505, 760 + y_shift), (525, 840 + y_shift)], fill=(30, 41, 59, 255), outline=INK_BLACK, width=3)
        _draw_polygon_smooth(
            draw,
            [(490, 780 + y_shift), (540, 780 + y_shift), (540, 825 + y_shift), (490, 825 + y_shift)],
            fill=skin_tone,
            outline=INK_BLACK,
            width=3,
        )

    # 14. Generic fallback cup
    elif prop == "generic_cup":
        mug = [
            (450, 730 + y_shift),
            (540, 730 + y_shift),
            (530, 820 + y_shift),
            (460, 820 + y_shift),
        ]
        _draw_polygon_smooth(draw, mug, fill=WHITE, outline=INK_BLACK, width=4)
        draw.arc([(525, 745 + y_shift), (565, 805 + y_shift)], -90, 90, fill=INK_BLACK, width=4)
        _draw_polygon_smooth(
            draw,
            [(430, 750 + y_shift), (460, 750 + y_shift), (460, 800 + y_shift), (430, 800 + y_shift)],
            fill=skin_tone,
            outline=INK_BLACK,
            width=3,
        )


# ---------------------------------------------------------------------------
# Main Procedural Drawing Pipeline
# ---------------------------------------------------------------------------


AUTHENTIC_WOJAK_PATH = Path(__file__).resolve().parents[3] / "assets" / "characters" / "wojak_doomer_cutout.png"


def _draw_authentic_wojak_base(
    canvas: Image.Image,
    spec: WojakSpec,
    skin_tone: tuple[int, int, int, int],
    y_shift: float = 0.0,
) -> bool:
    """Render canonical hand-drawn Wojak/Doomer base if available."""
    if not AUTHENTIC_WOJAK_PATH.is_file():
        return False
    if spec.expression == "chad" or spec.build == "buff":
        return False
    if spec.gender in ("female", "tradwife", "nonna"):
        return False

    try:
        with Image.open(AUTHENTIC_WOJAK_PATH) as base_im:
            draw = ImageDraw.Draw(canvas)
            h_target = int(660 + (20 if spec.height == "tall" else -20 if spec.height == "short" else 0))
            w_target = int(round(h_target * (base_im.width / max(1, base_im.height))))
            scaled = base_im.resize((w_target, h_target), Image.Resampling.LANCZOS)

            x_pos = (CANVAS_WIDTH - w_target) // 2
            y_pos = int(180 + y_shift)

            # Extend hoodie torso downward to bottom of canvas
            hoodie_color = (65, 68, 72, 255)
            draw.rectangle([x_pos + 40, y_pos + h_target - 40, x_pos + w_target - 40, CANVAS_HEIGHT], fill=hoodie_color)

            # Optional NPC Grey tinting
            if spec.expression == "npc" or spec.skin_tone == NPC_GREY_TONE:
                g = scaled.convert("L")
                scaled = Image.merge("RGBA", (g, g, g, scaled.getchannel("A")))

            canvas.paste(scaled, (x_pos, y_pos), scaled)

            # Draw cyan tears if crying
            if spec.expression in ("crying", "sad"):
                eye_lx = int(x_pos + w_target * 0.44)
                eye_ly = int(y_pos + h_target * 0.44)
                eye_rx = int(x_pos + w_target * 0.64)
                eye_ry = int(y_pos + h_target * 0.45)

                tear_color = (56, 189, 248, 240)
                tear_edge = (14, 116, 144, 255)
                tear_pts_l = [
                    (eye_lx - 6, eye_ly),
                    (eye_lx + 8, eye_ly + 10),
                    (eye_lx + 4, eye_ly + 60),
                    (eye_lx + 15, eye_ly + 120),
                    (eye_lx + 8, eye_ly + 200),
                    (eye_lx - 4, eye_ly + 260),
                    (eye_lx - 12, eye_ly + 180),
                    (eye_lx - 6, eye_ly + 90),
                ]
                draw.polygon(tear_pts_l, fill=tear_color, outline=tear_edge)

                tear_pts_r = [
                    (eye_rx - 4, eye_ry),
                    (eye_rx + 8, eye_ry + 10),
                    (eye_rx + 12, eye_ry + 80),
                    (eye_rx + 18, eye_ry + 150),
                    (eye_rx + 10, eye_ry + 240),
                    (eye_rx + 2, eye_ry + 180),
                    (eye_rx - 2, eye_ry + 90),
                ]
                draw.polygon(tear_pts_r, fill=tear_color, outline=tear_edge)

            # Draw hospital bandages if bandaged
            if spec.expression in ("bandaged", "injured") or "bandage" in spec.scenario or "bandage" in spec.prop:
                forehead_y = int(y_pos + h_target * 0.22)
                bandage_pts = [
                    (int(x_pos + w_target * 0.25), forehead_y - 20),
                    (int(x_pos + w_target * 0.78), forehead_y - 15),
                    (int(x_pos + w_target * 0.82), forehead_y + 55),
                    (int(x_pos + w_target * 0.22), forehead_y + 50),
                ]
                draw.polygon(bandage_pts, fill=(245, 245, 248, 245), outline=(60, 60, 60, 255), width=3)
                draw.line(
                    [(int(x_pos + w_target * 0.24), forehead_y + 18), (int(x_pos + w_target * 0.80), forehead_y + 22)],
                    fill=(180, 180, 185, 255),
                    width=2,
                )
                cheek_x = int(x_pos + w_target * 0.40)
                cheek_y = int(y_pos + h_target * 0.60)
                draw.rectangle([cheek_x, cheek_y, cheek_x + 60, cheek_y + 22], fill=(225, 175, 130, 255), outline=(120, 80, 50, 255), width=2)

            # Foreground props and hands
            _draw_props_and_hands(draw, spec, skin_tone, y_shift=y_shift)
            return True
    except Exception as exc:
        logger.warning("Could not render authentic wojak base: %s", exc)
        return False


def draw_wojak(spec: WojakSpec) -> Image.Image:
    """Render a fully procedural Wojak character onto an 800x1200 RGBA canvas.

    Outputs crisp hand-drawn vector ink contours and accurate transparent background.
    """
    img = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Height adjustment
    # Short characters shift down slightly; tall characters shift up slightly
    y_shift = 0.0
    if spec.height == "short":
        y_shift = 30.0
    elif spec.height == "tall":
        y_shift = -20.0

    # 2. Resolve skin tone
    if spec.skin_tone is not None:
        skin_tone = spec.skin_tone
    elif spec.expression == "npc" and spec.race == "fair":
        skin_tone = NPC_GREY_TONE
    else:
        skin_tone = SKIN_TONES.get(spec.race, SKIN_TONES["fair"])

    # Try authentic Wojak base first (Feels Guy / Doomer ground-truth style)
    if _draw_authentic_wojak_base(img, spec, skin_tone, y_shift=y_shift):
        return img

    # 3. Draw background props (hospital bed, trolley, placard)
    _draw_background_props(draw, spec, y_shift=y_shift)

    # 4. Head & neck geometry determination
    is_chad = spec.expression == "chad" or spec.build == "buff"
    if is_chad:
        neck_l, neck_r = 310.0, 490.0
        chin_y = 545.0 + y_shift
    elif spec.gender in ("female", "tradwife"):
        neck_l, neck_r = 345.0, 455.0
        chin_y = 520.0 + y_shift
    else:
        neck_l, neck_r = 335.0, 465.0
        chin_y = 532.0 + y_shift

    # 5. Draw Torso and Clothing
    _draw_torso_and_clothing(draw, spec, neck_l, neck_r, skin_tone, y_shift=y_shift)

    # 6. Draw Neck and Muscle definition
    _draw_neck_and_muscles(draw, spec, neck_l, neck_r, chin_y, skin_tone, y_shift=y_shift)

    # 7. Draw Head silhouette & Ear
    _draw_head_silhouette(draw, spec, skin_tone, y_shift=y_shift)

    # 8. Draw Facial features (eyes, nose, mouth, wrinkles, stubble, etc.)
    _draw_facial_features(draw, spec, skin_tone, y_shift=y_shift)

    # 9. Draw Tears layer (if crying)
    _draw_tears_layer(draw, spec, y_shift=y_shift)

    # 10. Draw Hair and Headwear
    _draw_hair_and_headwear(draw, spec, neck_l, neck_r, skin_tone, y_shift=y_shift)

    # 11. Draw Foreground Props & Hands
    _draw_props_and_hands(draw, spec, skin_tone, y_shift=y_shift)

    return img


# ---------------------------------------------------------------------------
# Caching & Convenience Utility
# ---------------------------------------------------------------------------

CHARACTERS_CACHE_DIR = Path(__file__).resolve().parents[3] / "assets" / "characters" / "wojaks"


def spec_hash(spec: WojakSpec) -> str:
    """Generate deterministic 16-hex hash of spec."""
    raw = (
        f"{spec.country}:{spec.setting}:{spec.scenario}:{spec.age}:{spec.gender}:"
        f"{spec.race}:{spec.height}:{spec.build}:{spec.expression}:{spec.hair}:"
        f"{spec.clothing}:{spec.prop}:{spec.seed}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def wojak_for_context(
    country: str,
    setting: str,
    scenario: str,
    age: str | None = None,
    gender: str | None = None,
    race: str | None = None,
    **kwargs: Any,
) -> Path:
    """Generate or retrieve cached Wojak PNG file for the given context."""
    spec = spec_from_country_and_context(
        country=country,
        setting=setting,
        scenario=scenario,
        age=age,
        gender=gender,
        race=race,
        **kwargs,
    )
    CHARACTERS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CHARACTERS_CACHE_DIR / f"wojak_{spec_hash(spec)}.png"
    if not out_path.exists():
        img = draw_wojak(spec)
        img.save(out_path, format="PNG")
    return out_path
