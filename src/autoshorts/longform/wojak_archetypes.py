"""Wojak Explainer Archetype Catalog & Script Generator.

Models the video format of the popular YouTube channel '@Wojak-Explainer':
- "Types of {Subculture} be like" (Introverts, Gym Goers, Coders, Gamers, Drivers, Students, Music Listeners)
- "Every {Category} in a nutshell" (Generations, Office Workers, Dating Profiles)

Strictly aligned with the Wojak Explainer Production Bible (Section 6):
Provides 10 canonical archetype topics with curated archetype segments, hand-drawn
Wojak character specifications, expressive comedic props, punchlines strictly under
120 characters for fast comedic reading, atmospheric scene backgrounds, and explicit
gender and race demographic diversity.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArchetypeSegment:
    """A single archetype card in a Wojak Explainer video."""

    archetype_title: str
    caption: str
    character_archetype: str  # doomer, bloomer, chad, tradwife, soyjak, zoomer, boomer, npc, coomer
    expression: str  # tired, happy, smug, shocked, crying, neutral, annoyed, deadpan
    prop: str  # phone, coffee, weights, monster_can, keyboard, book, vape, switch, etc.
    setting: str  # bedroom, gym, office, cafe, classroom, highway, etc.
    gender: str = "male"  # male, female, tradwife, nonna
    race: str = "fair"  # fair, olive, east_asian, south_asian, middle_eastern, black, latino


# 10 Canonical Topics matching Production Bible Section 6
ARCHETYPE_TOPICS: Final[dict[str, dict[str, Any]]] = {
    # 1. Types of Introverts Be Like
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
                gender="female",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE MASTER GHOSTER",
                caption="Agreed to plans 5 days ago. Texts 'car won't start' 5 minutes prior and feels pure euphoric relief.",
                character_archetype="doomer",
                expression="smug",
                prop="phone",
                setting="bedroom",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE 3 AM REPLAYER",
                caption="Cannot sleep because brain is replaying an awkward high-five interaction from October 2018 in 4K.",
                character_archetype="doomer",
                expression="tired",
                prop="phone",
                setting="dark bedroom",
                gender="female",
                race="east_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE PUBLIC RECLUSE",
                caption="Wears noise-cancelling headphones with zero music playing just so nobody speaks to them in public.",
                character_archetype="doomer",
                expression="neutral",
                prop="headphones",
                setting="subway",
                gender="male",
                race="black",
            ),
            ArchetypeSegment(
                archetype_title="THE TEXT OVERTHINKER",
                caption="Drafts a 3-word reply, deletes it 14 times, closes app, responds 3 weeks later with 'sorry was busy'.",
                character_archetype="soyjak",
                expression="shocked",
                prop="phone",
                setting="bedroom",
                gender="male",
                race="latino",
            ),
            ArchetypeSegment(
                archetype_title="THE HOMEBODY GIGACHAD",
                caption="Declined 6 party invites to read philosophy, cook steak, and sleep 9 hours. Completely unbothered.",
                character_archetype="chad",
                expression="smug",
                prop="book",
                setting="cozy library",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE DOORBELL PARALYSIS",
                caption="Freezes in dead silence for 12 minutes when food delivery driver knocks instead of leaving at door.",
                character_archetype="doomer",
                expression="shocked",
                prop="phone",
                setting="hallway",
                gender="female",
                race="south_asian",
            ),
        ],
    },
    # 2. Types of Gym Members Be Like
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
                gender="female",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE SILENT HEAVYWEIGHT",
                caption="Wears a faded 2014 hoodie. Speaks to nobody. Benches 405 lbs in complete silence, re-racks, leaves.",
                character_archetype="chad",
                expression="neutral",
                prop="weights",
                setting="power rack",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE PRE-WORKOUT FIEND",
                caption="Dry-scooped 3 scoops of pre-workout. Can hear his own heartbeat through the walls. Eyes vibrating.",
                character_archetype="soyjak",
                expression="shocked",
                prop="shaker",
                setting="gym locker",
                gender="male",
                race="latino",
            ),
            ArchetypeSegment(
                archetype_title="THE BENCH CAMPER",
                caption="Did one set of bench press 18 minutes ago. Has been scrolling TikTok on the bench ever since.",
                character_archetype="doomer",
                expression="tired",
                prop="phone",
                setting="bench press",
                gender="male",
                race="black",
            ),
            ArchetypeSegment(
                archetype_title="THE SAUNA PHILOSOPHER",
                caption="72-year-old man sitting in 190-degree sauna telling strangers how he cured his sciatica with raw liver.",
                character_archetype="boomer",
                expression="smug",
                prop="towel",
                setting="sauna room",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE RESOLUTION WARRIOR",
                caption="Bought $400 worth of matching gym gear on January 1st. Has not been seen since January 8th.",
                character_archetype="bloomer",
                expression="crying",
                prop="shaker",
                setting="gym floor",
                gender="female",
                race="east_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE FORM CRITIQUE NPC",
                caption="Interrupts your heavy squat set to explain biomechanics learned from a 30-second YouTube Short.",
                character_archetype="npc",
                expression="neutral",
                prop="phone",
                setting="squat rack",
                gender="male",
                race="south_asian",
            ),
        ],
    },
    # 3. Every Generation In A Nutshell
    "generations": {
        "title": "Every Generation In A Nutshell",
        "setting": "living room",
        "segments": [
            ArchetypeSegment(
                archetype_title="THE BOOMER (1946-1964)",
                caption="Bought a 4-bedroom house in 1974 for $14,000 and two goats. Types text messages with one index finger.",
                character_archetype="boomer",
                expression="smug",
                prop="monster_can",
                setting="suburban lawn",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="GEN X (1965-1980)",
                caption="Entire world forgot they exist. Raised by television and hose water. Cynical, independent, unaffected.",
                character_archetype="doomer",
                expression="neutral",
                prop="coffee",
                setting="garage",
                gender="female",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE MILLENNIAL (1981-1996)",
                caption="Has a master's degree, $80k debt, and an emotional support water bottle. Dreams of owning one house plant.",
                character_archetype="doomer",
                expression="crying",
                prop="coffee",
                setting="rental apartment",
                gender="male",
                race="latino",
            ),
            ArchetypeSegment(
                archetype_title="GEN Z (1997-2012)",
                caption="Attention span is 4.2 seconds. Communicates via 6 layers of irony. Says 'no cap on god fr fr' in job interviews.",
                character_archetype="zoomer",
                expression="shocked",
                prop="phone",
                setting="bedroom LED",
                gender="female",
                race="east_asian",
            ),
            ArchetypeSegment(
                archetype_title="GEN ALPHA (2013+)",
                caption="Learned how to swipe an iPad before learning how to walk. Knows every Skibidi lore theory by heart.",
                character_archetype="soyjak",
                expression="shocked",
                prop="tablet",
                setting="couch with tablet",
                gender="male",
                race="south_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE ANCIENT GIGACHAD",
                caption="Lived through dial-up internet, VHS tapes, and 2008 crash without complaining once. Endures everything.",
                character_archetype="chad",
                expression="smug",
                prop="book",
                setting="mountain porch",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE TRAD HOMESTEADER",
                caption="Rejects the modern grindset. Bakes artisan sourdough, tends chickens, and logs off LinkedIn forever.",
                character_archetype="tradwife",
                expression="happy",
                prop="bread",
                setting="country kitchen",
                gender="tradwife",
                race="fair",
            ),
        ],
    },
    # 4. Types of Developers Be Like
    "coders": {
        "title": "Types of Developers Be Like",
        "setting": "tech office",
        "segments": [
            ArchetypeSegment(
                archetype_title="THE JUNIOR DEV",
                caption="Pushed an API key directly to a public GitHub repo. Is currently sweating through his hoodie.",
                character_archetype="doomer",
                expression="shocked",
                prop="keyboard",
                setting="desk monitors",
                gender="male",
                race="south_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE SENIOR ARCHITECT",
                caption="Deletes 8,000 lines of legacy spaghetti code in 10 minutes. Fixes production with one single semicolon.",
                character_archetype="chad",
                expression="smug",
                prop="coffee",
                setting="dark terminal",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE STACK OVERFLOW WARRIOR",
                caption="Has no idea how his code works. Copies a 9-year-old answer marked with 2 upvotes. It somehow compiles.",
                character_archetype="soyjak",
                expression="happy",
                prop="keyboard",
                setting="browser tabs",
                gender="male",
                race="latino",
            ),
            ArchetypeSegment(
                archetype_title="THE LINUX EVANGELIST",
                caption="Will spend 6 hours explaining why his tiling window manager is 4% faster than Windows 11.",
                character_archetype="doomer",
                expression="neutral",
                prop="keyboard",
                setting="custom desktop",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE AI PROMPT ENGINEER",
                caption="Types 'make it look modern' into ChatGPT. Claims on LinkedIn to be a Senior AI Solutions Executive.",
                character_archetype="zoomer",
                expression="smug",
                prop="phone",
                setting="coffee shop",
                gender="female",
                race="east_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE CSS CENTERING VICTIM",
                caption="Spent 5 consecutive hours trying to center a single div inside a flexbox container. Starts weeping.",
                character_archetype="doomer",
                expression="crying",
                prop="keyboard",
                setting="browser tabs",
                gender="female",
                race="black",
            ),
            ArchetypeSegment(
                archetype_title="THE CODE REVIEW PURIST",
                caption="Leaves 87 comments on your pull request arguing about tabs versus spaces and variable casing.",
                character_archetype="npc",
                expression="annoyed",
                prop="coffee",
                setting="tech office",
                gender="male",
                race="middle_eastern",
            ),
        ],
    },
    # 5. Types of Students During Exams Be Like
    "students": {
        "title": "Types of Students During Exams Be Like",
        "setting": "university library",
        "segments": [
            ArchetypeSegment(
                archetype_title="THE ALL-NIGHTER SURVIVOR",
                caption="Has consumed 4 energy drinks, 0 hours of sleep, and is currently reading Chapter 1 at 5:45 AM.",
                character_archetype="doomer",
                expression="tired",
                prop="coffee",
                setting="library desk",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE 'I DIDN' STUDY' LIAR",
                caption="Cries in hallway saying 'I definitely failed'. Gets 98% on test, asks professor for extra credit.",
                character_archetype="soyjak",
                expression="crying",
                prop="notebook",
                setting="exam hall",
                gender="female",
                race="east_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE WING-IT GIGACHAD",
                caption="Showed up without a pen, borrowed a pencil from stranger, finishes exam in 20 minutes, walks out.",
                character_archetype="chad",
                expression="smug",
                prop="pencil",
                setting="classroom",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE COLOR-CODED HIGHLIGHTER",
                caption="Has 38 pastel highlighters. Entire textbook is glowing neon yellow. Has retained zero information.",
                character_archetype="bloomer",
                expression="happy",
                prop="highlighter",
                setting="study desk",
                gender="female",
                race="latino",
            ),
            ArchetypeSegment(
                archetype_title="THE PURE GUESSWORK MASTER",
                caption="When in doubt, chooses option 'C' for 40 consecutive multiple choice questions. Prays for a passing grade.",
                character_archetype="npc",
                expression="neutral",
                prop="exam_paper",
                setting="desk",
                gender="male",
                race="black",
            ),
            ArchetypeSegment(
                archetype_title="THE SYLLABUS LAWYER",
                caption="Argues with professor for 45 minutes over a 0.5 point deduction on Question 3B.",
                character_archetype="soyjak",
                expression="annoyed",
                prop="notebook",
                setting="lecture hall",
                gender="male",
                race="south_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE CAFFEINE OVERDOSER",
                caption="Hands shaking violently on bubble sheet after drinking cold brew, matcha, and two Red Bulls.",
                character_archetype="doomer",
                expression="shocked",
                prop="coffee",
                setting="desk",
                gender="female",
                race="middle_eastern",
            ),
        ],
    },
    # 6. Types of Drivers On The Road Be Like
    "drivers": {
        "title": "Types of Drivers On The Road Be Like",
        "setting": "highway road",
        "segments": [
            ArchetypeSegment(
                archetype_title="THE LEFT-LANE CAMPER",
                caption="Cruises at exactly 45 mph in the fast lane with cruise control on. Ignores 12 cars flashing high beams behind.",
                character_archetype="npc",
                expression="deadpan",
                prop="steering_wheel",
                setting="highway",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE LIFTED TRUCK BULLY",
                caption="Rides 2 inches from your rear bumper with LED stadium headlights illuminating your entire skeleton.",
                character_archetype="soyjak",
                expression="annoyed",
                prop="monster_can",
                setting="rearview mirror",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE NO-BLINKER DRIFTER",
                caption="Changes 4 lanes without a turn signal while eating a burrito and watching TikTok on dashboard mount.",
                character_archetype="zoomer",
                expression="smug",
                prop="phone",
                setting="traffic lane",
                gender="female",
                race="latino",
            ),
            ArchetypeSegment(
                archetype_title="THE STUDENT BEHIND WHEEL",
                caption="White-knuckling steering wheel at 10 and 2. Scared to make a right turn on red with zero oncoming traffic.",
                character_archetype="doomer",
                expression="shocked",
                prop="steering_wheel",
                setting="intersection",
                gender="female",
                race="east_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE HIGHWAY GIGACHAD",
                caption="Maintains 3-second follow distance, merges smoothly with zipper pattern, waves thank-you hand gesture.",
                character_archetype="chad",
                expression="smug",
                prop="steering_wheel",
                setting="open road",
                gender="male",
                race="black",
            ),
            ArchetypeSegment(
                archetype_title="THE ROUNDABOUT VICTIM",
                caption="Enters circular junction, panics completely, stops dead in middle, yields to empty oncoming traffic.",
                character_archetype="doomer",
                expression="crying",
                prop="steering_wheel",
                setting="roundabout",
                gender="male",
                race="south_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE SPEED TRAP DETECTOR",
                caption="Slams on brakes down to 35 mph on open highway because someone parked a white sedan on the shoulder.",
                character_archetype="boomer",
                expression="shocked",
                prop="sunglasses",
                setting="highway shoulder",
                gender="male",
                race="middle_eastern",
            ),
        ],
    },
    # 7. Types of Music Listeners Be Like
    "music": {
        "title": "Types of Music Listeners Be Like",
        "setting": "record lounge",
        "segments": [
            ArchetypeSegment(
                archetype_title="THE POST-PUNK DOOMER",
                caption="Wanders cold rainy city streets at 2 AM with wired earphones listening exclusively to Soviet synthwave.",
                character_archetype="doomer",
                expression="tired",
                prop="headphones",
                setting="city street",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE VINYL PURIST",
                caption="Spent $3,500 on audiophile turntable setup to explain why warmth of analog static is superior to digital.",
                character_archetype="soyjak",
                expression="smug",
                prop="vinyl_record",
                setting="record store",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE SPOTIFY WRAPPED BRAGGER",
                caption="Listened to 140,000 minutes of music just to post Top 0.001% artist badge on Instagram story for 24 hours.",
                character_archetype="zoomer",
                expression="happy",
                prop="phone",
                setting="bedroom",
                gender="female",
                race="east_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE GYM PHONK DEMON",
                caption="Has 400 identical Brazilian bass tracks on repeat. Heart rate hits 185 BPM before touching the barbell.",
                character_archetype="chad",
                expression="smug",
                prop="headphones",
                setting="weight room",
                gender="male",
                race="black",
            ),
            ArchetypeSegment(
                archetype_title="THE LOFI STUDY BLOOMER",
                caption="Has had same 24/7 anime girl studying stream running in background since October 2019. Pure inner peace.",
                character_archetype="bloomer",
                expression="happy",
                prop="tea_cup",
                setting="cozy desk",
                gender="female",
                race="latino",
            ),
            ArchetypeSegment(
                archetype_title="THE AUX CORD DICTATOR",
                caption="Demands bluetooth connection in car, skips 14 songs after 10 seconds, plays 11-minute unreleased demo.",
                character_archetype="soyjak",
                expression="annoyed",
                prop="phone",
                setting="car interior",
                gender="male",
                race="south_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE GUITAR SOLO DAD",
                caption="Plays imaginary air guitar in grocery store aisle when 1984 hair metal track comes on loudspeaker.",
                character_archetype="boomer",
                expression="smug",
                prop="sunglasses",
                setting="supermarket",
                gender="male",
                race="middle_eastern",
            ),
        ],
    },
    # 8. Types of Dating Profiles Be Like
    "dating": {
        "title": "Types of Dating Profiles Be Like",
        "setting": "cocktail bar",
        "segments": [
            ArchetypeSegment(
                archetype_title="THE RIZZLESS TEXTER",
                caption="Sent 'hey wyd' at midnight. Waited 48 hours with no response. Sent single question mark at 3:15 AM.",
                character_archetype="doomer",
                expression="crying",
                prop="phone",
                setting="bedroom",
                gender="male",
                race="south_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE 100-POINT CHECKLIST",
                caption="Must be 6'5, tech founder, marathon runner. Cancels date because birth chart shows Mars in retrograde.",
                character_archetype="soyjak",
                expression="smug",
                prop="crystal",
                setting="upscale lounge",
                gender="female",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE FISH PHOTO HERO",
                caption="Profile picture is exclusively him holding a 4-pound bass on a lake in Wisconsin. Bio says 'work hard play hard'.",
                character_archetype="boomer",
                expression="smug",
                prop="fish",
                setting="lake dock",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE UNMATCH SPEEDRUNNER",
                caption="Matches with someone, exchanges two normal messages, panics over making conversation, unmatches forever.",
                character_archetype="doomer",
                expression="shocked",
                prop="phone",
                setting="sofa",
                gender="female",
                race="east_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE GYM MIRROR FLEX",
                caption="All 6 profile photos are identical mirror selfies in same gym tank top with face completely blocked by phone.",
                character_archetype="chad",
                expression="neutral",
                prop="phone",
                setting="gym locker",
                gender="male",
                race="latino",
            ),
            ArchetypeSegment(
                archetype_title="THE FOODIE TRAVELER",
                caption="Bio reads 'fluent in sarcasm, tacos, and passport stamps'. Has visited 3 airport duty-free shops.",
                character_archetype="bloomer",
                expression="happy",
                prop="passport",
                setting="airport cafe",
                gender="female",
                race="black",
            ),
            ArchetypeSegment(
                archetype_title="THE GHOST APOLOGIST",
                caption="Disappears for 7 weeks without explanation, returns with 'omg my phone died so crazy how are you'.",
                character_archetype="zoomer",
                expression="deadpan",
                prop="phone",
                setting="coffee shop",
                gender="female",
                race="middle_eastern",
            ),
        ],
    },
    # 9. Types of Office Workers Be Like
    "workers": {
        "title": "Types of Office Workers Be Like",
        "setting": "corporate office",
        "segments": [
            ArchetypeSegment(
                archetype_title="THE EMAIL ESSAYIST",
                caption="Writes a 600-word dissertation with bullet points and appendices to answer a yes-or-no question.",
                character_archetype="soyjak",
                expression="smug",
                prop="coffee",
                setting="cubicle",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE SILENT QUITTER",
                caption="Does exactly 100% of contract duties, not one email more. Bag is packed and zipped at 4:59:50 PM.",
                character_archetype="doomer",
                expression="neutral",
                prop="briefcase",
                setting="office door",
                gender="female",
                race="east_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE COFFEE MACHINE CAMPER",
                caption="Spends 45 minutes by office kitchen Keurig discussing weekend patio renovations with innocent passerby.",
                character_archetype="boomer",
                expression="happy",
                prop="coffee",
                setting="breakroom",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE MEETING GIGACHAD",
                caption="Declines recurring 2-hour status meeting with note 'could be an email'. Solves blocker in 30 seconds.",
                character_archetype="chad",
                expression="smug",
                prop="laptop",
                setting="boardroom",
                gender="male",
                race="black",
            ),
            ArchetypeSegment(
                archetype_title="THE CALENDAR TETRIS MASTER",
                caption="Colors entire 40-hour work week with fake 'focus time' and 'sync prep' blocks to avoid all meetings.",
                character_archetype="bloomer",
                expression="smug",
                prop="planner",
                setting="standing desk",
                gender="female",
                race="latino",
            ),
            ArchetypeSegment(
                archetype_title="THE MICROMANAGER NPC",
                caption="Sends Teams message 'quick ping' followed immediately by incoming audio call while you are typing.",
                character_archetype="npc",
                expression="annoyed",
                prop="phone",
                setting="executive suite",
                gender="male",
                race="south_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE FRIDAY HAPPY HOUR HYPER",
                caption="Begins announcing drinks at 11:30 AM on Thursday. Knows drink orders of 40 colleagues by memory.",
                character_archetype="zoomer",
                expression="happy",
                prop="beverage",
                setting="office lounge",
                gender="female",
                race="middle_eastern",
            ),
        ],
    },
    # 10. Types of Gamers Be Like
    "gamers": {
        "title": "Types of Gamers Be Like",
        "setting": "gaming room",
        "segments": [
            ArchetypeSegment(
                archetype_title="THE SWEATY TRYHARD",
                caption="Drinks 2 energy drinks, plays in 4:3 stretched resolution, punches drywall after losing a casual lobby.",
                character_archetype="zoomer",
                expression="shocked",
                prop="controller",
                setting="gaming setup",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE COZY RETRO BOOMER",
                caption="Only plays games released between 1994 and 2004. Explains why modern graphics ruined video game industry.",
                character_archetype="boomer",
                expression="smug",
                prop="cartridge",
                setting="crt tv",
                gender="male",
                race="fair",
            ),
            ArchetypeSegment(
                archetype_title="THE STEAM SALE HOARDER",
                caption="Owns 842 games in library. Has played exactly 6 of them. Spends 2 hours browsing before going to sleep.",
                character_archetype="soyjak",
                expression="happy",
                prop="mouse",
                setting="desk",
                gender="male",
                race="south_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE SOULS-LIKE GIGACHAD",
                caption="Beats final boss at Level 1 naked with a wooden club without taking damage. Refuses to elaborate.",
                character_archetype="chad",
                expression="smug",
                prop="controller",
                setting="arena",
                gender="male",
                race="black",
            ),
            ArchetypeSegment(
                archetype_title="THE LORE HISTORIAN",
                caption="Can explain 4,000 years of fictional kingdom history but forgot girlfriend's birthday last Tuesday.",
                character_archetype="doomer",
                expression="neutral",
                prop="book",
                setting="bookshelf",
                gender="male",
                race="latino",
            ),
            ArchetypeSegment(
                archetype_title="THE COZY FARMING BLOOMER",
                caption="Spends 400 peaceful hours watering virtual parsnips and talking to townspeople in Stardew Valley.",
                character_archetype="bloomer",
                expression="happy",
                prop="switch",
                setting="cozy bed",
                gender="female",
                race="east_asian",
            ),
            ArchetypeSegment(
                archetype_title="THE MICROTRANSACTION WHALE",
                caption="Spent $2,400 on randomized anime weapon skins. Character looks like a neon disco ball in game.",
                character_archetype="coomer",
                expression="shocked",
                prop="credit_card",
                setting="battle pass",
                gender="female",
                race="middle_eastern",
            ),
        ],
    },
}

# Production Bible Section 6 alias mapping and search keywords
ARCHETYPE_ALIASES: Final[dict[str, str]] = {
    # 1. introverts
    "introverts": "introverts",
    "types_of_introverts": "introverts",
    "introvert": "introverts",
    "types of introverts": "introverts",
    "types of introverts be like": "introverts",
    "introversion": "introverts",
    "shy": "introverts",
    "social introvert": "introverts",
    "recluse": "introverts",
    "loner": "introverts",
    "homebody": "introverts",
    # 2. gym
    "gym": "gym",
    "gym_goers": "gym",
    "types_of_gym_goers": "gym",
    "types_of_gym_members": "gym",
    "types of gym members be like": "gym",
    "types of gym goers be like": "gym",
    "types of gym goers": "gym",
    "types of gym members": "gym",
    "gym members": "gym",
    "gym goers": "gym",
    "fitness": "gym",
    "workout": "gym",
    "lifting": "gym",
    "bodybuilding": "gym",
    # 3. generations
    "generations": "generations",
    "every_generation": "generations",
    "every generation in a nutshell": "generations",
    "every generation be like": "generations",
    "every generation": "generations",
    "generation": "generations",
    "boomer": "generations",
    "boomers": "generations",
    "millennial": "generations",
    "millennials": "generations",
    "gen x": "generations",
    "gen z": "generations",
    "gen alpha": "generations",
    # 4. coders
    "coders": "coders",
    "types_of_coders": "coders",
    "developers": "coders",
    "types_of_developers": "coders",
    "programmers": "coders",
    "types_of_programmers": "coders",
    "types of developers be like": "coders",
    "types of programmers be like": "coders",
    "types of coders be like": "coders",
    "types of developers": "coders",
    "types of programmers": "coders",
    "types of coders": "coders",
    "coding": "coders",
    "programming": "coders",
    "software": "coders",
    "devs": "coders",
    "software engineers": "coders",
    "dev": "coders",
    # 5. students
    "students": "students",
    "types_of_students": "students",
    "types of students during exams be like": "students",
    "types of students during exams": "students",
    "types of students be like": "students",
    "types of students": "students",
    "exams": "students",
    "exam": "students",
    "studying": "students",
    "student": "students",
    "university": "students",
    "college": "students",
    # 6. drivers
    "drivers": "drivers",
    "types_of_drivers": "drivers",
    "types of drivers on the road be like": "drivers",
    "types of drivers on the road": "drivers",
    "types of drivers be like": "drivers",
    "types of drivers": "drivers",
    "driving": "drivers",
    "road": "drivers",
    "traffic": "drivers",
    "highway": "drivers",
    "driver": "drivers",
    "cars": "drivers",
    "car": "drivers",
    # 7. music
    "music": "music",
    "types_of_music_listeners": "music",
    "types of music listeners be like": "music",
    "types of music listeners": "music",
    "music listeners": "music",
    "music listener": "music",
    "spotify": "music",
    "songs": "music",
    "playlist": "music",
    "audiophile": "music",
    # 8. dating
    "dating": "dating",
    "types_of_dating_profiles": "dating",
    "types of dating profiles be like": "dating",
    "types of dating profiles": "dating",
    "dating profiles": "dating",
    "dating profile": "dating",
    "tinder": "dating",
    "hinge": "dating",
    "bumble": "dating",
    "relationships": "dating",
    "romance": "dating",
    "dating app": "dating",
    # 9. workers
    "workers": "workers",
    "types_of_workers": "workers",
    "types_of_office_workers": "workers",
    "types of office workers be like": "workers",
    "types of office workers": "workers",
    "types of workers be like": "workers",
    "office": "workers",
    "office workers": "workers",
    "corporate": "workers",
    "workplace": "workers",
    "job": "workers",
    "career": "workers",
    "coworkers": "workers",
    # 10. gamers
    "gamers": "gamers",
    "types_of_gamers": "gamers",
    "types of gamers be like": "gamers",
    "types of gamers": "gamers",
    "gaming": "gamers",
    "video games": "gamers",
    "video game": "gamers",
    "gamer": "gamers",
    "steam": "gamers",
    "esports": "gamers",
}


def _normalize_query(query: str) -> str:
    """Normalize query text for robust matching."""
    q = query.strip().lower()
    # Replace separators with single space
    q = re.sub(r"[_\-]+", " ", q)
    # Remove punctuation
    q = re.sub(r"[^\w\s]", "", q)
    # Collapse multiple whitespaces
    return " ".join(q.split())


def resolve_archetype_topic(topic_query: str) -> dict[str, Any] | None:
    """Resolve a user topic string to a matching curated Wojak Explainer topic.

    Supports:
    1. Canonical topic keys (e.g. 'introverts', 'gym', 'generations', 'coders').
    2. Exact and partial title matching (e.g. 'Types of Drivers On The Road Be Like').
    3. Production Bible format names (e.g. 'types_of_introverts', 'every_generation').
    4. Keyword aliases (e.g. 'developers', 'exam', 'spotify', 'tinder', 'office').
    5. Substring & token overlap.
    6. Fuzzy similarity matching with difflib fallback.
    """
    raw_query = topic_query.strip().lower()
    if not raw_query:
        return None

    # 1. Direct key match in ARCHETYPE_TOPICS
    if raw_query in ARCHETYPE_TOPICS:
        return ARCHETYPE_TOPICS[raw_query]

    # 2. Direct key match in ARCHETYPE_ALIASES
    if raw_query in ARCHETYPE_ALIASES:
        canonical_key = ARCHETYPE_ALIASES[raw_query]
        return ARCHETYPE_TOPICS.get(canonical_key)

    # 3. Check normalized query against aliases
    norm_q = _normalize_query(raw_query)
    norm_under = norm_q.replace(" ", "_")

    if norm_under in ARCHETYPE_TOPICS:
        return ARCHETYPE_TOPICS[norm_under]
    if norm_q in ARCHETYPE_ALIASES:
        return ARCHETYPE_TOPICS[ARCHETYPE_ALIASES[norm_q]]
    if norm_under in ARCHETYPE_ALIASES:
        return ARCHETYPE_TOPICS[ARCHETYPE_ALIASES[norm_under]]

    # 4. Check against topic titles
    for data in ARCHETYPE_TOPICS.values():
        title_norm = _normalize_query(data["title"])
        if norm_q == title_norm or norm_q in title_norm or title_norm in norm_q:
            return data

    # 5. Check substring containment (query contains the alias phrase)
    for alias, canon in sorted(ARCHETYPE_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        alias_norm = _normalize_query(alias)
        if len(alias_norm) >= 3 and alias_norm in norm_q:
            return ARCHETYPE_TOPICS[canon]

    # 6. Check token subset matching for multi-word queries
    tokens = set(norm_q.split())
    if len(tokens) >= 2:
        for alias, canon in ARCHETYPE_ALIASES.items():
            alias_tokens = set(_normalize_query(alias).split())
            if tokens.issubset(alias_tokens) or (
                len(alias_tokens) >= 2 and alias_tokens.issubset(tokens)
            ):
                return ARCHETYPE_TOPICS[canon]

    # 7. Fuzzy match via difflib on alias list with strict cutoff to avoid false positives
    all_alias_keys = list(ARCHETYPE_ALIASES.keys())
    matches = difflib.get_close_matches(norm_q, all_alias_keys, n=1, cutoff=0.78)
    if matches:
        return ARCHETYPE_TOPICS[ARCHETYPE_ALIASES[matches[0]]]

    return None


def list_archetype_topics() -> list[str]:
    """Return all 10 canonical topic identifiers."""
    return list(ARCHETYPE_TOPICS.keys())


def get_archetype_segments(topic: str) -> list[ArchetypeSegment]:
    """Return the curated archetype segments for a given topic."""
    data = resolve_archetype_topic(topic)
    if not data:
        raise KeyError(f"Unknown archetype topic: {topic!r}")
    return list(data["segments"])
