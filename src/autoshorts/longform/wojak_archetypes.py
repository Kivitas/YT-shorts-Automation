"""Wojak Explainer Archetype Catalog & Script Generator.

Models the video format of the popular YouTube channel '@Wojak-Explainer':
- "Types of {Subculture} be like" (Introverts, Gym Goers, Coders, Gamers, Drivers, Students)
- "Every {Category} in a nutshell" (Generations, Office Workers, Dating Profiles)

Provides curated archetypes with hand-drawn Wojak specs, expressive props,
punchlines strictly under 120 chars, and atmospheric scene backgrounds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArchetypeSegment:
    """A single archetype card in a Wojak Explainer video."""

    archetype_title: str
    caption: str
    character_archetype: str  # doomer, bloomer, chad, tradwife, soyjak, zoomer, boomer, npc, coomer
    expression: str  # tired, happy, smug, shocked, crying, neutral, annoyed
    prop: str  # phone, coffee, weights, monster_can, keyboard, book, vape, switch
    setting: str  # bedroom, gym, office, cafe, classroom, highway
    gender: str = "male"
    race: str = "fair"


ARCHETYPE_TOPICS: Final[dict[str, dict[str, Any]]] = {
    "introverts": {
        "title": "Types of Introverts Be Like",
        "setting": "cozy room",
        "segments": [
            ArchetypeSegment(
                archetype_title="THE SOCIAL INTROVERT",
                caption="Loves hanging out with exactly two best friends. Add a third person and mental battery drops to 0%.",
                character_archetype="bloomer",
                expression="happy",
                prop="tea_cup",
                setting="cafe",
            ),
            ArchetypeSegment(
                archetype_title="THE MASTER GHOSTER",
                caption="Agreed to plans 5 days ago. Texts 'car won't start' 5 minutes prior and feels pure euphoric relief.",
                character_archetype="doomer",
                expression="smug",
                prop="phone",
                setting="bedroom",
            ),
            ArchetypeSegment(
                archetype_title="THE 3 AM REPLAYER",
                caption="Cannot sleep because brain is replaying an awkward high-five interaction from October 2018 in 4K.",
                character_archetype="doomer",
                expression="tired",
                prop="phone",
                setting="dark bedroom",
            ),
            ArchetypeSegment(
                archetype_title="THE PUBLIC RECLUSE",
                caption="Wears noise-cancelling headphones with zero music playing just so nobody speaks to them in public.",
                character_archetype="doomer",
                expression="neutral",
                prop="phone",
                setting="subway",
            ),
            ArchetypeSegment(
                archetype_title="THE TEXT OVERTHINKER",
                caption="Drafts a 3-word reply, deletes it 14 times, closes app, responds 3 weeks later with 'sorry was busy'.",
                character_archetype="doomer",
                expression="shocked",
                prop="phone",
                setting="bedroom",
            ),
            ArchetypeSegment(
                archetype_title="THE HOMEBODY GIGACHAD",
                caption="Declined 6 party invites to read philosophy, cook steak, and sleep 9 hours. Completely unbothered.",
                character_archetype="chad",
                expression="smug",
                prop="book",
                setting="cozy library",
            ),
        ],
    },
    "gym": {
        "title": "Types of Gym Members Be Like",
        "setting": "fitness gym",
        "segments": [
            ArchetypeSegment(
                archetype_title="THE TRIPOD INFLUENCER",
                caption="Takes 25 minutes adjusting camera angle, ring light, and outfit. Performs exactly one warmup rep.",
                character_archetype="zoomer",
                expression="smug",
                prop="phone",
                setting="gym mirror",
            ),
            ArchetypeSegment(
                archetype_title="THE SILENT HEAVYWEIGHT",
                caption="Wears a faded 2014 hoodie. Speaks to nobody. Benches 405 lbs in complete silence, re-racks, leaves.",
                character_archetype="chad",
                expression="neutral",
                prop="weights",
                setting="power rack",
            ),
            ArchetypeSegment(
                archetype_title="THE PRE-WORKOUT FIEND",
                caption="Dry-scooped 3 scoops of pre-workout. Can hear his own heartbeat through the walls. Eyes vibrating.",
                character_archetype="soyjak",
                expression="shocked",
                prop="shaker",
                setting="gym locker",
            ),
            ArchetypeSegment(
                archetype_title="THE BENCH CAMPER",
                caption="Did one set of bench press 18 minutes ago. Has been scrolling TikTok on the bench ever since.",
                character_archetype="doomer",
                expression="tired",
                prop="phone",
                setting="bench press",
            ),
            ArchetypeSegment(
                archetype_title="THE SAUNA PHILOSOPHER",
                caption="72-year-old man sitting in 190-degree sauna telling strangers how he cured his sciatica with raw liver.",
                character_archetype="boomer",
                expression="smug",
                prop="towel",
                setting="sauna room",
            ),
            ArchetypeSegment(
                archetype_title="THE RESOLUTION WARRIOR",
                caption="Bought $400 worth of matching gym gear on January 1st. Has not been seen since January 8th.",
                character_archetype="bloomer",
                expression="crying",
                prop="shaker",
                setting="gym floor",
            ),
        ],
    },
    "generations": {
        "title": "Every Generation Be Like",
        "setting": "living room",
        "segments": [
            ArchetypeSegment(
                archetype_title="THE BOOMER (1946-1964)",
                caption="Bought a 4-bedroom house in 1974 for $14,000 and two goats. Types text messages with one index finger.",
                character_archetype="boomer",
                expression="smug",
                prop="monster_can",
                setting="suburban lawn",
            ),
            ArchetypeSegment(
                archetype_title="GEN X (1965-1980)",
                caption="Entire world forgot they exist. Raised by television and hose water. Cynical, independent, unaffected.",
                character_archetype="doomer",
                expression="neutral",
                prop="coffee",
                setting="garage",
            ),
            ArchetypeSegment(
                archetype_title="THE MILLENNIAL (1981-1996)",
                caption="Has a master's degree, $80k debt, and an emotional support water bottle. Dreams of owning one house plant.",
                character_archetype="doomer",
                expression="crying",
                prop="coffee",
                setting="rental apartment",
            ),
            ArchetypeSegment(
                archetype_title="GEN Z (1997-2012)",
                caption="Attention span is 4.2 seconds. Communicates via 6 layers of irony. Says 'no cap on god fr fr' in job interviews.",
                character_archetype="zoomer",
                expression="shocked",
                prop="phone",
                setting="bedroom LED",
            ),
            ArchetypeSegment(
                archetype_title="GEN ALPHA (2013+)",
                caption="Learned how to swipe an iPad before learning how to walk. Knows every Skibidi lore theory by heart.",
                character_archetype="soyjak",
                expression="shocked",
                prop="tablet",
                setting="couch with tablet",
            ),
            ArchetypeSegment(
                archetype_title="THE ANCIENT GIGACHAD",
                caption="Lived through dial-up internet, VHS tapes, and 2008 crash without complaining once. Endures everything.",
                character_archetype="chad",
                expression="smug",
                prop="book",
                setting="mountain porch",
            ),
        ],
    },
    "coders": {
        "title": "Types of Programmers Be Like",
        "setting": "tech office",
        "segments": [
            ArchetypeSegment(
                archetype_title="THE JUNIOR DEV",
                caption="Pushed an API key directly to a public GitHub repo. Is currently sweating through his hoodie.",
                character_archetype="doomer",
                expression="shocked",
                prop="keyboard",
                setting="desk monitors",
            ),
            ArchetypeSegment(
                archetype_title="THE SENIOR ARCHITECT",
                caption="Deletes 8,000 lines of legacy spaghetti code in 10 minutes. Fixes production with one single semicolon.",
                character_archetype="chad",
                expression="smug",
                prop="coffee",
                setting="dark terminal",
            ),
            ArchetypeSegment(
                archetype_title="THE STACK OVERFLOW WARRIOR",
                caption="Has no idea how his code works. Copies a 9-year-old answer marked with 2 upvotes. It somehow compiles.",
                character_archetype="soyjak",
                expression="happy",
                prop="keyboard",
                setting="browser tabs",
            ),
            ArchetypeSegment(
                archetype_title="THE LINUX EVANGELIST",
                caption="Will spend 6 hours explaining why his tiling window manager is 4% faster than Windows 11.",
                character_archetype="doomer",
                expression="neutral",
                prop="keyboard",
                setting="custom desktop",
            ),
            ArchetypeSegment(
                archetype_title="THE AI PROMPT ENGINEER",
                caption="Types 'make it look modern' into ChatGPT. Claims on LinkedIn to be a Senior AI Solutions Executive.",
                character_archetype="zoomer",
                expression="smug",
                prop="phone",
                setting="coffee shop",
            ),
        ],
    },
    "students": {
        "title": "Types of Students During Exams",
        "setting": "university library",
        "segments": [
            ArchetypeSegment(
                archetype_title="THE ALL-NIGHTER SURVIVOR",
                caption="Has consumed 4 energy drinks, 0 hours of sleep, and is currently reading Chapter 1 at 5:45 AM.",
                character_archetype="doomer",
                expression="tired",
                prop="coffee",
                setting="library desk",
            ),
            ArchetypeSegment(
                archetype_title="THE 'I DIDN'T STUDY' LIAR",
                caption="Cries in hallway saying 'I definitely failed'. Gets 98% on test, asks professor for extra credit.",
                character_archetype="soyjak",
                expression="crying",
                prop="notebook",
                setting="exam hall",
            ),
            ArchetypeSegment(
                archetype_title="THE WING-IT GIGACHAD",
                caption="Showed up without a pen, borrowed a pencil from stranger, finishes exam in 20 minutes, walks out.",
                character_archetype="chad",
                expression="smug",
                prop="pencil",
                setting="classroom",
            ),
            ArchetypeSegment(
                archetype_title="THE COLOR-CODED HIGHLIGHTER",
                caption="Has 38 pastel highlighters. Entire textbook is glowing neon yellow. Has retained zero information.",
                character_archetype="bloomer",
                expression="happy",
                prop="highlighter",
                setting="study desk",
            ),
            ArchetypeSegment(
                archetype_title="THE PURE GUESSWORK MASTER",
                caption="When in doubt, chooses option 'C' for 40 consecutive multiple choice questions. Prays for a passing grade.",
                character_archetype="npc",
                expression="neutral",
                prop="exam_paper",
                setting="desk",
            ),
        ],
    },
}


def resolve_archetype_topic(topic_query: str) -> dict[str, Any] | None:
    """Resolve a user topic string to a matching curated Wojak Explainer topic."""
    q = topic_query.strip().lower()
    for key, data in ARCHETYPE_TOPICS.items():
        if key in q or q in key:
            return data
        if any(w in q for w in key.split("_")):
            return data
    return None
