"""Wojak Viral Video Researcher & Idea Engine.

Researches and synthesizes viral Wojak video ideas modeled after:
1. @Wojak-Explainer: Archetype anthologies ("Types of {Subculture} Be Like", "Every {Group} in a Nutshell").
2. SapX: Satirical country comparison memes ("Every Country's {System}").

Provides structured briefs with comedic hooks, archetype breakdowns, recommended props,
diverse character specs, and punchlines strictly under 120 characters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WojakSceneIdea:
    """A proposed scene in a researched Wojak video."""

    heading: str  # e.g. "USA", "THE SOCIAL INTROVERT", "THE CHAD LIFTER"
    punchline: str
    emotion: str = "shocked"  # shocked, crying, smug, chad, tired, happy, npc, bandaged
    prop: str = "bill"
    setting: str = "hospital"
    gender: str = "male"  # male, female, nonna, tradwife
    race: str = "fair"  # fair, olive, east_asian, south_asian, middle_eastern, black, latino
    height: str = "average"  # short, average, tall
    clothing: str = "hospital_gown"
    character_side: str = "left"


@dataclass
class WojakVideoBrief:
    """Complete strategic brief for a viral Wojak video."""

    title: str
    category: str  # "country_parody" or "archetype_anthology"
    format: str  # "16:9" or "9:16"
    hook: str
    target_duration_sec: float
    recommended_bgm_mood: str
    scenes: list[WojakSceneIdea] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Viral Knowledge Base & Topic Banks
# ---------------------------------------------------------------------------

VIRAL_COUNTRY_TOPICS = [
    {
        "topic": "hospital",
        "title": "Every Country's Hospital Bill",
        "hook": "Healthcare around the world compared: from $180,000 bills to herbal tea and 4-year waitlists.",
        "setting": "hospital",
        "scenes": [
            WojakSceneIdea(
                "USA",
                "1x Tylenol: $4,500. Ambulance: $18,000. Total: Bankruptcy.",
                "crying",
                "usa_bill",
                "hospital",
                "male",
                "fair",
                "tall",
                "hospital_gown",
                "left",
            ),
            WojakSceneIdea(
                "UK",
                "Treatment is free! But the waiting room has patients from 2019.",
                "npc",
                "uk_trolley_tea",
                "hospital",
                "male",
                "fair",
                "average",
                "hospital_gown",
                "right",
            ),
            WojakSceneIdea(
                "South Korea",
                "Came for a cough, left with jawline surgery and 10-step skincare.",
                "bandaged",
                "korea_bandages",
                "hospital",
                "female",
                "east_asian",
                "average",
                "hospital_gown",
                "left",
            ),
            WojakSceneIdea(
                "India",
                "Consultation was $2. Doctor wrote 16 prescriptions and cured it instantly.",
                "chad",
                "india_stethoscope",
                "hospital",
                "male",
                "south_asian",
                "tall",
                "doctor_coat",
                "right",
            ),
            WojakSceneIdea(
                "Canada",
                "100% free care! Next available MRI scan: November 2033.",
                "tired",
                "canada_calendar",
                "hospital",
                "female",
                "fair",
                "average",
                "casual_hoodie",
                "left",
            ),
            WojakSceneIdea(
                "Italy",
                "Doctor was drinking espresso, prescribed fresh air and Nonna's lasagna.",
                "happy",
                "italy_pasta_pot",
                "hospital",
                "nonna",
                "olive",
                "short",
                "apron",
                "right",
            ),
            WojakSceneIdea(
                "Germany",
                "Must submit Krankenkasse Form 48B in triplicate stamped by the Bürgermeister.",
                "npc",
                "germany_fax_clipboard",
                "hospital",
                "male",
                "fair",
                "tall",
                "business_suit",
                "left",
            ),
            WojakSceneIdea(
                "Sweden",
                "Doctor gave herbal tea, said lagom, and told me to sit in forest.",
                "smug",
                "sweden_phone_queue",
                "hospital",
                "male",
                "fair",
                "tall",
                "casual_hoodie",
                "right",
            ),
        ],
    },
    {
        "topic": "transport",
        "title": "Every Country's Public Transport",
        "hook": "Public transit across the globe: from Japanese bullet precision to chaotic joyful buses.",
        "setting": "transport",
        "scenes": [
            WojakSceneIdea(
                "Japan",
                "Train arrived 2 seconds early. Stationmaster apologized on live TV.",
                "npc",
                "clipboard",
                "transport",
                "female",
                "east_asian",
                "short",
                "business_suit",
                "left",
            ),
            WojakSceneIdea(
                "USA",
                "Waiting 45 mins for a bus with no shelter in 98 degree heat.",
                "crying",
                "phone",
                "transport",
                "male",
                "fair",
                "tall",
                "casual_hoodie",
                "right",
            ),
            WojakSceneIdea(
                "India",
                "Train is at 300% capacity. Passengers hanging on like mountain goats.",
                "chad",
                "phone",
                "transport",
                "male",
                "south_asian",
                "tall",
                "doctor_coat",
                "left",
            ),
            WojakSceneIdea(
                "Germany",
                "DB train is delayed 80 mins. Reason: 'Unexpected track existence'.",
                "annoyed",
                "germany_fax_clipboard",
                "transport",
                "male",
                "fair",
                "tall",
                "business_suit",
                "right",
            ),
            WojakSceneIdea(
                "UK",
                "Replacement bus service for the replacement train on strike.",
                "tired",
                "uk_trolley_tea",
                "transport",
                "male",
                "fair",
                "average",
                "casual_hoodie",
                "left",
            ),
            WojakSceneIdea(
                "France",
                "Entire metro on strike because coffee machine was down 5 mins.",
                "smug",
                "france_coffee_cigarette",
                "transport",
                "male",
                "fair",
                "average",
                "trench_coat",
                "right",
            ),
        ],
    },
    {
        "topic": "school",
        "title": "Every Country's High School Lunch",
        "hook": "Cafeteria meals compared: Michelin star trays vs mysterious grey cubes.",
        "setting": "school",
        "scenes": [
            WojakSceneIdea(
                "France",
                "Four-course meal with brie, roast chicken, and fresh baguette.",
                "smug",
                "pastry",
                "school",
                "female",
                "fair",
                "average",
                "casual_hoodie",
                "left",
            ),
            WojakSceneIdea(
                "USA",
                "Cardboard pizza rectangle, frozen corn, and chocolate milk bag.",
                "crying",
                "generic_cup",
                "school",
                "male",
                "fair",
                "tall",
                "casual_hoodie",
                "right",
            ),
            WojakSceneIdea(
                "Japan",
                "Students serve fresh salmon, miso soup, and rice in unison.",
                "happy",
                "tea_cup",
                "school",
                "female",
                "east_asian",
                "short",
                "business_suit",
                "left",
            ),
            WojakSceneIdea(
                "Italy",
                "Fresh handmade pasta with basil pomodoro and warm focaccia.",
                "happy",
                "italy_pasta_pot",
                "school",
                "nonna",
                "olive",
                "short",
                "apron",
                "right",
            ),
            WojakSceneIdea(
                "UK",
                "Beans on toast with soggy chips and pink custard tart.",
                "tired",
                "uk_trolley_tea",
                "school",
                "male",
                "fair",
                "average",
                "casual_hoodie",
                "left",
            ),
        ],
    },
]

VIRAL_ARCHETYPE_TOPICS = [
    {
        "topic": "gym",
        "title": "Types of Gym Goers Be Like",
        "hook": "The ultimate gym archetype guide: from the tripod influencer to the ancient locker guru.",
        "setting": "gym",
        "scenes": [
            WojakSceneIdea(
                "THE TRIPOD INFLUENCER",
                "Takes 40 mins filming one set. Yells if someone walks in 4K frame.",
                "smug",
                "phone",
                "gym",
                "female",
                "fair",
                "average",
                "casual_hoodie",
                "left",
            ),
            WojakSceneIdea(
                "THE HYPER-DRIVEN CHAD",
                "Hits PR, drinks 3 scoops of pre-workout dry, doesn't blink once.",
                "chad",
                "generic_cup",
                "gym",
                "male",
                "fair",
                "tall",
                "casual_hoodie",
                "right",
            ),
            WojakSceneIdea(
                "THE SLEEPY RESOLUTIONIST",
                "Bought $400 gym gear in January. Only visits to use massage chairs.",
                "tired",
                "generic_cup",
                "gym",
                "male",
                "south_asian",
                "average",
                "casual_hoodie",
                "left",
            ),
            WojakSceneIdea(
                "THE LOCKER ROOM PHILOSOPHER",
                "Ancient guru giving life advice in the locker room for 2 hours.",
                "happy",
                "towel",
                "gym",
                "male",
                "olive",
                "short",
                "casual_hoodie",
                "right",
            ),
        ],
    },
    {
        "topic": "introverts",
        "title": "Types of Introverts Be Like",
        "hook": "The spectrum of introversion: from 2-friend loyalists to instant ghosters.",
        "setting": "cafe",
        "scenes": [
            WojakSceneIdea(
                "THE SOCIAL INTROVERT",
                "Loves hanging with exactly two friends. A 3rd drops battery to 0%.",
                "happy",
                "tea_cup",
                "cafe",
                "female",
                "fair",
                "average",
                "casual_hoodie",
                "left",
            ),
            WojakSceneIdea(
                "THE MASTER GHOSTER",
                "Agreed to plans 5 days ago. Texts 'car died' and feels pure bliss.",
                "smug",
                "phone",
                "cafe",
                "male",
                "fair",
                "tall",
                "casual_hoodie",
                "right",
            ),
            WojakSceneIdea(
                "THE NOISE-CANCELLER",
                "Wears giant headphones everywhere just so nobody dares talk to him.",
                "doomer",
                "phone",
                "cafe",
                "male",
                "east_asian",
                "average",
                "casual_hoodie",
                "left",
            ),
            WojakSceneIdea(
                "THE OVERTHINKER",
                "Drafts 14 replies to 'what\\'s up', deletes all, and turns on airplane mode.",
                "crying",
                "phone",
                "cafe",
                "female",
                "latino",
                "short",
                "casual_hoodie",
                "right",
            ),
        ],
    },
    {
        "topic": "coders",
        "title": "Types of Programmers Be Like",
        "hook": "From junior copiers to 10x Vim legends with 400 terminals.",
        "setting": "office",
        "scenes": [
            WojakSceneIdea(
                "THE STACK-OVERFLOW PRO",
                "Copies 8-year-old code. Has no idea how it works, but passes CI/CD.",
                "happy",
                "generic_clipboard",
                "office",
                "male",
                "south_asian",
                "average",
                "casual_hoodie",
                "left",
            ),
            WojakSceneIdea(
                "THE 10X TERMINAL CHAD",
                "Writes kernel patches in pure Vim with keyboard clicks at 180 WPM.",
                "chad",
                "generic_cup",
                "office",
                "male",
                "fair",
                "tall",
                "casual_hoodie",
                "right",
            ),
            WojakSceneIdea(
                "THE BUG DETECTIVE",
                "Missing semicolon at 3 AM. Questions entire existence and life path.",
                "doomer",
                "coffee",
                "office",
                "male",
                "fair",
                "tall",
                "casual_hoodie",
                "left",
            ),
            WojakSceneIdea(
                "THE FRAMEWORK HOPPER",
                "Rewrites the same todo app in 6 new JavaScript frameworks every week.",
                "smug",
                "phone",
                "office",
                "female",
                "east_asian",
                "short",
                "casual_hoodie",
                "right",
            ),
        ],
    },
]


# ---------------------------------------------------------------------------
# Researcher Core Functionality
# ---------------------------------------------------------------------------


def research_viral_wojak_ideas(
    query: str | None = None,
    category: str | None = None,
    aspect_ratio: str = "16:9",
    segment_count: int = 6,
) -> list[WojakVideoBrief]:
    """Research, curate, and generate high-potential viral Wojak video briefs."""
    results: list[WojakVideoBrief] = []
    q_clean = (query or "").lower().strip()

    # Search in curated catalogs
    pool: list[dict[str, Any]] = []
    if category == "country_parody" or not category:
        pool.extend(VIRAL_COUNTRY_TOPICS)
    if category == "archetype_anthology" or not category:
        pool.extend(VIRAL_ARCHETYPE_TOPICS)

    # Filter matching topics or use all if query empty
    matched = (
        [t for t in pool if q_clean in t["topic"] or q_clean in t["title"].lower()]
        if q_clean
        else pool
    )
    if not matched:
        matched = pool

    for item in matched:
        scenes = [
            WojakSceneIdea(
                heading=s.heading,
                punchline=s.punchline,
                emotion=s.emotion,
                prop=s.prop,
                setting=s.setting,
                gender=s.gender,
                race=s.race,
                height=s.height,
                clothing=s.clothing,
                character_side="left" if idx % 2 == 0 else "right",
            )
            for idx, s in enumerate(item["scenes"][:segment_count])
        ]

        cat = "country_parody" if "country" in item["title"].lower() else "archetype_anthology"
        dur = 3.5 + len(scenes) * 4.5
        brief = WojakVideoBrief(
            title=item["title"],
            category=cat,
            format=aspect_ratio,
            hook=item["hook"],
            target_duration_sec=dur,
            recommended_bgm_mood="comedic_ragtime_bouncy",
            scenes=scenes,
        )
        results.append(brief)

    # If query was completely custom and no direct match, generate procedural dynamic brief
    if q_clean and not any(q_clean in b.title.lower() for b in results):
        custom_brief = _generate_procedural_brief(query or "random", aspect_ratio, segment_count)
        results.insert(0, custom_brief)

    return results


def _generate_procedural_brief(
    query: str, aspect_ratio: str, segment_count: int
) -> WojakVideoBrief:
    """Dynamically generate a novel Wojak video brief on the fly for any topic."""
    title = f"Types of {query.title()} Be Like"
    archetypes = [
        (
            "THE OVERTHINKER",
            "Analyzes every tiny detail until the opportunity has completely passed.",
            "crying",
            "phone",
            "female",
            "fair",
            "short",
        ),
        (
            "THE ABSOLUTE CHAD",
            "Shows up with zero preparation, succeeds effortlessly, leaves without elaborating.",
            "chad",
            "generic_cup",
            "male",
            "south_asian",
            "tall",
        ),
        (
            "THE DOOMER",
            "Knows exactly how this will go wrong, called it 3 weeks ago, nobody listened.",
            "doomer",
            "coffee",
            "male",
            "fair",
            "tall",
        ),
        (
            "THE ENTHUSIAST",
            "Joined 2 days ago, already made it their entire personality and wardrobe.",
            "happy",
            "tea_cup",
            "female",
            "east_asian",
            "average",
        ),
        (
            "THE SILENT OBSERVER",
            "Says 4 words the entire time, but makes the deadliest accurate observation.",
            "smug",
            "generic_clipboard",
            "male",
            "olive",
            "average",
        ),
        (
            "THE CHAOS AGENT",
            "Does the exact opposite of instructions just to see what happens.",
            "shocked",
            "phone",
            "female",
            "latino",
            "short",
        ),
    ][:segment_count]

    scenes = [
        WojakSceneIdea(
            heading=arch[0],
            punchline=arch[1],
            emotion=arch[2],
            prop=arch[3],
            setting="office",
            gender=arch[4],
            race=arch[5],
            height=arch[6],
            character_side="left" if idx % 2 == 0 else "right",
        )
        for idx, arch in enumerate(archetypes)
    ]

    return WojakVideoBrief(
        title=title,
        category="archetype_anthology",
        format=aspect_ratio,
        hook=f"A satirical breakdown of every type of {query.lower()} you meet in real life.",
        target_duration_sec=3.5 + len(scenes) * 4.5,
        recommended_bgm_mood="comedic_ragtime_bouncy",
        scenes=scenes,
    )
