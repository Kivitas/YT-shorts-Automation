"""Script generation engine for Country Meme and Types Of... meme videos.

Supports both dynamic LLM-driven generation (via AutoShorts LLM provider)
and a robust, hilarious, deterministic procedural fallback engine.
"""

from __future__ import annotations

import json
import logging
import random
import re
from typing import Any

from autoshorts.core.providers import get_provider
from autoshorts.meme.topics import (
    resolve_topic_and_category,
    select_hashtags,
)
from autoshorts.meme.types import MemeConfig, MemeScript, MemeSegment

logger = logging.getLogger("autoshorts.meme.script")

# Visual & Audio keyword taxonomies
POSES = [
    "sleeping_desk",
    "surrounded_books",
    "gaming_intense",
    "hiding_corner",
    "dramatic_heroic",
    "smug_crossed_arms",
    "shocked_facepalm",
    "screaming_run",
    "peace_sign",
    "facepalm",
    "finger_guns",
    "bowing_polite",
    "standing_stoic",
    "running_panic",
    "dramatic_pointing",
]

EXPRESSIONS = [
    "shocked",
    "smug",
    "deadpan",
    "panicked",
    "screaming",
    "sleeping",
    "crying_laughing",
    "sparkling_eyes",
    "intense",
    "mischievous",
    "proud",
]

BACKGROUNDS = [
    "train_station",
    "classroom",
    "kitchen",
    "gaming_room",
    "street_city",
    "snowy_winter",
    "supermarket",
    "airport",
    "office",
    "gym",
    "zombie_wasteland",
    "bedroom",
    "library",
]

ANIMATION_TYPES = [
    "zoom_in",
    "zoom_out",
    "shake",
    "pan_left",
    "pan_right",
    "parallax",
    "impact_pop",
]

MUSIC_MOODS = [
    "playful",
    "quirky",
    "epic_orchestral",
    "suspense",
    "punchline_cut",
    "unexpected_reveal",
    "lofi_chill",
]

SFX_CUES = [
    "record_scratch",
    "bass_drop",
    "cartoon_impact",
    "whoosh",
    "rimshot",
    "stinger",
    "none",
]


def match_audio_and_sfx(
    scenario: str,
    expression: str,
    pose: str,
    is_punchline: bool = False,
    is_reveal: bool = False,
) -> tuple[str, str, str]:
    """Determine music_mood, sfx_cue, and animation_type based on comedic scenario cues.

    Rules:
    - Normal/funny scene -> playful upbeat music
    - Awkward scene -> quirky comedic music
    - Overconfident character -> epic orchestral music
    - Disaster scene -> dramatic suspense music
    - Punchline -> sudden music cut + comedic SFX
    - Unexpected reveal -> bass hit + rapid zoom

    Args:
        scenario: Description of the situation.
        expression: Character facial expression keyword.
        pose: Character pose keyword.
        is_punchline: Whether this segment serves as the concluding punchline.
        is_reveal: Whether this segment introduces an unexpected dramatic twist.

    Returns:
        Tuple of (music_mood, sfx_cue, animation_type).
    """
    text = (scenario + " " + expression + " " + pose).lower()

    if is_punchline or "punchline" in text or "catastrophe" in text:
        return "punchline_cut", "rimshot", "impact_pop"

    if is_reveal or "secret" in text or "suddenly" in text or "plot twist" in text:
        return "unexpected_reveal", "bass_drop", "zoom_in"

    if any(
        k in text
        for k in ("epic", "heroic", "smug", "chad", "sigma", "billionaire", "genius", "master")
    ):
        return "epic_orchestral", "stinger", "parallax"

    if any(
        k in text
        for k in (
            "disaster",
            "screaming",
            "panic",
            "fire",
            "zombie",
            "explosion",
            "apocalypse",
            "run",
        )
    ):
        return "suspense", "whoosh", "shake"

    if any(
        k in text
        for k in ("awkward", "deadpan", "confused", "cringe", "silent", "staring", "ghost")
    ):
        return "quirky", "record_scratch", "zoom_out"

    if any(k in text for k in ("sleep", "chill", "nap", "pillow", "bed", "relax")):
        return "lofi_chill", "none", "pan_right"

    # Default funny upbeat scene
    return "playful", "cartoon_impact", "zoom_in"


def build_description(script: MemeScript) -> str:
    """Build high-retention YouTube Shorts or TikTok description text.

    Args:
        script: Generated MemeScript instance.

    Returns:
        Formatted multi-line description text with chapters, hooks, and hashtags.
    """
    segment_lines = [
        f"⏱️ {i + 1}. {seg.heading} - {seg.name}" for i, seg in enumerate(script.segments)
    ]
    segments_summary = "\n".join(segment_lines)
    tags_string = " ".join(script.hashtags)

    ending_prompt = script.ending

    description = f"""{script.title}

{script.hook}
{script.intro}

🔥 Timestamps / Featured:
{segments_summary}

💬 {ending_prompt}

{tags_string}
#Anime #Animation #Viral #Shorts
Created with AutoShorts Meme Engine
"""
    return description.strip()


# ============================================================================
# Curated Country & Archetype Knowledge Bases
# ============================================================================

COUNTRY_ARCHETYPES: list[dict[str, Any]] = [
    {
        "id": "japan",
        "name": "Japan",
        "heading": "JAPAN 🇯🇵",
        "character": "Anime salaryman in crisp suit with spectacles and glowing digital pocket watch",
        "pose": "bowing_polite",
        "expression": "shocked",
        "background": "train_station",
        "scenarios": {
            "public_transport": (
                "Train arrives 12 seconds early, conductor immediately issues handwritten apology certificates to all passengers.",
                "Gomen nasai! We arrived 12 seconds ahead of schedule! Please forgive our reckless haste!",
                "In Japan, the bullet train arrived twelve seconds early, forcing the conductor into an immediate twenty-minute televised apology.",
            ),
            "driving": (
                "Smoothly parallel parks a kei-car into an impossible 2-millimeter gap while repeatedly bowing to crossing pedestrians.",
                "Precision parking complete with millimeter clearance. Arigato!",
                "In Japan, traffic is so disciplined that drivers parallel park into shoeboxes while bowing politely through the windshield.",
            ),
            "school": (
                "Classroom cleaning duty carried out like a synchronized military tactical maneuver before 5 hours of after-school cramming.",
                "Floor sanitized to mirror finish. Now begins stage two: 700 algebra problems!",
                "In Japan, school life means synchronizing desk cleaning with military precision before diving into 14 hours of homework.",
            ),
            "winter": (
                "Equipped with heated Kotatsu table and five layers of high-tech thermal gear, refusing to step outside until April.",
                "Kotatsu activated! Leaving this heated sanctuary is strictly prohibited by anime law!",
                "In Japan, winter means turning the living room into a heated Kotatsu bunker and surviving exclusively on ramen and determination.",
            ),
            "default": (
                "Turns an everyday mundane chore into a breathtaking, perfectly synchronized anime training arc.",
                "Efficiency rating: 99.99%! Perfection is the only acceptable baseline!",
                "In Japan, even the simplest daily routine is executed with ninety-nine point nine percent robotic perfection.",
            ),
        },
    },
    {
        "id": "germany",
        "name": "Germany",
        "heading": "GERMANY 🇩🇪",
        "character": "Stoic anime engineer wearing safety glasses, holding a clipboard and laser measuring tool",
        "pose": "standing_stoic",
        "expression": "deadpan",
        "background": "office",
        "scenarios": {
            "public_transport": (
                "Waiting at a deserted crosswalk at 3:15 AM with zero cars in sight because the pedestrian signal is still red.",
                "Ordnung muss sein! The light is red. We wait until precisely 03:16:00.",
                "In Germany, citizens will wait at a red pedestrian light at three in the morning with not a single vehicle within forty miles.",
            ),
            "driving": (
                "Merging onto the Autobahn in a diesel station wagon and immediately cruising at Mach 2 in total silence.",
                "Autobahn speed limit is purely a theoretical suggestion. Left lane cleared.",
                "In Germany, entering the Autobahn means unlocking warp speed while strictly monitoring your tire pressure gauges.",
            ),
            "winter": (
                "Dons high-visibility Jack Wolfskin all-weather jacket and goes on a scheduled 15km mountain hike through a blizzard.",
                "There is no bad weather, there is only inappropriate outdoor apparel.",
                "In Germany, a blizzard is just an excuse to calibrate your three-thousand-dollar Gore-Tex hiking jacket.",
            ),
            "default": (
                "Refuses to proceed with any action until 42 pages of certified DIN standard compliance forms are properly stamped.",
                "Protocol verified. Authorization stamp 7-B approved. Proceed according to regulation.",
                "In Germany, everything must comply with industrial standards, down to the exact millimeter of bread crust thickness.",
            ),
        },
    },
    {
        "id": "usa",
        "name": "United States",
        "heading": "USA 🇺🇸",
        "character": "Boisterous anime hero wearing aviator sunglasses, eagle leather jacket, holding an oversized soda",
        "pose": "dramatic_heroic",
        "expression": "smug",
        "background": "supermarket",
        "scenarios": {
            "public_transport": (
                "Looks in confusion at public transit map before stepping into a lifted V8 monster truck with 12 cupholders.",
                "What in the bald eagle is a subway? If it ain't got eight cylinders, I ain't riding!",
                "In the United States, public transportation is an alien myth replaced by four-ton pickup trucks equipped with mega drink dispensers.",
            ),
            "food": (
                "Deep-frying a glazed donut stuffed with bacon and cheese, served with a 128-ounce iced sweet tea.",
                "If it can't be deep-fried on a stick at the county fair, does it even count as nutrition?!",
                "In America, dinner isn't finished until every carbohydrate is battered, deep-fried, and crowned with extra cheese.",
            ),
            "supermarket": (
                "Navigating wholesale warehouse aisles riding an electric shopping cart with a 40-pack of peanut butter tubs.",
                "I came here for bread and somehow bought a 50-gallon drum of barbecue sauce and a trampoline!",
                "In the US, grocery shopping means buying enough industrial-sized cereal boxes to survive a three-year nuclear winter.",
            ),
            "default": (
                "Solving every minor inconvenience with overwhelming enthusiasm, excessive horsepower, and fireworks.",
                "Freedom and maximum horsepower solves everything! Let's go!",
                "In the US, subtle moderation doesn't exist; you either go ten times bigger than necessary or you don't show up at all.",
            ),
        },
    },
    {
        "id": "italy",
        "name": "Italy",
        "heading": "ITALY 🇮🇹",
        "character": "Dramatically theatrical anime chef gesticulating with both hands, wearing a pristine apron",
        "pose": "shocked_facepalm",
        "expression": "screaming",
        "background": "kitchen",
        "scenarios": {
            "food": (
                "Watches a tourist snap spaghetti noodles in half before putting them into cold tap water, falling to knees in agony.",
                "MAMMA MIA! You broke the pasta! This is a culinary declaration of war against my ancestors!",
                "In Italy, breaking dry spaghetti in half is legally classified as an international war crime punishable by banishment.",
            ),
            "driving": (
                "Squeezing a vintage scooter between two city buses at full throttle, guiding traffic purely with intense hand gestures.",
                "Mirrors are optional, hand gestures are eternal! Che fai?!",
                "In Italy, road traffic operates not by traffic lights, but by the expressive intensity of rapid-fire hand gestures.",
            ),
            "parents": (
                "Grandmother prepares an eight-course banquet because you dared to mention you were slightly hungry 10 minutes ago.",
                "Mangia! You look so skinny, the wind could blow you all the way to Sicily!",
                "In Italy, tell nonna you're hungry and she will cook enough homemade pasta to feed an entire Roman legion.",
            ),
            "default": (
                "Turning any mild everyday interaction into a three-act operatic emotional drama with exquisite espresso.",
                "Life without passion and proper espresso is not life at all!",
                "In Italy, every mundane event is treated with the emotional drama and grand passion of a Renaissance opera.",
            ),
        },
    },
    {
        "id": "uk",
        "name": "United Kingdom",
        "heading": "UK 🇬🇧",
        "character": "Polite anime gentleman holding an umbrella in torrential rain, clutching a steaming cup of tea",
        "pose": "bowing_polite",
        "expression": "deadpan",
        "background": "street_city",
        "scenarios": {
            "public_transport": (
                "Someone steps forcefully on his foot; he immediately turns around and apologizes profusely for being in their way.",
                "Terribly sorry! My foot was completely in the way of your boot! Quite my fault!",
                "In Britain, if someone steps on your foot on the tube, you are required by law to apologize for standing in their way.",
            ),
            "winter": (
                "Three snowflakes fall from the sky; the entire national transportation infrastructure declares immediate total collapse.",
                "A flake has touched the rail! Shut down everything and put the kettle on, lads!",
                "In the UK, two snowflakes on a railway track instantly trigger a nationwide logistical apocalypse.",
            ),
            "driving": (
                "Politely waving through 18 consecutive cars at a roundabout until the petrol tank runs completely dry.",
                "After you... no please, after you! I insist! We shall starve here politely together!",
                "In Britain, drivers will starve to death at a roundabout politely gesturing for the other person to go first.",
            ),
            "default": (
                "Facing down impending disaster by taking a quiet sip of tea and remarking that it's a bit breezy today.",
                "Bit chilly outside today, mustn't grumble. Tea will sort it right out.",
                "In the UK, no catastrophe is so grand that a fresh cup of tea and aggressive politeness cannot overcome it.",
            ),
        },
    },
    {
        "id": "india",
        "name": "India",
        "heading": "INDIA 🇮🇳",
        "character": "High-energy anime tech genius spinning a screwdriver with extreme swagger and flair",
        "pose": "finger_guns",
        "expression": "sparkling_eyes",
        "background": "street_city",
        "scenarios": {
            "public_transport": (
                "Effortlessly boards a moving train with one finger, shares hot samosas with 40 strangers, and solves calculus.",
                "Adjust a little bit brother, there is always room for twenty more champions here!",
                "In India, public transit trains defy all known laws of physics with legendary boarding agility and infinite seat sharing.",
            ),
            "driving": (
                "Turning a 2-lane road into an 8-lane expressway using 100% horn intuition and zero fear of mortality.",
                "Horn OK Please! Traffic rules are guidelines, intuition is supreme!",
                "In India, drivers use their vehicle horns with the tactical complexity of a dolphin using echolocation.",
            ),
            "internet": (
                "Setting up high-speed 5G in the middle of a remote Himalayan village to teach someone calculus on YouTube.",
                "Server fixed in 30 seconds using pure jugaad and high-speed fiber! No problem, sir!",
                "In India, tech support isn't a department; it's an innate superpower shared by millions of passionate innovators.",
            ),
            "default": (
                "Solving impossible structural engineering challenges in 3 minutes using pure Jugaad innovation and duct tape.",
                "Jugaad engineering activated! Why buy a new machine when two clips and a rubber band work better?",
                "In India, any problem can be solved in three minutes using sheer creativity, boundless optimism, and clever engineering.",
            ),
        },
    },
    {
        "id": "france",
        "name": "France",
        "heading": "FRANCE 🇫🇷",
        "character": "Chic anime philosopher wearing a stylish scarf, holding a warm baguette under arm",
        "pose": "smug_crossed_arms",
        "expression": "smug",
        "background": "street_city",
        "scenarios": {
            "public_transport": (
                "Train cancelled indefinitely because the railway union called an impromptu strike to protest the concept of Mondays.",
                "C'est la vie! Today we do not work, today we defend the sacred art of relaxation!",
                "In France, public transit workers will stage a passionate nationwide strike simply because Tuesday arrived too quickly.",
            ),
            "food": (
                "Engages in an intense 45-minute monologue dissecting the crust sound of a baguette before taking one bite.",
                "Mon dieu! If the crust does not sing with that precise crunch, it is but mere bread, not art!",
                "In France, eating lunch isn't a quick break; it is a sacred philosophical symposium lasting three glorious hours.",
            ),
            "default": (
                "Refuses to acknowledge a tourist's question until they greet with a flawless Parisian 'Bonjour'.",
                "Did I hear a greeting? No? Then alas, you do not yet exist in this café, my friend.",
                "In France, you can speak six languages fluently, but without a proper 'Bonjour' you remain completely invisible.",
            ),
        },
    },
    {
        "id": "australia",
        "name": "Australia",
        "heading": "AUSTRALIA 🇦🇺",
        "character": "Fearless anime adventurer with slouch hat and thongs, holding an impossibly large spider like a pet",
        "pose": "peace_sign",
        "expression": "crying_laughing",
        "background": "street_city",
        "scenarios": {
            "survival": (
                "Casually picks up a dinner-plate sized huntsman spider off the ceiling and walks it outside like a golden retriever.",
                "G'day mate! Don't mind Barry on the roof, he just catches the mozzies for us!",
                "In Australia, finding a venomous creature the size of a dinner plate in your kitchen is considered pleasant morning company.",
            ),
            "driving": (
                "Waiting patiently while an angry kangaroo and a cassowary have an MMA title match on the two-lane highway.",
                "Just having a little dust-up in the middle lane, no worries mate, we'll wait it out!",
                "In Australia, traffic jams aren't caused by car accidents, but by kangaroos challenging passing motorists to boxing matches.",
            ),
            "default": (
                "Wanders through extreme scorching heat in flip-flops with a chilled beverage, unfazed by deadly wildlife.",
                "Too easy mate! If it doesn't bite, it ain't Australian!",
                "In Australia, danger is just everyday background noise while you kick back in flip-flops with your mates.",
            ),
        },
    },
    {
        "id": "canada",
        "name": "Canada",
        "heading": "CANADA 🇨🇦",
        "character": "Cheerful flannel-clad anime hero with snow boots, pouring maple syrup straight onto snow",
        "pose": "peace_sign",
        "expression": "proud",
        "background": "snowy_winter",
        "scenarios": {
            "winter": (
                "Wearing cargo shorts and a t-shirt in minus 25 degrees while walking to Tim Hortons for an iced cappuccino.",
                "Bit of a gentle breeze out here, eh? Perfect weather for an iced coffee!",
                "In Canada, negative twenty degrees is celebrated as official shorts weather as long as there is an iced drink in hand.",
            ),
            "public_transport": (
                "Bumps into an inanimate snowplow on the sidewalk and reflexively apologizes to it three times.",
                "Oh, so sorry about that! Didn't mean to crowd your lane there, big machine!",
                "In Canada, politeness is so instinctive that people will genuinely apologize to parking meters after running into them.",
            ),
            "default": (
                "Resolves any neighborhood conflict instantly by offering fresh maple cookies and hockey tickets.",
                "No hard feelings, eh? Let's go watch the hockey game and drink some syrup!",
                "In Canada, aggressive confrontation does not exist; every disagreement is peacefully defused with maple syrup and hockey.",
            ),
        },
    },
    {
        "id": "south_korea",
        "name": "South Korea",
        "heading": "SOUTH KOREA 🇰🇷",
        "character": "Ultra-aesthetic anime idol with radiant glass skin and glowing iced americano",
        "pose": "smug_crossed_arms",
        "expression": "sparkling_eyes",
        "background": "classroom",
        "scenarios": {
            "school": (
                "Finishes 8 hours of school, attends 6 hours of hagwon cram academy, then studies at a 24-hour study café until dawn.",
                "Sleeping is a luxury reserved for after graduation! Give me more iced americano!",
                "In South Korea, studying twenty-four hours a day fueled exclusively by iced americano is considered a normal Tuesday.",
            ),
            "winter": (
                "Freezing minus fifteen windchill outside, but still firmly clutching a giant plastic cup of iced americano.",
                "Eoljukah! Even if I freeze to death, iced americano is mandatory!",
                "In Korea, winter weather can reach arctic temperatures, but giving up iced coffee is considered total spiritual defeat.",
            ),
            "default": (
                "Performing a 15-step skincare routine while simultaneously climbing to rank 1 in an online competitive battle royale.",
                "Glass skin maintenance and esports dominance: both must be maintained at maximum rank!",
                "In South Korea, supreme aesthetic skincare and hyper-competitive gaming mastery are pursued with Olympian devotion.",
            ),
        },
    },
]

TYPES_OF_ARCHETYPES: dict[str, list[dict[str, Any]]] = {
    "school": [
        {
            "id": "procrastinator",
            "name": "The Procrastinator",
            "heading": "THE PROCRASTINATOR ⏰",
            "character": "Panicked anime student surrounded by towering piles of textbooks with 2% phone battery",
            "pose": "screaming_run",
            "expression": "panicked",
            "background": "classroom",
            "narration": "Due tomorrow? Do tomorrow! Starts the 20-page research paper at 3:47 AM fueled entirely by panic and iced tea.",
            "speech_bubble": "If I type at 400 words per minute, I can still get seven minutes of sleep before homeroom!",
            "scenario": "Typing a term paper with one minute remaining on the clock while running down the school hallway.",
        },
        {
            "id": "overachiever",
            "name": "The Overachiever",
            "heading": "THE OVERACHIEVER 📝",
            "character": "Prim anime student wearing three medals, holding an exam paper with 99% in tears",
            "pose": "shocked_facepalm",
            "expression": "crying_laughing",
            "background": "classroom",
            "narration": "Cries inconsolably after getting a ninety-nine percent, demanding to know where that single half-point went.",
            "speech_bubble": "A ninety-nine?! My academic legacy is utterly ruined forever!",
            "scenario": "Interrogating the teacher with a magnifying glass over half a mark on question four.",
        },
        {
            "id": "snack_smuggler",
            "name": "The Snack Smuggler",
            "heading": "THE SNACK SMUGGLER 🍕",
            "character": "Mischievous anime student concealing a full barbecue buffet inside an oversized hoodie",
            "pose": "hiding_corner",
            "expression": "mischievous",
            "background": "classroom",
            "narration": "Somehow opens a noisy bag of potato chips in a dead silent exam hall without making a single sound.",
            "speech_bubble": "Be very quiet... this pepperoni pizza is essential for my brain power.",
            "scenario": "Discreetly assembling a gourmet sandwich behind a chemistry textbook while the teacher turns around.",
        },
        {
            "id": "sleeping_giant",
            "name": "The Sleeping Giant",
            "heading": "THE SLEEPING GIANT 💤",
            "character": "Anime student asleep face down on a desk with a bubble coming from their nose",
            "pose": "sleeping_desk",
            "expression": "sleeping",
            "background": "classroom",
            "narration": "Has slept through every single lecture since September, gets called on randomly, and answers with Nobel Prize insight.",
            "speech_bubble": "Zzz... the answer to question seven is photosynthesis, teacher... zzz...",
            "scenario": "Wakes up for exactly three seconds to solve an impossible calculus equation, then resumes napping.",
        },
        {
            "id": "pen_collector",
            "name": "The Stationery Addict",
            "heading": "THE PEN COLLECTOR 🖊️",
            "character": "Enthusiastic anime student with 72 pastel highlighters organized by hexadecimal color code",
            "pose": "smug_crossed_arms",
            "expression": "sparkling_eyes",
            "background": "classroom",
            "narration": "Spends forty-five minutes crafting the title in glowing calligraphy, runs out of time to take actual notes.",
            "speech_bubble": "My pastel aesthetic notes are flawless! Content? We will figure that out next term!",
            "scenario": "Creating an illuminated manuscript title while the teacher writes four blackboard chapters of equations.",
        },
        {
            "id": "hallway_ninja",
            "name": "The Hallway Sprinter",
            "heading": "THE HALLWAY SPRINTER 🏃",
            "character": "Anime kid sprinting with arms behind back Naruto-style between lockers",
            "pose": "screaming_run",
            "expression": "intense",
            "background": "classroom",
            "narration": "Treats the two-minute passing period like the final stage of the Olympic four-hundred-meter dash.",
            "speech_bubble": "Make way! Class starts in ninety seconds and I must secure the prime back row seat!",
            "scenario": "Drifting around locker corners at supersonic speed to reach the chemistry lab before the bell rings.",
        },
    ],
    "gamers": [
        {
            "id": "the_rager",
            "name": "The Rager",
            "heading": "THE RAGER 🤬",
            "character": "Furious anime gamer surrounded by smoke and broken mechanical keycaps",
            "pose": "screaming_run",
            "expression": "screaming",
            "background": "gaming_room",
            "narration": "Dies once to a tutorial bot and immediately writes a 4,000-word essay about enemy hitboxes.",
            "speech_bubble": "HE'S LITERALLY ONE SHOT! MY MOUSE DISCONNECTED! THIS GAME IS BROKEN!",
            "scenario": "Screaming at the monitor as a harmless tutorial slime defeats their fully geared avatar.",
        },
        {
            "id": "the_hoarder",
            "name": "The Item Hoarder",
            "heading": "THE HOARDER 🎒",
            "character": "Anime adventurer with a backpack bursting with 999 elixirs and potions",
            "pose": "surrounded_books",
            "expression": "panicked",
            "background": "gaming_room",
            "narration": "Fights the final boss on 1 HP because using a single mega-elixir would be 'wasting it for later'.",
            "speech_bubble": "I cannot use this potion now! What if there is a secret second final boss after this?!",
            "scenario": "Refusing to consume a recovery potion while their character flashes red on the brink of defeat.",
        },
        {
            "id": "the_fashionista",
            "name": "The Fashionista",
            "heading": "THE FASHIONISTA 👗",
            "character": "Anime gamer wearing extravagant glowing angel wings with negative 50 defense",
            "pose": "peace_sign",
            "expression": "sparkling_eyes",
            "background": "gaming_room",
            "narration": "Stats are minus eighty, defense is zero, but the glowing pink dragon cape looks completely legendary.",
            "speech_bubble": "Damage numbers are temporary, but matching server drip is eternal!",
            "scenario": "Walking into a high-level raid wearing decorative cosmetic bunny ears and a sparkling kimono.",
        },
        {
            "id": "the_afk",
            "name": "The AFK Champion",
            "heading": "THE AFK CHAMPION 🥪",
            "character": "Anime character motionless in spawn with a sandwich floating beside them",
            "pose": "standing_stoic",
            "expression": "deadpan",
            "background": "gaming_room",
            "narration": "Waits 40 minutes in ranked matchmaking queue, only to leave to make a gourmet grilled cheese the exact second it starts.",
            "speech_bubble": "Brb guys, microwave just pinged! Protect my character for twelve minutes!",
            "scenario": "Standing completely frozen in the crosshairs of five enemy snipers while making a sandwich.",
        },
        {
            "id": "the_tryhard",
            "name": "The Sweaty Tryhard",
            "heading": "THE SWEATY TRYHARD ⚡",
            "character": "Sweating anime player with forehead sweatband, glowing RGB headset, drinking energy drink via IV",
            "pose": "gaming_intense",
            "expression": "intense",
            "background": "gaming_room",
            "narration": "Plays casual party games with the tactical focus and blood pressure of a fighter pilot in combat.",
            "speech_bubble": "Rotate! Rotate to zone B! Why is nobody calling out the mini-game flower spawns?!",
            "scenario": "Applying advanced esports tactical communication to an online game of Mario Party.",
        },
    ],
    "relatable": [
        {
            "id": "the_overthinker",
            "name": "The Overthinker",
            "heading": "THE OVERTHINKER 🧠",
            "character": "Wide-eyed anime character in bed staring at the ceiling as clocks spin wildly",
            "pose": "hiding_corner",
            "expression": "panicked",
            "background": "bedroom",
            "narration": "Lying awake at 3:14 AM analyzing why the cashier said 'enjoy your meal' and they replied 'you too'.",
            "speech_bubble": "Does the cashier think I wanted them to eat my burger?! I must relocate to another continent!",
            "scenario": "Staring into the cosmic void of embarrassment over a tiny conversation from three years ago.",
        },
        {
            "id": "alarm_multiplier",
            "name": "The Alarm Multiplier",
            "heading": "THE ALARM MULTIPLIER ⏰",
            "character": "Anime sleeper wrapped in blankets with a phone screen showing 35 active alarms",
            "pose": "sleeping_desk",
            "expression": "sleeping",
            "background": "bedroom",
            "narration": "Sets alarms for 7:00, 7:03, 7:07, 7:12, and 7:15, then unconsciously swipes dismiss on all thirty of them.",
            "speech_bubble": "The 7:04 alarm was just a drill. The 7:11 alarm is the real boss... Zzz...",
            "scenario": "Waking up peacefully at 11:45 AM after silencing thirty consecutive military-grade alarm sirens.",
        },
        {
            "id": "the_hype_friend",
            "name": "The Hype Friend",
            "heading": "THE HYPE FRIEND 🎉",
            "character": "Super energetic anime buddy cheering with pom-poms and confetti cannons",
            "pose": "dramatic_heroic",
            "expression": "sparkling_eyes",
            "background": "street_city",
            "narration": "Celebrates you successfully opening a water bottle like you just discovered the cure for gravity.",
            "speech_bubble": "LET'S GOOOOO! Look at that hydration technique! Absolute peak performance!",
            "scenario": "Launching fireworks in the middle of a café because their friend parallel parked on the first try.",
        },
        {
            "id": "the_gps_skeptic",
            "name": "The Shortcut Believer",
            "heading": "THE SHORTCUT BELIEVER 🗺️",
            "character": "Overconfident anime driver holding a compass while car is submerged in a duck pond",
            "pose": "smug_crossed_arms",
            "expression": "smug",
            "background": "street_city",
            "narration": "Refuses to use Google Maps because they 'know a secret backroad' that magically adds two hours to the trip.",
            "speech_bubble": "The GPS wants us to take the highway. Trust me, cutting through this farm field saves four minutes!",
            "scenario": "Confidently driving down a muddy hiking trail convinced it is a secret expressway shortcut.",
        },
        {
            "id": "the_ghost",
            "name": "The Social Ghost",
            "heading": "THE SOCIAL GHOST 👻",
            "character": "Anime introverted ninja dissolving into shadows behind a party houseplant",
            "pose": "hiding_corner",
            "expression": "deadpan",
            "background": "office",
            "narration": "Arrives at the party, touches one chip, says hello to the host's cat, and vanishes into thin air without a trace.",
            "speech_bubble": "My social battery has expired. Performing smoke-bomb tactical Irish exit now.",
            "scenario": "Silently stepping behind a curtain and teleporting home without uttering a single farewell.",
        },
        {
            "id": "weather_denier",
            "name": "The Weather Denier",
            "heading": "THE WEATHER DENIER ❄️",
            "character": "Anime teenager shivering in flip-flops and swim trunks amidst a howling snowstorm",
            "pose": "standing_stoic",
            "expression": "intense",
            "background": "snowy_winter",
            "narration": "Refuses to admit it is cold even as icicles form on their eyelashes, insisting that a jacket would be 'too warm'.",
            "speech_bubble": "It is not cold, it is merely crisp! Real champions do not surrender to fleece jackets!",
            "scenario": "Walking calmly through six inches of snow wearing cargo shorts and sunglasses.",
        },
    ],
    "dark_comedy": [
        {
            "id": "toilet_paper_king",
            "name": "The Supply Hoarder",
            "heading": "THE SUPPLY HOARDER 🧻",
            "character": "Anime bunker survivor sitting atop a throne of 4,000 toilet paper rolls with a shotgun",
            "pose": "smug_crossed_arms",
            "expression": "smug",
            "background": "zombie_wasteland",
            "narration": "During an alien invasion or zombie plague, their immediate instinct is purchasing enough paper towels to wrap the moon.",
            "speech_bubble": "The infected may roam outside, but inside my fortress, hygiene remains five stars!",
            "scenario": "Barricading the supermarket aisle with a mountain of multi-ply paper towels.",
        },
        {
            "id": "zombie_befriender",
            "name": "The Optimistic Befriender",
            "heading": "THE ZOMBIE BEFRIENDER 🧟",
            "character": "Naive anime protagonist offering a flower to a snarling mutant zombie",
            "pose": "peace_sign",
            "expression": "sparkling_eyes",
            "background": "zombie_wasteland",
            "narration": "Convinced that the undead horde is simply misunderstood and just needs a warm hug and group therapy.",
            "speech_bubble": "Look into his glowing red eyes! He doesn't want brains, he just wants affection!",
            "scenario": "Attempting to teach a feral mutant how to do high-fives in the middle of a wasteland.",
        },
        {
            "id": "chill_snacker",
            "name": "The Unbothered Snacker",
            "heading": "THE UNBOTHERED SNACKER 🍿",
            "character": "Relaxed anime character eating popcorn while meteors crash into the background",
            "pose": "sleeping_desk",
            "expression": "deadpan",
            "background": "zombie_wasteland",
            "narration": "Society has completely collapsed, but they finally don't have to reply to work emails on Monday morning.",
            "speech_bubble": "The world is burning, but hey... at least nobody can schedule a 9 AM status sync meeting anymore.",
            "scenario": "Lounging comfortably on a lawn chair watching the chaos with iced lemonade.",
        },
        {
            "id": "first_betrayer",
            "name": "The Instant Betrayer",
            "heading": "THE 5-SECOND BETRAYER 🐍",
            "character": "Shifty anime rogue in sunglasses pointing towards their own allies",
            "pose": "dramatic_pointing",
            "expression": "mischievous",
            "background": "zombie_wasteland",
            "narration": "Pledges lifelong loyalty, then trades their entire squad to the zombie leader for a half-eaten candy bar three minutes later.",
            "speech_bubble": "Look guys, it's just basic market economics! They had peanut butter cups!",
            "scenario": "Signing a peace treaty with the alien invaders in exchange for free Wi-Fi.",
        },
    ],
    "anime": [
        {
            "id": "shonen_protagonist",
            "name": "The Monologuing Hero",
            "heading": "THE SPEECH MAKER 🗣️",
            "character": "Spiky-haired anime protagonist glowing with golden aura giving an impassioned speech to a grocery clerk",
            "pose": "dramatic_heroic",
            "expression": "intense",
            "background": "supermarket",
            "narration": "Gives a fifteen-minute speech about the power of friendship and unbreakable destiny when asked if he wants paper or plastic.",
            "speech_bubble": "Even if this grocery bag tears, the unbreakable bonds of our friendship will carry these eggs home!",
            "scenario": "Clenching fists in dramatic slow-motion while music swells in the checkout aisle.",
        },
        {
            "id": "glasses_pusher",
            "name": "The Glasses Intellect",
            "heading": "THE GLASSES PUSHER 👓",
            "character": "Anime genius with glasses flashing pure blinding white light with one finger on the frame",
            "pose": "smug_crossed_arms",
            "expression": "smug",
            "background": "classroom",
            "narration": "Pushes their glasses up the bridge of their nose, unleashing a blinding flash of tactical wisdom before ordering a sandwich.",
            "speech_bubble": "All according to plan. Your choice of condiment was predicted forty moves ago.",
            "scenario": "Releasing a lens glare flash that illuminates the whole room before delivering a trivial observation.",
        },
        {
            "id": "window_gazer",
            "name": "The Back Row Window Gazer",
            "heading": "THE WINDOW GAZER 🪟",
            "character": "Brooding anime teenager sitting by the classroom window staring dramatically at falling cherry blossoms",
            "pose": "standing_stoic",
            "expression": "deadpan",
            "background": "classroom",
            "narration": "Occupies the sacred second-to-last seat by the window, waiting for the magical school transfer student to fall from the sky.",
            "speech_bubble": "The wind... it speaks of impending destiny and unexpected homework assignments.",
            "scenario": "Looking mysteriously at the horizon while the teacher demands their geometry worksheet.",
        },
        {
            "id": "toast_runner",
            "name": "The Toast Sprinter",
            "heading": "THE TOAST RUNNER 🍞",
            "character": "Frantic anime schoolgirl sprinting with a slice of buttered toast clamped between teeth",
            "pose": "screaming_run",
            "expression": "panicked",
            "background": "street_city",
            "narration": "Wakes up late, runs out the front door with a slice of bread in mouth, and inevitably collides with destiny at the street corner.",
            "speech_bubble": "I'm late, I'm late! If I don't bump into my rival at this intersection my plot will never advance!",
            "scenario": "Running at 30 miles per hour with breakfast toast securely held in mouth.",
        },
    ],
}


# ============================================================================
# Procedural Generator Implementation
# ============================================================================


def _generate_procedural_script(config: MemeConfig) -> MemeScript:
    """Build a rich, high-quality, hilarious meme script procedurally.

    Uses deterministic topic selection, curated anime cultural profiles,
    and automatic scene mood matching.

    Args:
        config: MemeConfig parameters.

    Returns:
        Fully populated MemeScript instance.
    """
    mode = config.mode
    category = config.category
    topic, resolved_cat = resolve_topic_and_category(
        topic=config.topic,
        category=category,
        mode=mode,
    )
    segment_count = max(5, min(10, config.segment_count))

    # Determine topic keyword for scenario matching
    topic_lower = topic.lower()
    scenario_key = "default"
    if any(k in topic_lower for k in ("transit", "transport", "bus", "train", "subway")):
        scenario_key = "public_transport"
    elif any(k in topic_lower for k in ("drive", "traffic", "car", "autobahn", "road")):
        scenario_key = "driving"
    elif any(k in topic_lower for k in ("school", "exam", "student", "class", "teacher")):
        scenario_key = "school"
    elif any(k in topic_lower for k in ("winter", "snow", "cold", "freeze")):
        scenario_key = "winter"
    elif any(k in topic_lower for k in ("food", "eat", "snack", "dinner", "breakfast", "buffet")):
        scenario_key = "food"
    elif any(k in topic_lower for k in ("supermarket", "store", "shop")):
        scenario_key = "supermarket"
    elif any(k in topic_lower for k in ("parent", "mom", "dad", "grandma")):
        scenario_key = "parents"
    elif any(k in topic_lower for k in ("internet", "wifi", "tech")):
        scenario_key = "internet"
    elif any(k in topic_lower for k in ("survive", "survival", "wildlife", "zombie", "apocalypse")):
        scenario_key = "survival"

    segments: list[MemeSegment] = []

    if mode == "country_meme":
        # Select countries
        available = list(COUNTRY_ARCHETYPES)
        # Seeded shuffle based on topic so it's consistent for identical inputs but fresh otherwise
        rng = random.Random(topic + str(segment_count))
        rng.shuffle(available)
        selected_countries = available[:segment_count]

        for idx, country in enumerate(selected_countries):
            is_final = idx == len(selected_countries) - 1
            scenarios_map = country.get("scenarios", {})
            scen_tuple = scenarios_map.get(scenario_key) or scenarios_map.get("default")
            if not scen_tuple:
                scen_tuple = (
                    f"Handles {topic} with unmistakable national flair and extreme anime exaggeration.",
                    "This is how we do things in my country!",
                    f"In {country['name']}, {topic} is treated with unmatched cultural flair.",
                )

            scenario_desc, speech_bubble, narration = scen_tuple
            pose = country.get("pose", "dramatic_heroic")
            expression = country.get("expression", "smug")
            bg = country.get("background", "street_city")

            # Final segment punchline exaggeration
            if is_final:
                expression = "screaming" if expression != "screaming" else "crying_laughing"

            music_mood, sfx_cue, anim = match_audio_and_sfx(
                scenario=scenario_desc,
                expression=expression,
                pose=pose,
                is_punchline=is_final,
            )

            segment = MemeSegment(
                id=f"{country['id']}_{idx + 1}",
                name=country["name"],
                heading=country["heading"],
                narration=narration,
                scenario=scenario_desc,
                character_description=country["character"],
                pose=pose,
                expression=expression,
                background_theme=bg,
                animation_type=anim,
                music_mood=music_mood,
                sfx_cue=sfx_cue,
                subtitle_text=narration,
                speech_bubble_text=speech_bubble,
            )
            segments.append(segment)

        # Country title & hooks
        suffix = " 🌍💀"
        if "be like" in topic.lower():
            title = f"{topic}{suffix}"
        else:
            title = f"{topic} Be Like{suffix}"
        hooks = [
            f"Every single country handles this differently, but wait for number {len(segments)}! 💀",
            "Why is this so terrifyingly accurate?! Look at how your country acts: 😭",
            "Tell me which country matches your daily life in the comments! 👇",
        ]
        hook = hooks[hash(topic) % len(hooks)]

        clean_topic = topic
        for prefix in ("Every Country's ", "Every Country "):
            if clean_topic.startswith(prefix):
                clean_topic = clean_topic[len(prefix) :]
        for suffix in (" Be Like", " be like"):
            if clean_topic.endswith(suffix):
                clean_topic = clean_topic[: -len(suffix)]

        intro = f"Today we are exposing how different countries handle {clean_topic}! Let's see who is the most chaotic..."
        ending = "Which country are you representing? Drop your flag in the comments and subscribe for part 2! 🚩"

    else:
        # Types of... mode
        cat_key = resolved_cat if resolved_cat in TYPES_OF_ARCHETYPES else "relatable"
        if "game" in topic_lower or "gamer" in topic_lower:
            cat_key = "gamers"
        elif "school" in topic_lower or "student" in topic_lower or "exam" in topic_lower:
            cat_key = "school"
        elif "zombie" in topic_lower or "apocalypse" in topic_lower or "dark" in topic_lower:
            cat_key = "dark_comedy"
        elif "anime" in topic_lower:
            cat_key = "anime"

        pool = list(TYPES_OF_ARCHETYPES.get(cat_key, TYPES_OF_ARCHETYPES["relatable"]))
        # Also mix in general relatable if pool is small
        if len(pool) < segment_count:
            for item in TYPES_OF_ARCHETYPES["relatable"]:
                if item not in pool:
                    pool.append(item)

        rng = random.Random(topic + str(segment_count))
        rng.shuffle(pool)
        selected_types = pool[:segment_count]

        for idx, item in enumerate(selected_types):
            is_final = idx == len(selected_types) - 1
            pose = item.get("pose", "dramatic_heroic")
            expression = item.get("expression", "smug")
            bg = item.get("background", "classroom")

            if is_final:
                expression = "panicked" if expression != "panicked" else "crying_laughing"

            music_mood, sfx_cue, anim = match_audio_and_sfx(
                scenario=item["scenario"],
                expression=expression,
                pose=pose,
                is_punchline=is_final,
            )

            segment = MemeSegment(
                id=f"{item['id']}_{idx + 1}",
                name=item["name"],
                heading=item["heading"],
                narration=item["narration"],
                scenario=item["scenario"],
                character_description=item["character"],
                pose=pose,
                expression=expression,
                background_theme=bg,
                animation_type=anim,
                music_mood=music_mood,
                sfx_cue=sfx_cue,
                subtitle_text=item["narration"],
                speech_bubble_text=item["speech_bubble"],
            )
            segments.append(segment)

        title = f"{topic} Be Like... 😭💀"
        hooks = [
            f"There are strictly {len(segments)} types of people in this situation, and you know EVERY one of them! 💀",
            "Tag the friend who is 100% number 3 right now! 😭",
            "If you do not do at least two of these, are you even human?! 💀",
        ]
        hook = hooks[hash(topic) % len(hooks)]

        clean_topic = topic
        for prefix in ("Types of ", "Types Of "):
            if clean_topic.startswith(prefix):
                clean_topic = clean_topic[len(prefix) :]
        for suffix in (" Be Like", " be like"):
            if clean_topic.endswith(suffix):
                clean_topic = clean_topic[: -len(suffix)]

        intro = f"There are strictly {len(segments)} types of {clean_topic}! Let's see which one you are..."
        ending = (
            "Which type did we miss? Call them out in the comments below and follow for more! 👇"
        )

    hashtags = select_hashtags(
        mode=mode,
        category=resolved_cat,
        topic=topic,
        count=4,
    )

    # Estimate duration: hook ~3s + intro ~4s + segments (~6.5s each) + ending ~4s
    est_duration = 3.0 + 4.0 + (len(segments) * 6.5) + 4.0

    return MemeScript(
        mode=mode,
        category=resolved_cat,
        topic=topic,
        title=title,
        hook=hook,
        intro=intro,
        segments=segments,
        ending=ending,
        hashtags=hashtags,
        estimated_duration=round(est_duration, 1),
    )


# ============================================================================
# LLM Generation with Fallback
# ============================================================================


def _generate_llm_script(config: MemeConfig) -> MemeScript | None:
    """Attempt to generate script via configured LLM provider.

    Returns None if provider unavailable or output fails JSON validation.

    Args:
        config: MemeConfig parameters.

    Returns:
        MemeScript or None on failure.
    """
    try:
        provider = get_provider()
    except Exception as exc:
        logger.debug("LLM provider unavailable: %s", exc)
        return None

    mode = config.mode
    topic, category = resolve_topic_and_category(
        topic=config.topic,
        category=config.category,
        mode=mode,
    )
    count = max(5, min(10, config.segment_count))

    prompt = f"""You are an elite viral YouTube Shorts and TikTok meme comedy writer.
Generate a structured JSON script for a {mode} short video.

Topic: "{topic}"
Category: "{category}"
Number of segments: {count}
Style: Anime comedy with relatable cultural and character stereotypes.

Allowed values:
- pose: sleeping_desk, surrounded_books, gaming_intense, hiding_corner, dramatic_heroic, smug_crossed_arms, shocked_facepalm, screaming_run, peace_sign, facepalm, finger_guns, bowing_polite, standing_stoic, running_panic, dramatic_pointing
- expression: shocked, smug, deadpan, panicked, screaming, sleeping, crying_laughing, sparkling_eyes, intense, mischievous, proud
- background_theme: train_station, classroom, kitchen, gaming_room, street_city, snowy_winter, supermarket, airport, office, gym, zombie_wasteland, bedroom, library
- animation_type: zoom_in, zoom_out, shake, pan_left, pan_right, parallax, impact_pop
- music_mood: playful, quirky, epic_orchestral, suspense, punchline_cut, unexpected_reveal, lofi_chill
- sfx_cue: record_scratch, bass_drop, cartoon_impact, whoosh, rimshot, stinger, none

Required JSON schema:
{{
  "mode": "{mode}",
  "category": "{category}",
  "topic": "{topic}",
  "title": "High-engagement title with emojis",
  "hook": "0-3s high-retention hook sentence",
  "intro": "3-6s topic introduction",
  "segments": [
    {{
      "id": "unique_id",
      "name": "Country or Type Name",
      "heading": "DISPLAY HEADING WITH EMOJI",
      "narration": "Funny punchy voiceover narration (15-25 words)",
      "scenario": "Visual situation description",
      "character_description": "Anime character visual look",
      "pose": "one of allowed poses",
      "expression": "one of allowed expressions",
      "background_theme": "one of allowed backgrounds",
      "animation_type": "one of allowed animations",
      "music_mood": "one of allowed music moods",
      "sfx_cue": "one of allowed sfx cues",
      "subtitle_text": "Clean subtitle line",
      "speech_bubble_text": "Short punchy anime dialogue bubble"
    }}
  ],
  "ending": "Funny punchline + viewer engagement call to action question"
}}

Respond ONLY with valid JSON. No markdown code fences, no extra text.
"""

    try:
        response_text = provider.complete(prompt, max_tokens=2500, temperature=0.7)
        # Clean response of potential markdown fences
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        data = json.loads(cleaned)

        raw_segments = data.get("segments", [])
        if not isinstance(raw_segments, list) or len(raw_segments) < 3:
            logger.warning("LLM returned too few segments (%d). Falling back.", len(raw_segments))
            return None

        segments: list[MemeSegment] = []
        for i, s in enumerate(raw_segments):
            seg = MemeSegment(
                id=str(s.get("id") or f"seg_{i + 1}"),
                name=str(s.get("name") or f"Segment {i + 1}"),
                heading=str(s.get("heading") or s.get("name") or f"PART {i + 1}"),
                narration=str(s.get("narration") or ""),
                scenario=str(s.get("scenario") or ""),
                character_description=str(s.get("character_description") or "Anime character"),
                pose=str(s.get("pose") if s.get("pose") in POSES else "dramatic_heroic"),
                expression=str(
                    s.get("expression") if s.get("expression") in EXPRESSIONS else "smug"
                ),
                background_theme=str(
                    s.get("background_theme")
                    if s.get("background_theme") in BACKGROUNDS
                    else "classroom"
                ),
                animation_type=str(
                    s.get("animation_type")
                    if s.get("animation_type") in ANIMATION_TYPES
                    else "zoom_in"
                ),
                music_mood=str(
                    s.get("music_mood") if s.get("music_mood") in MUSIC_MOODS else "playful"
                ),
                sfx_cue=str(s.get("sfx_cue") if s.get("sfx_cue") in SFX_CUES else "cartoon_impact"),
                subtitle_text=str(s.get("subtitle_text") or s.get("narration") or ""),
                speech_bubble_text=str(s.get("speech_bubble_text") or ""),
            )
            segments.append(seg)

        hashtags = select_hashtags(
            mode=mode,
            category=category,
            topic=topic,
            count=4,
        )

        est_duration = 3.0 + 4.0 + (len(segments) * 6.5) + 4.0

        raw_title = str(
            data.get("title")
            or (f"{topic} Be Like 💀" if "be like" not in topic.lower() else f"{topic} 💀")
        )
        clean_title = re.sub(
            r"\(?\s*anime\s+edition\s*\)?", "", raw_title, flags=re.IGNORECASE
        ).strip()

        return MemeScript(
            mode=mode,
            category=category,
            topic=topic,
            title=clean_title,
            hook=str(data.get("hook") or "Tell me which one is you in the comments! 💀"),
            intro=str(data.get("intro") or f"Today we're exposing {topic}!"),
            segments=segments,
            ending=str(data.get("ending") or "Which one did we miss? Comment below! 👇"),
            hashtags=hashtags,
            estimated_duration=round(est_duration, 1),
        )
    except Exception as exc:
        logger.warning("LLM script generation failed, falling back to procedural: %s", exc)
        return None


def build_meme_script(config: MemeConfig, use_llm: bool = True) -> MemeScript:
    """Build a complete meme video script from configuration.

    Tries LLM generation if enabled, otherwise falls back seamlessly
    to rich procedural generation with deterministic variety.

    Args:
        config: MemeConfig containing topic, mode, segment count, etc.
        use_llm: Whether to attempt calling LLM provider first. Defaults to True.

    Returns:
        Structured MemeScript object.
    """
    config.validate()

    if use_llm:
        script = _generate_llm_script(config)
        if script is not None:
            return script

    return _generate_procedural_script(config)
