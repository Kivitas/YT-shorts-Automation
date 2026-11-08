"""Wojak long-form cultural parody topic catalogs & procedural joke engine.

Modeled after viral SapX country meme parodies (16:9 landscape, 2.5-3.0 minutes,
25-35 countries, 4.0-4.5 seconds per country, mandatory 3.5s Parody Disclaimer).
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from typing import Any

# ==============================================================================
# Mandatory Parody Video Constraints & Disclaimer
# ==============================================================================

MANDATORY_DISCLAIMER_HEADING: str = "⚠️ PARODY DISCLAIMER"
MANDATORY_DISCLAIMER_BODY: str = (
    "This video is a satirical comedy parody for entertainment purposes only. "
    "Regional stereotypes, cultural habits, and healthcare depictions are exaggerated "
    "satire. Nothing in this video should be taken seriously. Enjoy the memes!"
)
DISCLAIMER_DURATION_SECONDS: float = 3.5

# Pacing & Video Format Constraints
LANDSCAPE_WIDTH: int = 1920
LANDSCAPE_HEIGHT: int = 1080
ASPECT_RATIO: str = "16:9"

MIN_COUNTRIES_PER_VIDEO: int = 25
MAX_COUNTRIES_PER_VIDEO: int = 35
DEFAULT_COUNTRIES_PER_VIDEO: int = 33

MIN_COUNTRY_DURATION: float = 4.0
MAX_COUNTRY_DURATION: float = 4.5
DEFAULT_COUNTRY_DURATION: float = 4.5

MIN_TOTAL_DURATION: float = 150.0  # 2.5 minutes
MAX_TOTAL_DURATION: float = 180.0  # 3.0 minutes


@dataclass(frozen=True)
class CountryMeta:
    """Metadata for country cards in Wojak longform videos."""

    name: str
    country_code: str
    heading: str
    demonym: str
    default_expression: str = "neutral"
    default_prop: str = "none"
    default_attire: str = "shirt"
    default_race: str = "fair"
    cultural_traits: tuple[str, ...] = field(default_factory=tuple)


# Catalog of 40 well-recognized countries with codes, headings, and character defaults
COUNTRY_CATALOG: dict[str, CountryMeta] = {
    "USA": CountryMeta(
        name="United States",
        country_code="US",
        heading="USA",
        demonym="American",
        default_expression="shocked",
        default_prop="bill",
        default_attire="jacket",
        default_race="fair",
        cultural_traits=("expensive bills", "lawsuits", "tipping", "giant cars", "fast food"),
    ),
    "UK": CountryMeta(
        name="United Kingdom",
        country_code="GB",
        heading="UK",
        demonym="British",
        default_expression="tired",
        default_prop="tea_cup",
        default_attire="jacket",
        default_race="fair",
        cultural_traits=("queueing", "weather complaints", "apologizing", "tea", "understatement"),
    ),
    "Sweden": CountryMeta(
        name="Sweden",
        country_code="SE",
        heading="Sweden",
        demonym="Swedish",
        default_expression="neutral",
        default_prop="coffee_cup",
        default_attire="shirt",
        default_race="pale",
        cultural_traits=("fika", "flatpack furniture", "personal space", "queues", "lagom"),
    ),
    "India": CountryMeta(
        name="India",
        country_code="IN",
        heading="India",
        demonym="Indian",
        default_expression="smug",
        default_prop="phone",
        default_attire="shirt",
        default_race="brown",
        cultural_traits=("bargaining", "jugaad", "parental pressure", "spices", "engineers"),
    ),
    "South Korea": CountryMeta(
        name="South Korea",
        country_code="KR",
        heading="South Korea",
        demonym="South Korean",
        default_expression="smug",
        default_prop="phone",
        default_attire="jacket",
        default_race="light",
        cultural_traits=("hagwons", "plastic surgery", "iced americano", "k-pop", "5g"),
    ),
    "Portugal": CountryMeta(
        name="Portugal",
        country_code="PT",
        heading="Portugal",
        demonym="Portuguese",
        default_expression="tired",
        default_prop="pastry",
        default_attire="shirt",
        default_race="tan",
        cultural_traits=("pastel de nata", "bacalhau", "saudade", "trams", "patience"),
    ),
    "Italy": CountryMeta(
        name="Italy",
        country_code="IT",
        heading="Italy",
        demonym="Italian",
        default_expression="happy",
        default_prop="pasta_pot",
        default_attire="shirt",
        default_race="light",
        cultural_traits=("pasta rules", "hand gestures", "mama cooking", "espresso", "drama"),
    ),
    "Netherlands": CountryMeta(
        name="Netherlands",
        country_code="NL",
        heading="Netherlands",
        demonym="Dutch",
        default_expression="annoyed",
        default_prop="clipboard",
        default_attire="shirt",
        default_race="fair",
        cultural_traits=("bicycles", "paracetamol", "tikkie", "directness", "hagelslag"),
    ),
    "Germany": CountryMeta(
        name="Germany",
        country_code="DE",
        heading="Germany",
        demonym="German",
        default_expression="neutral",
        default_prop="clipboard",
        default_attire="jacket",
        default_race="fair",
        cultural_traits=("bureaucracy", "forms in triplicate", "autobahn", "lueften", "rules"),
    ),
    "Ireland": CountryMeta(
        name="Ireland",
        country_code="IE",
        heading="Ireland",
        demonym="Irish",
        default_expression="happy",
        default_prop="tea_cup",
        default_attire="shirt",
        default_race="pale",
        cultural_traits=("craic", "tayto crisps", "trolley waits", "rain", "pub banter"),
    ),
    "Canada": CountryMeta(
        name="Canada",
        country_code="CA",
        heading="Canada",
        demonym="Canadian",
        default_expression="tired",
        default_prop="coffee_cup",
        default_attire="jacket",
        default_race="fair",
        cultural_traits=("apologies", "tim hortons", "subzero blizzards", "mri waitlists", "maple syrup"),
    ),
    "France": CountryMeta(
        name="France",
        country_code="FR",
        heading="France",
        demonym="French",
        default_expression="annoyed",
        default_prop="sign",
        default_attire="coat",
        default_race="light",
        cultural_traits=("strikes", "baguettes", "philosophical debates", "cheese", "complaints"),
    ),
    "Turkey": CountryMeta(
        name="Turkey",
        country_code="TR",
        heading="Turkey",
        demonym="Turkish",
        default_expression="smug",
        default_prop="tea_cup",
        default_attire="shirt",
        default_race="tan",
        cultural_traits=("hair transplants", "cay", "street cats", "bargaining", "hospitality"),
    ),
    "Japan": CountryMeta(
        name="Japan",
        country_code="JP",
        heading="Japan",
        demonym="Japanese",
        default_expression="shocked",
        default_prop="bag",
        default_attire="jacket",
        default_race="light",
        cultural_traits=("polite bowing", "on-time trains", "vending machines", "overwork", "convenience stores"),
    ),
    "Australia": CountryMeta(
        name="Australia",
        country_code="AU",
        heading="Australia",
        demonym="Australian",
        default_expression="neutral",
        default_prop="clipboard",
        default_attire="shirt",
        default_race="tan",
        cultural_traits=("deadly wildlife", "spiders", "she'll be right", "vegemite", "beaches"),
    ),
    "Switzerland": CountryMeta(
        name="Switzerland",
        country_code="CH",
        heading="Switzerland",
        demonym="Swiss",
        default_expression="smug",
        default_prop="bill",
        default_attire="jacket",
        default_race="fair",
        cultural_traits=("astronomical prices", "luxury watches", "punctuality", "quiet hours", "fondue"),
    ),
    "Brazil": CountryMeta(
        name="Brazil",
        country_code="BR",
        heading="Brazil",
        demonym="Brazilian",
        default_expression="happy",
        default_prop="phone",
        default_attire="jersey",
        default_race="tan",
        cultural_traits=("football", "carnival", "samba in queues", "warmth", "optimism"),
    ),
    "Mexico": CountryMeta(
        name="Mexico",
        country_code="MX",
        heading="Mexico",
        demonym="Mexican",
        default_expression="happy",
        default_prop="pasta_pot",
        default_attire="shirt",
        default_race="tan",
        cultural_traits=("la chancla", "spicy chili", "fiestas", "oxxo stores", "grandma remedies"),
    ),
    "Poland": CountryMeta(
        name="Poland",
        country_code="PL",
        heading="Poland",
        demonym="Polish",
        default_expression="annoyed",
        default_prop="bag",
        default_attire="coat",
        default_race="fair",
        cultural_traits=("przeciag draft fears", "biedronka", "babcia hospitality", "potatoes", "complaining"),
    ),
    "Spain": CountryMeta(
        name="Spain",
        country_code="ES",
        heading="Spain",
        demonym="Spanish",
        default_expression="tired",
        default_prop="coffee_cup",
        default_attire="shirt",
        default_race="tan",
        cultural_traits=("siesta", "midnight dinners", "tapas", "mercadona", "loud conversations"),
    ),
    "Greece": CountryMeta(
        name="Greece",
        country_code="GR",
        heading="Greece",
        demonym="Greek",
        default_expression="happy",
        default_prop="coffee_cup",
        default_attire="shirt",
        default_race="tan",
        cultural_traits=("frappe", "family gatherings", "olive oil barrels", "ferry chaos", "philoxenia"),
    ),
    "Singapore": CountryMeta(
        name="Singapore",
        country_code="SG",
        heading="Singapore",
        demonym="Singaporean",
        default_expression="neutral",
        default_prop="clipboard",
        default_attire="jacket",
        default_race="light",
        cultural_traits=("strict fines", "spotless streets", "kiasu attitude", "hawker centres", "aircon"),
    ),
    "Finland": CountryMeta(
        name="Finland",
        country_code="FI",
        heading="Finland",
        demonym="Finnish",
        default_expression="neutral",
        default_prop="none",
        default_attire="shirt",
        default_race="pale",
        cultural_traits=("sauna silence", "lonkero", "extreme winter stamina", "personal space", "sisu"),
    ),
    "Norway": CountryMeta(
        name="Norway",
        country_code="NO",
        heading="Norway",
        demonym="Norwegian",
        default_expression="annoyed",
        default_prop="clipboard",
        default_attire="coat",
        default_race="pale",
        cultural_traits=("cross-country skiing", "expensive groceries", "oil fund", "brown cheese", "stoicism"),
    ),
    "Egypt": CountryMeta(
        name="Egypt",
        country_code="EG",
        heading="Egypt",
        demonym="Egyptian",
        default_expression="smug",
        default_prop="tea_cup",
        default_attire="shirt",
        default_race="tan",
        cultural_traits=("microbus drifting", "balcony baskets", "bismillah remedies", "bargaining", "humor"),
    ),
    "China": CountryMeta(
        name="China",
        country_code="CN",
        heading="China",
        demonym="Chinese",
        default_expression="neutral",
        default_prop="tea_cup",
        default_attire="shirt",
        default_race="light",
        cultural_traits=("hot water cures all", "gaokao grind", "mobile payments", "speed", "hard work"),
    ),
    "Argentina": CountryMeta(
        name="Argentina",
        country_code="AR",
        heading="Argentina",
        demonym="Argentine",
        default_expression="annoyed",
        default_prop="clipboard",
        default_attire="jersey",
        default_race="tan",
        cultural_traits=("asado", "messi debates", "inflation humor", "psychoanalysis", "mate tea"),
    ),
    "South Africa": CountryMeta(
        name="South Africa",
        country_code="ZA",
        heading="South Africa",
        demonym="South African",
        default_expression="smug",
        default_prop="phone",
        default_attire="shirt",
        default_race="dark",
        cultural_traits=("loadshedding", "braai", "now-now timing", "bakkies", "resilience"),
    ),
    "Austria": CountryMeta(
        name="Austria",
        country_code="AT",
        heading="Austria",
        demonym="Austrian",
        default_expression="smug",
        default_prop="pastry",
        default_attire="jacket",
        default_race="pale",
        cultural_traits=("sachertorte", "wiener grant", "classical music", "cleanliness", "formalities"),
    ),
    "Belgium": CountryMeta(
        name="Belgium",
        country_code="BE",
        heading="Belgium",
        demonym="Belgian",
        default_expression="annoyed",
        default_prop="sign",
        default_attire="jacket",
        default_race="fair",
        cultural_traits=("flemish vs walloon", "fries with mayo", "waffles", "potholes", "compromises"),
    ),
    "New Zealand": CountryMeta(
        name="New Zealand",
        country_code="NZ",
        heading="New Zealand",
        demonym="New Zealander",
        default_expression="happy",
        default_prop="ball",
        default_attire="jersey",
        default_race="tan",
        cultural_traits=("rugby", "barefoot in shops", "all blacks", "kiwi slang", "laidback"),
    ),
    "Philippines": CountryMeta(
        name="Philippines",
        country_code="PH",
        heading="Philippines",
        demonym="Filipino",
        default_expression="happy",
        default_prop="phone",
        default_attire="shirt",
        default_race="tan",
        cultural_traits=("karaoke", "nurses abroad", "lechon", "mall culture", "warm smiles"),
    ),
    "Thailand": CountryMeta(
        name="Thailand",
        country_code="TH",
        heading="Thailand",
        demonym="Thai",
        default_expression="happy",
        default_prop="tea_cup",
        default_attire="shirt",
        default_race="tan",
        cultural_traits=("spicy street food", "7-eleven sanctuary", "mai pen rai", "luxury clinics", "smiles"),
    ),
    "Vietnam": CountryMeta(
        name="Vietnam",
        country_code="VN",
        heading="Vietnam",
        demonym="Vietnamese",
        default_expression="happy",
        default_prop="pasta_pot",
        default_attire="shirt",
        default_race="tan",
        cultural_traits=("motorbikes everywhere", "iced coffee", "pho", "plastic sidewalk chairs", "bustle"),
    ),
    "Romania": CountryMeta(
        name="Romania",
        country_code="RO",
        heading="Romania",
        demonym="Romanian",
        default_expression="smug",
        default_prop="coffee_cup",
        default_attire="jacket",
        default_race="fair",
        cultural_traits=("informal envelopes", "dracula tourism", "fast internet", "hustle", "dacia cars"),
    ),
    "Hungary": CountryMeta(
        name="Hungary",
        country_code="HU",
        heading="Hungary",
        demonym="Hungarian",
        default_expression="annoyed",
        default_prop="clipboard",
        default_attire="shirt",
        default_race="fair",
        cultural_traits=("paprika", "thermal baths", "complex bureaucracy", "thermal springs", "sour cream"),
    ),
    "Czech Republic": CountryMeta(
        name="Czech Republic",
        country_code="CZ",
        heading="Czech Republic",
        demonym="Czech",
        default_expression="neutral",
        default_prop="tea_cup",
        default_attire="shirt",
        default_race="fair",
        cultural_traits=("pilsner cheaper than water", "dark humor", "mushroom picking", "socks in sandals", "castles"),
    ),
    "Denmark": CountryMeta(
        name="Denmark",
        country_code="DK",
        heading="Denmark",
        demonym="Danish",
        default_expression="smug",
        default_prop="pastry",
        default_attire="shirt",
        default_race="pale",
        cultural_traits=("hygge", "bicycles in sleet", "design", "pastries", "high taxes"),
    ),
    "Saudi Arabia": CountryMeta(
        name="Saudi Arabia",
        country_code="SA",
        heading="Saudi Arabia",
        demonym="Saudi",
        default_expression="smug",
        default_prop="phone",
        default_attire="shirt",
        default_race="tan",
        cultural_traits=("luxury supercars", "kabsa feasts", "desert drifting", "mega projects", "ac at max"),
    ),
    "Colombia": CountryMeta(
        name="Colombia",
        country_code="CO",
        heading="Colombia",
        demonym="Colombian",
        default_expression="happy",
        default_prop="coffee_cup",
        default_attire="shirt",
        default_race="tan",
        cultural_traits=("tinto coffee", "salsa dancing", "arepas", "polite greetings", "music everywhere"),
    ),
}

# Aliases to resolve informal country references
COUNTRY_ALIASES: dict[str, str] = {
    "us": "USA",
    "usa": "USA",
    "united states": "USA",
    "america": "USA",
    "uk": "UK",
    "united kingdom": "UK",
    "britain": "UK",
    "england": "UK",
    "great britain": "UK",
    "south korea": "South Korea",
    "korea": "South Korea",
    "nz": "New Zealand",
    "new zealand": "New Zealand",
    "czechia": "Czech Republic",
    "czech republic": "Czech Republic",
    "uae": "Saudi Arabia",
}

# ==============================================================================
# Canonical Topics & Aliases
# ==============================================================================

CANONICAL_TOPICS = (
    "hospital",
    "public_transport",
    "school",
    "parents",
    "dating",
    "supermarkets",
    "winter",
    "driving",
)

TOPIC_TITLES: dict[str, str] = {
    "hospital": "Every Country's Hospital Be Like",
    "public_transport": "Every Country's Public Transport Be Like",
    "school": "Every Country's School Life Be Like",
    "parents": "Every Country's Parents Be Like",
    "dating": "Every Country's Dating Culture Be Like",
    "supermarkets": "Every Country's Supermarkets Be Like",
    "winter": "Every Country's Winter Be Like",
    "driving": "Every Country's Driving Be Like",
}

TOPIC_HASHTAGS: dict[str, list[str]] = {
    "hospital": ["#wojak", "#memes", "#everycountrybelike", "#hospitalmemes", "#healthcare", "#parody", "#funny"],
    "public_transport": ["#wojak", "#memes", "#everycountrybelike", "#publictransport", "#trains", "#subway", "#funny"],
    "school": ["#wojak", "#memes", "#everycountrybelike", "#schoolmemes", "#studentlife", "#nostalgia", "#funny"],
    "parents": ["#wojak", "#memes", "#everycountrybelike", "#parentsmemes", "#relatable", "#family", "#funny"],
    "dating": ["#wojak", "#memes", "#everycountrybelike", "#datingculture", "#romance", "#singlelife", "#funny"],
    "supermarkets": ["#wojak", "#memes", "#everycountrybelike", "#supermarketmemes", "#groceryshopping", "#funny", "#parody"],
    "winter": ["#wojak", "#memes", "#everycountrybelike", "#wintermemes", "#coldweather", "#snow", "#funny"],
    "driving": ["#wojak", "#memes", "#everycountrybelike", "#drivingmemes", "#traffic", "#roadrage", "#funny"],
}

TOPIC_ALIASES: dict[str, str] = {
    "hospital": "hospital",
    "hospitals": "hospital",
    "healthcare": "hospital",
    "doctor": "hospital",
    "doctors": "hospital",
    "every country's hospital be like": "hospital",
    "every country hospital": "hospital",
    "public transport": "public_transport",
    "public_transport": "public_transport",
    "transport": "public_transport",
    "transit": "public_transport",
    "metro": "public_transport",
    "subway": "public_transport",
    "trains": "public_transport",
    "every country's public transport be like": "public_transport",
    "school": "school",
    "schools": "school",
    "school life": "school",
    "school_life": "school",
    "education": "school",
    "high school": "school",
    "every country's school life be like": "school",
    "parents": "parents",
    "parenting": "parents",
    "asian parents": "parents",
    "strict parents": "parents",
    "every country's parents be like": "parents",
    "dating": "dating",
    "dating culture": "dating",
    "dating_culture": "dating",
    "romance": "dating",
    "relationships": "dating",
    "love": "dating",
    "every country's dating culture be like": "dating",
    "supermarkets": "supermarkets",
    "supermarket": "supermarkets",
    "grocery": "supermarkets",
    "groceries": "supermarkets",
    "grocery shopping": "supermarkets",
    "every country's supermarkets be like": "supermarkets",
    "winter": "winter",
    "snow": "winter",
    "cold": "winter",
    "freezing": "winter",
    "blizzard": "winter",
    "every country's winter be like": "winter",
    "driving": "driving",
    "traffic": "driving",
    "drivers": "driving",
    "cars": "driving",
    "road": "driving",
    "roads": "driving",
    "every country's driving be like": "driving",
}


def normalize_wojak_topic(raw_topic: str) -> str:
    """Normalize any user topic phrase into a recognized canonical topic key or clean slug."""
    clean = re.sub(r"[^a-zA-Z0-9\s_]", "", raw_topic or "").strip().lower()
    clean = re.sub(r"\s+", " ", clean)
    if clean in TOPIC_ALIASES:
        return TOPIC_ALIASES[clean]
    # Check if a canonical topic is contained inside the phrase
    for phrase, canonical in TOPIC_ALIASES.items():
        if phrase in clean:
            return canonical
    return re.sub(r"\s+", "_", clean) or "hospital"


# ==============================================================================
# Curated Joke Catalogs (35 Countries x 8 Canonical Topics = 280 Viral Jokes)
# ==============================================================================

_HOSPITAL_JOKES: dict[str, dict[str, Any]] = {
    "USA": {
        "joke_text": "Hospital looks like a 5-star hotel. Price is also 5-star. $38 for one aspirin.",
        "setting": "luxury hospital lobby with billing desk",
        "character": {"age": 32, "gender": "male", "race": "fair", "expression": "shocked", "prop": "bill", "attire": "jacket"},
    },
    "UK": {
        "joke_text": "Lying on a trolley in the corridor for 14 hours. Still says 'lovely nurses.'",
        "setting": "busy NHS emergency hospital corridor",
        "character": {"age": 45, "gender": "male", "race": "fair", "expression": "tired", "prop": "tea_cup", "attire": "gown"},
    },
    "Sweden": {
        "joke_text": "Free and fair. You're number 112 in the phone queue. Fika while you wait.",
        "setting": "minimalist Scandinavian clinic waiting room",
        "character": {"age": 29, "gender": "female", "race": "pale", "expression": "neutral", "prop": "coffee_cup", "attire": "shirt"},
    },
    "India": {
        "joke_text": "World-class surgery for 10x less than in the US. Americans fly in with a list.",
        "setting": "state-of-the-art private hospital ward",
        "character": {"age": 38, "gender": "male", "race": "brown", "expression": "smug", "prop": "stethoscope", "attire": "lab_coat"},
    },
    "South Korea": {
        "joke_text": "Came in for a cold. Left with a new jawline. Paid with a coupon.",
        "setting": "high-tech Gangnam aesthetic medical clinic",
        "character": {"age": 24, "gender": "female", "race": "light", "expression": "smug", "prop": "phone", "attire": "jacket"},
    },
    "Portugal": {
        "joke_text": "ER wait: 12 hours. Brought a custard tart. Brought three actually.",
        "setting": "tiled Lisbon emergency waiting lounge",
        "character": {"age": 52, "gender": "male", "race": "tan", "expression": "tired", "prop": "pastry", "attire": "shirt"},
    },
    "Italy": {
        "joke_text": "Hospital food is bad, so the whole family brings lasagna. To the ICU.",
        "setting": "ICU ward room packed with 14 relatives",
        "character": {"age": 34, "gender": "female", "race": "light", "expression": "happy", "prop": "pasta_pot", "attire": "shirt"},
    },
    "Netherlands": {
        "joke_text": "Doctor prescribes 500mg paracetamol and cycling for a shattered femur.",
        "setting": "modern Dutch family clinic examination room",
        "character": {"age": 31, "gender": "male", "race": "fair", "expression": "annoyed", "prop": "clipboard", "attire": "shirt"},
    },
    "Germany": {
        "joke_text": "Please sign 42 compliance forms in triplicate before suffering from appendicitis.",
        "setting": "sterile German administrative admissions desk",
        "character": {"age": 42, "gender": "male", "race": "fair", "expression": "neutral", "prop": "clipboard", "attire": "jacket"},
    },
    "Ireland": {
        "joke_text": "Record number of patients on trolleys. The craic is still mighty.",
        "setting": "crowded Dublin emergency corridor",
        "character": {"age": 36, "gender": "male", "race": "pale", "expression": "happy", "prop": "tea_cup", "attire": "shirt"},
    },
    "Canada": {
        "joke_text": "Free MRI scan approved! Scheduled for November 14, 2029.",
        "setting": "Canadian community health triage desk",
        "character": {"age": 30, "gender": "female", "race": "fair", "expression": "tired", "prop": "clipboard", "attire": "jacket"},
    },
    "France": {
        "joke_text": "Doctors are on strike. Nurses are on strike. Even the ambulance is on strike.",
        "setting": "picket line outside Parisian university hospital",
        "character": {"age": 40, "gender": "male", "race": "light", "expression": "annoyed", "prop": "sign", "attire": "coat"},
    },
    "Turkey": {
        "joke_text": "Came for a heart checkup. Left with 4,000 new hair follicles.",
        "setting": "Istanbul medical tourism clinic suite",
        "character": {"age": 33, "gender": "male", "race": "tan", "expression": "smug", "prop": "bandage", "attire": "shirt"},
    },
    "Japan": {
        "joke_text": "Doctor bows 18 times to apologize that your medicine took 2 extra minutes.",
        "setting": "spotless Tokyo robotic pharmacy counter",
        "character": {"age": 44, "gender": "male", "race": "light", "expression": "shocked", "prop": "bag", "attire": "lab_coat"},
    },
    "Australia": {
        "joke_text": "Nurse casually moves a dinner-plate-sized huntsman spider off your IV drip.",
        "setting": "sunlit outback hospital ward room",
        "character": {"age": 28, "gender": "female", "race": "tan", "expression": "neutral", "prop": "clipboard", "attire": "shirt"},
    },
    "Switzerland": {
        "joke_text": "Hospital bill delivered in a leather velvet briefcase with a complementary Rolex.",
        "setting": "private Alpine luxury clinic suite overlooking mountains",
        "character": {"age": 50, "gender": "male", "race": "fair", "expression": "smug", "prop": "bill", "attire": "jacket"},
    },
    "Brazil": {
        "joke_text": "ER waiting room has samba music playing and the doctor is watching the football match.",
        "setting": "tropical public hospital waiting room",
        "character": {"age": 26, "gender": "male", "race": "tan", "expression": "happy", "prop": "phone", "attire": "jersey"},
    },
    "Mexico": {
        "joke_text": "Grandma brings home-made chicken soup into the sterile operating room. Doctor agrees it works better.",
        "setting": "lively regional general hospital corridor",
        "character": {"age": 60, "gender": "female", "race": "tan", "expression": "happy", "prop": "pasta_pot", "attire": "gown"},
    },
    "Poland": {
        "joke_text": "Old babcia claims opening the hospital window will cause a lethal draft (przeciąg).",
        "setting": "traditional Polish provincial hospital room",
        "character": {"age": 68, "gender": "female", "race": "fair", "expression": "annoyed", "prop": "bag", "attire": "coat"},
    },
    "Spain": {
        "joke_text": "Emergency room closes between 2 PM and 5 PM for mandatory siesta. Disease must wait.",
        "setting": "warm Andalusian hospital admissions area",
        "character": {"age": 35, "gender": "male", "race": "tan", "expression": "tired", "prop": "coffee_cup", "attire": "shirt"},
    },
    "Greece": {
        "joke_text": "Patient was discharged 3 days ago but 40 cousins are still in the courtyard drinking frappe.",
        "setting": "sunny Greek hospital courtyard with marble benches",
        "character": {"age": 39, "gender": "male", "race": "tan", "expression": "happy", "prop": "coffee_cup", "attire": "shirt"},
    },
    "Singapore": {
        "joke_text": "Entire hospital is spotless, run by AI robots. Caught chewing gum: 5 years in prison.",
        "setting": "hyper-clean futuristic Singapore medical hub",
        "character": {"age": 31, "gender": "female", "race": "light", "expression": "neutral", "prop": "clipboard", "attire": "jacket"},
    },
    "Finland": {
        "joke_text": "Hospital sauna has 6 patients discussing existential dread in complete silence.",
        "setting": "wooden pine sauna inside Finnish rehabilitation hospital",
        "character": {"age": 43, "gender": "male", "race": "pale", "expression": "neutral", "prop": "none", "attire": "shirt"},
    },
    "Norway": {
        "joke_text": "Told doctor he feels depressed in winter. Doctor hands him skis and says 'go outside'.",
        "setting": "panoramic fjord clinic consultation office",
        "character": {"age": 35, "gender": "female", "race": "pale", "expression": "annoyed", "prop": "clipboard", "attire": "coat"},
    },
    "Egypt": {
        "joke_text": "Pharmacist diagnoses your broken leg, fixes your phone, and sells you mint tea.",
        "setting": "busy Cairo street corner pharmacy",
        "character": {"age": 46, "gender": "male", "race": "tan", "expression": "smug", "prop": "tea_cup", "attire": "shirt"},
    },
    "China": {
        "joke_text": "Doctor prescribes IV drip, boiled hot water, and herbal tea for a small paper cut.",
        "setting": "bustling metropolitan Chinese hospital hall",
        "character": {"age": 37, "gender": "male", "race": "light", "expression": "neutral", "prop": "tea_cup", "attire": "lab_coat"},
    },
    "Argentina": {
        "joke_text": "Surgeon analyzes your MRI while arguing about Lionel Messi's positioning against Colombia.",
        "setting": "Buenos Aires university hospital corridor",
        "character": {"age": 41, "gender": "male", "race": "tan", "expression": "annoyed", "prop": "clipboard", "attire": "jersey"},
    },
    "South Africa": {
        "joke_text": "Loadshedding hits midway through surgery. Team switches seamlessly to phone flashlights and braai banter.",
        "setting": "operating theater with battery-powered backup lamps",
        "character": {"age": 33, "gender": "male", "race": "dark", "expression": "smug", "prop": "phone", "attire": "lab_coat"},
    },
    "Austria": {
        "joke_text": "Doctor insists your anxiety is an unresolved Freudian Oedipal trauma and prescribes Sachertorte.",
        "setting": "grand high-ceilinged Viennese consulting room",
        "character": {"age": 48, "gender": "male", "race": "pale", "expression": "smug", "prop": "pastry", "attire": "jacket"},
    },
    "Belgium": {
        "joke_text": "Hospital is divided in half: Flemish side gives bandages, Walloon side demands a referendum first.",
        "setting": "bilingual Brussels central hospital lobby",
        "character": {"age": 38, "gender": "female", "race": "fair", "expression": "annoyed", "prop": "sign", "attire": "jacket"},
    },
    "New Zealand": {
        "joke_text": "Came in with a broken arm from rugby. Doctor is barefoot and offers you an ice lolly.",
        "setting": "laid-back seaside Kiwi clinic",
        "character": {"age": 27, "gender": "male", "race": "tan", "expression": "happy", "prop": "ball", "attire": "jersey"},
    },
    "Philippines": {
        "joke_text": "All the nurses in the country have moved to Texas or London. Remaining staff is 90% student interns.",
        "setting": "crowded Manila provincial ward",
        "character": {"age": 23, "gender": "female", "race": "tan", "expression": "tired", "prop": "clipboard", "attire": "lab_coat"},
    },
    "Thailand": {
        "joke_text": "Medical tourism hospital looks like a 7-star palace with gold chandeliers and an infinity pool.",
        "setting": "palatial Bangkok private medical center atrium",
        "character": {"age": 30, "gender": "female", "race": "tan", "expression": "happy", "prop": "tea_cup", "attire": "gown"},
    },
    "Vietnam": {
        "joke_text": "Patient's entire village parked their motorbikes outside and brought 12 pots of pho to the ward.",
        "setting": "bustling Hanoi general hospital courtyard",
        "character": {"age": 32, "gender": "male", "race": "tan", "expression": "happy", "prop": "pasta_pot", "attire": "shirt"},
    },
    "Romania": {
        "joke_text": "Gave the doctor chocolate, coffee, and an envelope of cash just to get a fresh thermometer.",
        "setting": "county hospital reception desk",
        "character": {"age": 45, "gender": "male", "race": "fair", "expression": "smug", "prop": "coffee_cup", "attire": "jacket"},
    },
}

_PUBLIC_TRANSPORT_JOKES: dict[str, dict[str, Any]] = {
    "Japan": {
        "joke_text": "Train was 12 seconds late. Railway president holds a televised weeping press conference.",
        "setting": "pristine Tokyo Shinkansen platform",
        "character": {"age": 48, "gender": "male", "race": "light", "expression": "shocked", "prop": "bag", "attire": "jacket"},
    },
    "Germany": {
        "joke_text": "Deutsche Bahn announces a 140-minute delay due to a slightly misplaced leaf on the track.",
        "setting": "crowded Frankfurt central station platform display",
        "character": {"age": 38, "gender": "male", "race": "fair", "expression": "annoyed", "prop": "clipboard", "attire": "coat"},
    },
    "India": {
        "joke_text": "Train officially has capacity for 800 passengers. 3,400 people are currently riding on the roof.",
        "setting": "packed Mumbai suburban train platform",
        "character": {"age": 27, "gender": "male", "race": "brown", "expression": "smug", "prop": "phone", "attire": "shirt"},
    },
    "UK": {
        "joke_text": "Rail replacement bus service for a train that was already replacing a cancelled bus.",
        "setting": "rain-soaked London bus shelter",
        "character": {"age": 40, "gender": "female", "race": "fair", "expression": "tired", "prop": "tea_cup", "attire": "coat"},
    },
    "USA": {
        "joke_text": "Metro has 2 lines, hasn't been cleaned since 1987, and someone is preaching in car 4.",
        "setting": "dimly lit New York subway platform with graffiti",
        "character": {"age": 30, "gender": "male", "race": "fair", "expression": "shocked", "prop": "phone", "attire": "jacket"},
    },
    "France": {
        "joke_text": "Metro on strike every Tuesday, Thursday, and whenever the weather is slightly overcast.",
        "setting": "shuttered Paris metro entrance with protest banner",
        "character": {"age": 35, "gender": "male", "race": "light", "expression": "annoyed", "prop": "sign", "attire": "jacket"},
    },
    "Italy": {
        "joke_text": "Bus schedule is purely decorative philosophy. The driver stopped to buy an espresso.",
        "setting": "historic Rome street corner bus stop",
        "character": {"age": 44, "gender": "male", "race": "light", "expression": "happy", "prop": "coffee_cup", "attire": "shirt"},
    },
    "Sweden": {
        "joke_text": "If another passenger sits less than 4 seats away from you, it's considered an act of war.",
        "setting": "spacious Stockholm metro carriage with silent riders",
        "character": {"age": 28, "gender": "female", "race": "pale", "expression": "neutral", "prop": "phone", "attire": "jacket"},
    },
    "Egypt": {
        "joke_text": "Microbus driver overtakes on the shoulder at 130 km/h while counting coins and texting with his knee.",
        "setting": "chaotic Cairo ring road highway",
        "character": {"age": 39, "gender": "male", "race": "tan", "expression": "smug", "prop": "tea_cup", "attire": "shirt"},
    },
    "Switzerland": {
        "joke_text": "Train arrives at 08:14:02. Passengers check their luxury watches in disappointment at the 2-second error.",
        "setting": "spotless Zurich mountain railway platform",
        "character": {"age": 46, "gender": "male", "race": "fair", "expression": "annoyed", "prop": "bill", "attire": "jacket"},
    },
    "Netherlands": {
        "joke_text": "Bicycle traffic jam with 600 commuters in pouring rain, not a single umbrella in sight.",
        "setting": "Amsterdam central canal bike intersection",
        "character": {"age": 29, "gender": "male", "race": "fair", "expression": "neutral", "prop": "bag", "attire": "jacket"},
    },
    "Brazil": {
        "joke_text": "Bus taking corners on two wheels. Passengers holding on for dear life while the radio blasts funk.",
        "setting": "winding hillside Rio bus route",
        "character": {"age": 25, "gender": "male", "race": "tan", "expression": "happy", "prop": "phone", "attire": "jersey"},
    },
    "Australia": {
        "joke_text": "Bus delayed because a kangaroo is having a standoff with the driver on the highway.",
        "setting": "sun-bleached Sydney suburban road stop",
        "character": {"age": 32, "gender": "female", "race": "tan", "expression": "neutral", "prop": "clipboard", "attire": "shirt"},
    },
    "Canada": {
        "joke_text": "Bus is 20 minutes late in -30°C blizzard. Passengers apologize to the driver for waiting.",
        "setting": "frozen Montreal bus stop covered in 3 feet of snow",
        "character": {"age": 31, "gender": "male", "race": "fair", "expression": "tired", "prop": "coffee_cup", "attire": "coat"},
    },
    "South Korea": {
        "joke_text": "Subway has 5G, heated seats, and 400 people simultaneously silent-scrolling webtoons.",
        "setting": "hyper-modern Seoul subway car interior",
        "character": {"age": 24, "gender": "female", "race": "light", "expression": "neutral", "prop": "phone", "attire": "jacket"},
    },
    "Singapore": {
        "joke_text": "Train arrives every 90 seconds. Dropped a piece of lint on the floor: $500 fine.",
        "setting": "gleaming air-conditioned MRT station platform",
        "character": {"age": 33, "gender": "female", "race": "light", "expression": "shocked", "prop": "clipboard", "attire": "jacket"},
    },
    "Spain": {
        "joke_text": "Metro platform at 1 AM has toddlers, grandparents, and street musicians starting a party.",
        "setting": "vibrant Madrid metro platform at midnight",
        "character": {"age": 27, "gender": "male", "race": "tan", "expression": "happy", "prop": "phone", "attire": "shirt"},
    },
    "Mexico": {
        "joke_text": "Metro pesero has flashing neon lights, a subwoofer bigger than the engine, and a driver giving life advice.",
        "setting": "colorful Mexico City minibus interior",
        "character": {"age": 42, "gender": "male", "race": "tan", "expression": "happy", "prop": "bag", "attire": "shirt"},
    },
    "Poland": {
        "joke_text": "Ticket inspector materializes out of thin air the exact millisecond the doors close.",
        "setting": "Warsaw tram car interior with fare machine",
        "character": {"age": 47, "gender": "male", "race": "fair", "expression": "smug", "prop": "clipboard", "attire": "jacket"},
    },
    "Turkey": {
        "joke_text": "Dolmuş driver collects coins from the back row through 12 strangers without dropping a single lira.",
        "setting": "crowded yellow Istanbul shared minibus",
        "character": {"age": 36, "gender": "male", "race": "tan", "expression": "smug", "prop": "tea_cup", "attire": "shirt"},
    },
    "Greece": {
        "joke_text": "Ferry boarding is controlled chaos with 500 people screaming 'ELA ELA' at reversing trucks.",
        "setting": "sun-drenched Piraeus harbor ferry ramp",
        "character": {"age": 45, "gender": "male", "race": "tan", "expression": "shocked", "prop": "coffee_cup", "attire": "shirt"},
    },
    "Ireland": {
        "joke_text": "Bus driver thanks the passengers. Passengers thank the driver. Everyone apologizes for existing.",
        "setting": "double-decker Dublin bus exit door",
        "character": {"age": 29, "gender": "female", "race": "pale", "expression": "happy", "prop": "bag", "attire": "coat"},
    },
    "Finland": {
        "joke_text": "10 people waiting for the bus, spaced exactly 8 meters apart in knee-deep snow.",
        "setting": "snowy Helsinki bus stop in sub-zero twilight",
        "character": {"age": 37, "gender": "male", "race": "pale", "expression": "neutral", "prop": "none", "attire": "coat"},
    },
    "Norway": {
        "joke_text": "Electric bus ran out of battery in the fjord. Passengers stoically hike 12 km home in cross-country skis.",
        "setting": "picturesque icy fjord road with stopped electric bus",
        "character": {"age": 34, "gender": "female", "race": "pale", "expression": "neutral", "prop": "bag", "attire": "coat"},
    },
    "Portugal": {
        "joke_text": "Vintage tram rattles so hard your dental fillings come loose. Tourists love it.",
        "setting": "steep historic Lisbon cobblestone tram route",
        "character": {"age": 55, "gender": "male", "race": "tan", "expression": "tired", "prop": "pastry", "attire": "shirt"},
    },
    "China": {
        "joke_text": "Bullet train does 350 km/h silently while facial recognition scans your ticket and pays for your tea.",
        "setting": "futuristic Beijing high-speed railway concourse",
        "character": {"age": 31, "gender": "female", "race": "light", "expression": "smug", "prop": "phone", "attire": "jacket"},
    },
    "Argentina": {
        "joke_text": "Subte metro car built in 1913 still running. Lights flicker every time the train takes a turn.",
        "setting": "antique wooden Buenos Aires subway car",
        "character": {"age": 38, "gender": "male", "race": "tan", "expression": "tired", "prop": "bag", "attire": "jacket"},
    },
    "South Africa": {
        "joke_text": "Minibus taxi flashes hand signals to show destination. Driver invents a 4th lane on the pavement.",
        "setting": "bustling Johannesburg taxi rank",
        "character": {"age": 30, "gender": "male", "race": "dark", "expression": "smug", "prop": "phone", "attire": "shirt"},
    },
    "Austria": {
        "joke_text": "Tram driver rings the bell with furious etiquette if anyone hesitates at the doors for 0.3 seconds.",
        "setting": "Viennese Ringstrasse historic tram stop",
        "character": {"age": 49, "gender": "male", "race": "pale", "expression": "annoyed", "prop": "pastry", "attire": "jacket"},
    },
    "Belgium": {
        "joke_text": "Train announcement switched languages midway and now passengers on both sides are deeply offended.",
        "setting": "Brussels South train station bilingual concourse",
        "character": {"age": 37, "gender": "female", "race": "fair", "expression": "annoyed", "prop": "bag", "attire": "jacket"},
    },
    "New Zealand": {
        "joke_text": "Entire town bus network consists of one bus named Dave who stops to let ducks cross.",
        "setting": "green rural Kiwi country road stop",
        "character": {"age": 28, "gender": "male", "race": "tan", "expression": "happy", "prop": "ball", "attire": "jersey"},
    },
    "Philippines": {
        "joke_text": "Jeepney packed with 28 people, 3 chickens, and a boombox blasting 90s love ballads at max volume.",
        "setting": "neon-painted Manila jeepney road stop",
        "character": {"age": 26, "gender": "female", "race": "tan", "expression": "happy", "prop": "phone", "attire": "shirt"},
    },
    "Thailand": {
        "joke_text": "Bangkok BTS sky train freezing cold inside, 42°C outside. Tuk-tuk offers to weave through 4 traffic jams.",
        "setting": "elevated Bangkok BTS Skytrain concourse",
        "character": {"age": 29, "gender": "female", "race": "tan", "expression": "happy", "prop": "tea_cup", "attire": "shirt"},
    },
    "Vietnam": {
        "joke_text": "Public bus exists, but everyone prefers joining the swarm of 4 million synchronized scooters.",
        "setting": "roaring Saigon scooter intersection roundabout",
        "character": {"age": 27, "gender": "male", "race": "tan", "expression": "smug", "prop": "bag", "attire": "jacket"},
    },
    "Romania": {
        "joke_text": "Maxitaxi driver crosses 3 lanes with no signals while negotiating a business deal over speakerphone.",
        "setting": "Bucharest suburban bus station curb",
        "character": {"age": 43, "gender": "male", "race": "fair", "expression": "smug", "prop": "phone", "attire": "jacket"},
    },
}

_SCHOOL_JOKES: dict[str, dict[str, Any]] = {
    "South Korea": {
        "joke_text": "Finished regular school at 4 PM. Now attending Hagwon #1, Hagwon #2, and Hagwon #3 until 2 AM.",
        "setting": "nighttime Gangnam cram school hallway",
        "character": {"age": 16, "gender": "female", "race": "light", "expression": "tired", "prop": "phone", "attire": "jacket"},
    },
    "India": {
        "joke_text": "Scored 99.2% on the national board exam. Father asks where the remaining 0.8% went.",
        "setting": "study room stacked with engineering prep books",
        "character": {"age": 17, "gender": "male", "race": "brown", "expression": "shocked", "prop": "clipboard", "attire": "shirt"},
    },
    "USA": {
        "joke_text": "High school looks like a college campus. 40% of the municipal budget went to the football stadium.",
        "setting": "sprawling Texas high school football field bleachers",
        "character": {"age": 17, "gender": "male", "race": "fair", "expression": "smug", "prop": "ball", "attire": "jersey"},
    },
    "Japan": {
        "joke_text": "Students stay 2 hours after school on hands and knees scrubbing the hallway floors with toothbrushes.",
        "setting": "immaculate Japanese wooden classroom corridor",
        "character": {"age": 15, "gender": "male", "race": "light", "expression": "tired", "prop": "bag", "attire": "jacket"},
    },
    "Germany": {
        "joke_text": "At age 10, a standardized exam decides whether you become an astrophysicist or a bricklayer.",
        "setting": "austere German Gymnasium classroom desk",
        "character": {"age": 14, "gender": "female", "race": "fair", "expression": "neutral", "prop": "clipboard", "attire": "shirt"},
    },
    "France": {
        "joke_text": "3-course gourmet school lunch with brie and ratatouille. Followed by a 4-hour philosophy essay.",
        "setting": "Parisian lycée dining hall with white tablecloths",
        "character": {"age": 17, "gender": "male", "race": "light", "expression": "smug", "prop": "pastry", "attire": "jacket"},
    },
    "UK": {
        "joke_text": "School uniform includes a wool blazer and striped tie in 30°C heat. Heating permanently stuck on full blast.",
        "setting": "Victorian red-brick British school assembly hall",
        "character": {"age": 16, "gender": "male", "race": "fair", "expression": "tired", "prop": "bag", "attire": "jacket"},
    },
    "Finland": {
        "joke_text": "No homework, 3 hours of school a day, kids play in the forest. Scores #1 in global education rankings.",
        "setting": "sunlit Finnish forest school playground",
        "character": {"age": 14, "gender": "female", "race": "pale", "expression": "happy", "prop": "none", "attire": "jacket"},
    },
    "Italy": {
        "joke_text": "High school building was constructed in 1420. Heating broken since the Renaissance. Mom packs a focaccia.",
        "setting": "ancient stone Italian liceo classroom with vaulted arches",
        "character": {"age": 16, "gender": "male", "race": "light", "expression": "happy", "prop": "pasta_pot", "attire": "shirt"},
    },
    "China": {
        "joke_text": "Gaokao preparation started in kindergarten. Student drinks energy tonic while solving differential equations.",
        "setting": "intense high school study hall with motivational red banners",
        "character": {"age": 17, "gender": "male", "race": "light", "expression": "tired", "prop": "tea_cup", "attire": "shirt"},
    },
    "Australia": {
        "joke_text": "School assembly cancelled because an eastern brown snake is sunbathing by the drinking taps.",
        "setting": "Queensland schoolyard under corrugated iron shed",
        "character": {"age": 15, "gender": "male", "race": "tan", "expression": "neutral", "prop": "ball", "attire": "shirt"},
    },
    "Canada": {
        "joke_text": "Recess at -25°C. Teachers say 'put on your snow pants, it builds character'.",
        "setting": "frozen school playground covered in snow drifts",
        "character": {"age": 14, "gender": "female", "race": "fair", "expression": "tired", "prop": "bag", "attire": "coat"},
    },
    "Poland": {
        "joke_text": "Change from outdoor shoes into indoor slippers (kapcie) or face execution by the school janitor.",
        "setting": "tiled school locker room entrance with strict guard",
        "character": {"age": 15, "gender": "male", "race": "fair", "expression": "shocked", "prop": "bag", "attire": "shirt"},
    },
    "Mexico": {
        "joke_text": "Monday morning flag ceremony under 38°C sun. Half the class passes out from patriotic heatstroke.",
        "setting": "concrete school courtyard under blazing sun",
        "character": {"age": 15, "gender": "male", "race": "tan", "expression": "tired", "prop": "pennant", "attire": "shirt"},
    },
    "Ireland": {
        "joke_text": "Run by nuns until 1998. The ghost of Sister Mary still haunts the chemistry lab.",
        "setting": "imposing grey stone convent school corridor",
        "character": {"age": 16, "gender": "female", "race": "pale", "expression": "shocked", "prop": "tea_cup", "attire": "jacket"},
    },
    "Sweden": {
        "joke_text": "Students call the teacher by their first name and negotiate homework deadlines over cinnamon buns.",
        "setting": "modern airy Swedish school common room",
        "character": {"age": 16, "gender": "male", "race": "pale", "expression": "happy", "prop": "pastry", "attire": "shirt"},
    },
    "Turkey": {
        "joke_text": "Morning national oath shouted at 120 decibels. Bell rings and everyone drinks cay.",
        "setting": "school courtyard packed with lined-up students",
        "character": {"age": 15, "gender": "male", "race": "tan", "expression": "smug", "prop": "tea_cup", "attire": "shirt"},
    },
    "Brazil": {
        "joke_text": "School ends at noon. Everyone goes straight to the beach with backpacks on.",
        "setting": "sunny street outside coastal school gate",
        "character": {"age": 16, "gender": "female", "race": "tan", "expression": "happy", "prop": "ball", "attire": "shirt"},
    },
    "Spain": {
        "joke_text": "Lunch break is 2 hours long so students can go home, eat paella, and take a nap.",
        "setting": "bright Spanish instituto courtyard",
        "character": {"age": 16, "gender": "male", "race": "tan", "expression": "happy", "prop": "phone", "attire": "shirt"},
    },
    "Netherlands": {
        "joke_text": "Kids bike 15 km to school against 40 knot headwind while eating chocolate sprinkles on bread.",
        "setting": "Dutch bike parking lot with 2,000 stacked bikes",
        "character": {"age": 15, "gender": "female", "race": "fair", "expression": "neutral", "prop": "pastry", "attire": "jacket"},
    },
    "Switzerland": {
        "joke_text": "Students walk home for lunch and fondue. Private school tuition costs more than an island.",
        "setting": "pristine Alpine campus grounds",
        "character": {"age": 17, "gender": "male", "race": "fair", "expression": "smug", "prop": "bill", "attire": "jacket"},
    },
    "Egypt": {
        "joke_text": "Private tutoring center has 800 students in an amphitheater with the teacher on a wireless microphone.",
        "setting": "packed Egyptian lecture amphitheater",
        "character": {"age": 17, "gender": "male", "race": "tan", "expression": "shocked", "prop": "clipboard", "attire": "shirt"},
    },
    "Greece": {
        "joke_text": "School year delayed by 3 weeks because the textbook shipment is stuck in Piraeus port.",
        "setting": "Mediterranean school patio overlooking olive trees",
        "character": {"age": 16, "gender": "male", "race": "tan", "expression": "happy", "prop": "coffee_cup", "attire": "shirt"},
    },
    "Singapore": {
        "joke_text": "Parent signed the child up for coding olympiad before ultrasound revealed the gender.",
        "setting": "high-tech Singapore primary school computer laboratory",
        "character": {"age": 14, "gender": "female", "race": "light", "expression": "smug", "prop": "clipboard", "attire": "jacket"},
    },
    "Portugal": {
        "joke_text": "History class spends 9 months explaining why Portugal discovered the entire universe first.",
        "setting": "classic Portuguese classroom with azulejo blue tiles",
        "character": {"age": 16, "gender": "male", "race": "tan", "expression": "smug", "prop": "pastry", "attire": "shirt"},
    },
    "Argentina": {
        "joke_text": "Class debate about inflation lasts 4 hours. Teacher and students agree currency is an illusion.",
        "setting": "aged Buenos Aires classroom with open windows",
        "character": {"age": 17, "gender": "male", "race": "tan", "expression": "annoyed", "prop": "clipboard", "attire": "shirt"},
    },
    "South Africa": {
        "joke_text": "Inter-schools rugby derby has 20,000 spectators screaming louder than the World Cup final.",
        "setting": "packed high school rugby stadium field",
        "character": {"age": 17, "gender": "male", "race": "dark", "expression": "happy", "prop": "ball", "attire": "jersey"},
    },
    "Austria": {
        "joke_text": "Music teacher can hear if your violin is 0.1 hertz out of tune from across the Danube.",
        "setting": "grand Viennese music academy practice hall",
        "character": {"age": 16, "gender": "female", "race": "pale", "expression": "tired", "prop": "bag", "attire": "jacket"},
    },
    "Belgium": {
        "joke_text": "History textbook exists in two contradictory versions depending on whether you take the bus north or south.",
        "setting": "Brussels high school library study desk",
        "character": {"age": 16, "gender": "male", "race": "fair", "expression": "annoyed", "prop": "clipboard", "attire": "jacket"},
    },
    "New Zealand": {
        "joke_text": "Entire school does a spine-tingling Haka before the math test to intimidate the quadratic equations.",
        "setting": "lush green New Zealand school sports field",
        "character": {"age": 16, "gender": "male", "race": "tan", "expression": "smug", "prop": "ball", "attire": "jersey"},
    },
    "Philippines": {
        "joke_text": "School festival dance presentation practiced for 6 months. Choreography puts Broadway to shame.",
        "setting": "covered basketball court auditorium in Manila",
        "character": {"age": 15, "gender": "female", "race": "tan", "expression": "happy", "prop": "pennant", "attire": "gown"},
    },
    "Thailand": {
        "joke_text": "Morning uniform inspection includes checking if hair length violates ministerial code 14.B.",
        "setting": "tropical schoolyard with morning assembly rows",
        "character": {"age": 15, "gender": "male", "race": "tan", "expression": "neutral", "prop": "bag", "attire": "shirt"},
    },
    "Vietnam": {
        "joke_text": "Math teacher solves 10 complex calculus problems simultaneously on the blackboard without notes.",
        "setting": "Hanoi high school classroom with ceiling fans",
        "character": {"age": 16, "gender": "male", "race": "tan", "expression": "shocked", "prop": "clipboard", "attire": "shirt"},
    },
    "Romania": {
        "joke_text": "Olympiad math team wins 8 gold medals. School heating broke down in November.",
        "setting": "classic Romanian high school chemistry lab",
        "character": {"age": 17, "gender": "male", "race": "fair", "expression": "smug", "prop": "coffee_cup", "attire": "coat"},
    },
    "Colombia": {
        "joke_text": "English teacher spends first 20 minutes explaining why salsa rhythm is essential to grammar.",
        "setting": "breezy Medellin classroom overlooking green hills",
        "character": {"age": 16, "gender": "female", "race": "tan", "expression": "happy", "prop": "coffee_cup", "attire": "shirt"},
    },
}

_PARENTS_JOKES: dict[str, dict[str, Any]] = {
    "India": {
        "joke_text": "Sharma ji's son already has a master's degree at age 14. What are you doing with your life?",
        "setting": "traditional Indian living room sofa",
        "character": {"age": 50, "gender": "male", "race": "brown", "expression": "annoyed", "prop": "tea_cup", "attire": "shirt"},
    },
    "Italy": {
        "joke_text": "Son is 39 years old, lives at home, and mother still irons his socks and cuts the crust off his bread.",
        "setting": "sunlit Italian kitchen with simmering tomato sauce",
        "character": {"age": 62, "gender": "female", "race": "light", "expression": "happy", "prop": "pasta_pot", "attire": "gown"},
    },
    "USA": {
        "joke_text": "Mom tracks child's location with 4 apps, checks their emotional aura, and hires a sleep coach.",
        "setting": "open-concept suburban kitchen island",
        "character": {"age": 42, "gender": "female", "race": "fair", "expression": "shocked", "prop": "phone", "attire": "jacket"},
    },
    "South Korea": {
        "joke_text": "Mother sends child to English immersion kindergarten before they can speak Korean.",
        "setting": "meticulous Seoul apartment living room",
        "character": {"age": 44, "gender": "female", "race": "light", "expression": "neutral", "prop": "phone", "attire": "jacket"},
    },
    "Germany": {
        "joke_text": "Child falls from a 3-meter climbing frame. Mother calmly notes: 'Der Schmerz ist ein Lernprozess' (Pain is a learning process).",
        "setting": "natural wooden German park playground",
        "character": {"age": 39, "gender": "female", "race": "fair", "expression": "neutral", "prop": "coffee_cup", "attire": "jacket"},
    },
    "Mexico": {
        "joke_text": "Mom can hit a moving target around two corners with a flying flip-flop (La Chancla) with sniper precision.",
        "setting": "Mexican family dining patio",
        "character": {"age": 54, "gender": "female", "race": "tan", "expression": "annoyed", "prop": "pasta_pot", "attire": "gown"},
    },
    "UK": {
        "joke_text": "Parent's highest form of praise for getting into Oxford: 'Not too bad, I suppose.'",
        "setting": "cozy British living room with electric fire",
        "character": {"age": 52, "gender": "male", "race": "fair", "expression": "neutral", "prop": "tea_cup", "attire": "jacket"},
    },
    "France": {
        "joke_text": "Children eat Roquefort cheese at age 2 without crying. Tantrums in public are socially illegal.",
        "setting": "chic Paris bistro outdoor terrace",
        "character": {"age": 41, "gender": "female", "race": "light", "expression": "smug", "prop": "pastry", "attire": "coat"},
    },
    "Japan": {
        "joke_text": "Parent sends 6-year-old on the Tokyo subway alone with a water bottle and a tiny yellow hat.",
        "setting": "tidy Tokyo residential street entrance",
        "character": {"age": 40, "gender": "female", "race": "light", "expression": "happy", "prop": "bag", "attire": "jacket"},
    },
    "Poland": {
        "joke_text": "Put on a hat or you will catch meningitis immediately! Also eat more potatoes.",
        "setting": "Polish apartment kitchen table covered with lace",
        "character": {"age": 65, "gender": "female", "race": "fair", "expression": "annoyed", "prop": "pasta_pot", "attire": "coat"},
    },
    "Turkey": {
        "joke_text": "Mom insists you are starving even though you just ate 4 plates of manti and a tray of baklava.",
        "setting": "warm Istanbul living room spread with dishes",
        "character": {"age": 56, "gender": "female", "race": "tan", "expression": "happy", "prop": "pastry", "attire": "gown"},
    },
    "Brazil": {
        "joke_text": "Mom introduces 14-year-old to the entire neighborhood as 'my little baby prince'.",
        "setting": "veranda overlooking vibrant Brazilian street",
        "character": {"age": 46, "gender": "female", "race": "tan", "expression": "happy", "prop": "phone", "attire": "shirt"},
    },
    "Ireland": {
        "joke_text": "Mother keeps the immersion heater turned off like it costs 10,000 euros a second to run.",
        "setting": "hallway airing cupboard in Dublin house",
        "character": {"age": 58, "gender": "female", "race": "pale", "expression": "shocked", "prop": "tea_cup", "attire": "shirt"},
    },
    "Sweden": {
        "joke_text": "Paternity leave is 480 days. Dads at the cafe pushing strollers and debating oat milk brands.",
        "setting": "hip Stockholm coffee shop window",
        "character": {"age": 34, "gender": "male", "race": "pale", "expression": "smug", "prop": "coffee_cup", "attire": "jacket"},
    },
    "Australia": {
        "joke_text": "Dad tells kid 'she'll be right mate' after kid gets bitten by three different venomous creatures.",
        "setting": "suburban Aussie backyard barbecue porch",
        "character": {"age": 47, "gender": "male", "race": "tan", "expression": "neutral", "prop": "tea_cup", "attire": "shirt"},
    },
    "Canada": {
        "joke_text": "Dad builds an NHL-regulation ice rink in the backyard so son can practice at 5 AM in -20°C.",
        "setting": "floodlit backyard ice rink in Ontario",
        "character": {"age": 45, "gender": "male", "race": "fair", "expression": "happy", "prop": "coffee_cup", "attire": "coat"},
    },
    "Greece": {
        "joke_text": "Mom calls 18 times during your 10-minute commute to check if you wore an undershirt.",
        "setting": "balcony in Athens filled with potted basil",
        "character": {"age": 60, "gender": "female", "race": "tan", "expression": "annoyed", "prop": "phone", "attire": "gown"},
    },
    "China": {
        "joke_text": "Piano practice for 6 hours daily. Second place in national competition brings dishonor upon the ancestry.",
        "setting": "living room with grand piano and framed certificates",
        "character": {"age": 48, "gender": "female", "race": "light", "expression": "annoyed", "prop": "tea_cup", "attire": "jacket"},
    },
    "Spain": {
        "joke_text": "Dinner with parents starts at 10:30 PM. 7-year-old child sipping Fanta at midnight with the family.",
        "setting": "bustling outdoor plaza restaurant table",
        "character": {"age": 43, "gender": "male", "race": "tan", "expression": "happy", "prop": "coffee_cup", "attire": "shirt"},
    },
    "Netherlands": {
        "joke_text": "Parents tell teenagers 'here is a condom and a bicycle, be home by midnight'.",
        "setting": "bright minimalist canal house hallway",
        "character": {"age": 49, "gender": "male", "race": "fair", "expression": "neutral", "prop": "bag", "attire": "shirt"},
    },
    "Egypt": {
        "joke_text": "Mom's response to any illness, injury, or emotional trauma: 'Drink hot lemon and say Bismillah'.",
        "setting": "warm Cairo family apartment sitting area",
        "character": {"age": 55, "gender": "female", "race": "tan", "expression": "happy", "prop": "tea_cup", "attire": "gown"},
    },
    "Portugal": {
        "joke_text": "Grandmother prepares a feast for 25 people when only you came to visit for 15 minutes.",
        "setting": "traditional kitchen with heavy ceramic pots",
        "character": {"age": 70, "gender": "female", "race": "tan", "expression": "happy", "prop": "pastry", "attire": "gown"},
    },
    "Switzerland": {
        "joke_text": "Kids must be completely silent indoors after 8 PM according to building code 402.B.",
        "setting": "spotless Alpine apartment building lobby",
        "character": {"age": 46, "gender": "male", "race": "fair", "expression": "neutral", "prop": "clipboard", "attire": "jacket"},
    },
    "Finland": {
        "joke_text": "Parent and child sit together in a sauna for 4 hours without uttering a single spoken word.",
        "setting": "lakeside wooden sauna bench",
        "character": {"age": 51, "gender": "male", "race": "pale", "expression": "neutral", "prop": "none", "attire": "shirt"},
    },
    "Singapore": {
        "joke_text": "Mom compares child's PSLE test score to every relative within a 5-generation family tree.",
        "setting": "air-conditioned HDB flat dining table",
        "character": {"age": 47, "gender": "female", "race": "light", "expression": "smug", "prop": "clipboard", "attire": "jacket"},
    },
    "Argentina": {
        "joke_text": "Father explains world economic collapse over Sunday mate while analyzing why his son doesn't pass the ball.",
        "setting": "asado grill patio in Buenos Aires",
        "character": {"age": 53, "gender": "male", "race": "tan", "expression": "annoyed", "prop": "tea_cup", "attire": "shirt"},
    },
    "South Africa": {
        "joke_text": "Dad teaches 10-year-old how to start a braai fire in 40 km/h wind before teaching them how to read.",
        "setting": "sun-drenched suburban lawn by the braai",
        "character": {"age": 48, "gender": "male", "race": "dark", "expression": "smug", "prop": "tea_cup", "attire": "shirt"},
    },
    "Austria": {
        "joke_text": "Father insists that proper table manners and shoe polishing prevent spiritual collapse.",
        "setting": "ornate Viennese dining room with polished parquet",
        "character": {"age": 56, "gender": "male", "race": "pale", "expression": "neutral", "prop": "pastry", "attire": "jacket"},
    },
    "Belgium": {
        "joke_text": "Parents compromise by sending one kid to Flemish school, one to French school, and arguing at dinner.",
        "setting": "bilingual townhouse dining room",
        "character": {"age": 45, "gender": "female", "race": "fair", "expression": "tired", "prop": "coffee_cup", "attire": "jacket"},
    },
    "New Zealand": {
        "joke_text": "Mum sees kid bleeding from rugby tackle: 'Rub some dirt in it, you're fine!'",
        "setting": "grass rugby sideline in rainy Auckland",
        "character": {"age": 44, "gender": "female", "race": "tan", "expression": "happy", "prop": "ball", "attire": "jersey"},
    },
    "Philippines": {
        "joke_text": "Mom points at everything with her lips and threatens to call the 'multo' ghost if you don't sleep.",
        "setting": "Manila living room with karaoke machine",
        "character": {"age": 52, "gender": "female", "race": "tan", "expression": "smug", "prop": "phone", "attire": "gown"},
    },
    "Thailand": {
        "joke_text": "Mom says 'Have you eaten rice yet?' as the universal substitute for 'I love you'.",
        "setting": "warm Bangkok kitchen filled with lemongrass scents",
        "character": {"age": 50, "gender": "female", "race": "tan", "expression": "happy", "prop": "pasta_pot", "attire": "shirt"},
    },
    "Vietnam": {
        "joke_text": "Dad tests kid on times tables while navigating through a 6-lane scooter roundabout.",
        "setting": "scooter seat in bustling Hanoi street",
        "character": {"age": 46, "gender": "male", "race": "tan", "expression": "smug", "prop": "bag", "attire": "jacket"},
    },
    "Romania": {
        "joke_text": "Grandmother will not let you leave until you eat soup, cabbage rolls, and half a pig.",
        "setting": "rustic Transylvanian kitchen table",
        "character": {"age": 67, "gender": "female", "race": "fair", "expression": "happy", "prop": "pasta_pot", "attire": "gown"},
    },
    "Colombia": {
        "joke_text": "Mom plays salsa music at 7 AM on Saturday as the official notification that cleaning has commenced.",
        "setting": "colorful sunny courtyard with mop and bucket",
        "character": {"age": 48, "gender": "female", "race": "tan", "expression": "happy", "prop": "coffee_cup", "attire": "shirt"},
    },
}

_DATING_JOKES: dict[str, dict[str, Any]] = {
    "France": {
        "joke_text": "No concept of 'dating'. You drink wine, discuss Sartre for 3 hours, and suddenly you've been married for 40 years.",
        "setting": "dimly lit Parisian wine cellar cafe",
        "character": {"age": 28, "gender": "male", "race": "light", "expression": "smug", "prop": "pastry", "attire": "jacket"},
    },
    "USA": {
        "joke_text": "Talking phase, texting phase, exclusive phase, defining the relationship, 3 therapy sessions, then ghosted.",
        "setting": "trendy rooftop bar in Los Angeles",
        "character": {"age": 27, "gender": "female", "race": "fair", "expression": "shocked", "prop": "phone", "attire": "jacket"},
    },
    "UK": {
        "joke_text": "Two pints of lager, awkward mumbling about the rain, 14 apologies, and an agreed trip to Nando's.",
        "setting": "dimly lit corner booth in a London pub",
        "character": {"age": 29, "gender": "male", "race": "fair", "expression": "tired", "prop": "tea_cup", "attire": "jacket"},
    },
    "Italy": {
        "joke_text": "Brings 3 dozen red roses on the first date. Mom already approved her Instagram profile.",
        "setting": "cobblestone piazza trattoria table with candles",
        "character": {"age": 30, "gender": "male", "race": "light", "expression": "happy", "prop": "pasta_pot", "attire": "shirt"},
    },
    "Japan": {
        "joke_text": "Confesses love formally with a 90-degree bow after meeting twice in a themed cat cafe.",
        "setting": "quiet Tokyo park bench under cherry blossoms",
        "character": {"age": 25, "gender": "male", "race": "light", "expression": "shocked", "prop": "bag", "attire": "jacket"},
    },
    "South Korea": {
        "joke_text": "Matching outfits, couple phone cases, and celebrating the 100-day anniversary with professional photographers.",
        "setting": "pastel aesthetic cafe in Hongdae",
        "character": {"age": 23, "gender": "female", "race": "light", "expression": "happy", "prop": "phone", "attire": "jacket"},
    },
    "Germany": {
        "joke_text": "First date split 50/50 to the exact cent (€14.37 each). Calendar invite sent for date #2 next month.",
        "setting": "orderly beer garden wooden bench",
        "character": {"age": 31, "gender": "male", "race": "fair", "expression": "neutral", "prop": "clipboard", "attire": "shirt"},
    },
    "Sweden": {
        "joke_text": "Go for fika. If both survive 45 minutes of silent staring, you move in together to assemble IKEA furniture.",
        "setting": "chic minimalist cafe in Gamla Stan",
        "character": {"age": 28, "gender": "female", "race": "pale", "expression": "neutral", "prop": "coffee_cup", "attire": "shirt"},
    },
    "India": {
        "joke_text": "First date includes 14 aunties, a horoscope astrological compatibility chart, and background check by an uncle.",
        "setting": "upscale hotel restaurant lounge",
        "character": {"age": 27, "gender": "female", "race": "brown", "expression": "shocked", "prop": "phone", "attire": "gown"},
    },
    "Brazil": {
        "joke_text": "Met at a street carnival at 11 PM. By 2 AM they are planning their destination wedding in Rio.",
        "setting": "Copacabana beach promenade under lamplight",
        "character": {"age": 25, "gender": "male", "race": "tan", "expression": "happy", "prop": "phone", "attire": "jersey"},
    },
    "Netherlands": {
        "joke_text": "Bikes to the date in a storm. Splits a single stroopwafel via Tikkie payment request for €1.15.",
        "setting": "cozy canal-side cafe entrance",
        "character": {"age": 29, "gender": "male", "race": "fair", "expression": "smug", "prop": "phone", "attire": "jacket"},
    },
    "Australia": {
        "joke_text": "Date at the local pub. Compliment: 'You're not a bad sort, eh?' Splitting a chicken parma and chips.",
        "setting": "beer garden in Melbourne pub",
        "character": {"age": 28, "gender": "male", "race": "tan", "expression": "happy", "prop": "tea_cup", "attire": "shirt"},
    },
    "Spain": {
        "joke_text": "Date starts at 11:30 PM. Tapas crawl until 4 AM. First kiss happens during churros at sunrise.",
        "setting": "vibrant narrow alley in Seville with fairy lights",
        "character": {"age": 26, "gender": "female", "race": "tan", "expression": "happy", "prop": "pastry", "attire": "shirt"},
    },
    "Mexico": {
        "joke_text": "Hires a 7-piece Mariachi band to serenade outside her window on a Tuesday at 3 AM.",
        "setting": "cobblestone street outside colonial balcony",
        "character": {"age": 28, "gender": "male", "race": "tan", "expression": "happy", "prop": "ball", "attire": "jacket"},
    },
    "Poland": {
        "joke_text": "Brings an odd number of flowers because even numbers are for funerals. Babcia inspects from the window.",
        "setting": "flower kiosk in Krakow main square",
        "character": {"age": 29, "gender": "male", "race": "fair", "expression": "shocked", "prop": "pastry", "attire": "jacket"},
    },
    "Ireland": {
        "joke_text": "Shifted in Coppers nightclub at 3 AM. Neither remembers what the other looks like in daylight.",
        "setting": "crowded Dublin dancefloor with flashing lights",
        "character": {"age": 26, "gender": "male", "race": "pale", "expression": "happy", "prop": "tea_cup", "attire": "shirt"},
    },
    "Canada": {
        "joke_text": "Spent 40 minutes arguing over who gets to pay the bill because each wants to be more polite.",
        "setting": "cozy diner booth in Toronto",
        "character": {"age": 28, "gender": "female", "race": "fair", "expression": "tired", "prop": "bill", "attire": "jacket"},
    },
    "Greece": {
        "joke_text": "Date involves meeting her entire extended village and answering questions about your grandfather's olive grove.",
        "setting": "island seaside taverna with blue chairs",
        "character": {"age": 32, "gender": "male", "race": "tan", "expression": "smug", "prop": "coffee_cup", "attire": "shirt"},
    },
    "Turkey": {
        "joke_text": "Reading coffee grounds to see if the spirits predict marriage within the next lunar cycle.",
        "setting": "Bosphorus-view cafe with ornate copper cups",
        "character": {"age": 27, "gender": "female", "race": "tan", "expression": "smug", "prop": "tea_cup", "attire": "shirt"},
    },
    "Singapore": {
        "joke_text": "First romantic conversation: 'So... do you want to apply for a BTO government flat together?'",
        "setting": "Gardens by the Bay promenade walkway",
        "character": {"age": 28, "gender": "male", "race": "light", "expression": "smug", "prop": "clipboard", "attire": "jacket"},
    },
    "Finland": {
        "joke_text": "Stood next to each other at the bus stop for 3 years. Finally exchanged a subtle nod. Relationship official.",
        "setting": "snowy park bench in Helsinki",
        "character": {"age": 31, "gender": "male", "race": "pale", "expression": "neutral", "prop": "none", "attire": "coat"},
    },
    "Switzerland": {
        "joke_text": "Both bring signed financial liability disclosures and proof of health insurance to the fondue date.",
        "setting": "high-end Alpine chalet dining room",
        "character": {"age": 34, "gender": "male", "race": "fair", "expression": "smug", "prop": "bill", "attire": "jacket"},
    },
    "Egypt": {
        "joke_text": "Engaged for 3 years. Still can't sit in the living room without her 12-year-old brother chaperoning.",
        "setting": "formal Egyptian salon sitting room",
        "character": {"age": 27, "gender": "male", "race": "tan", "expression": "tired", "prop": "tea_cup", "attire": "shirt"},
    },
    "Norway": {
        "joke_text": "First date is a 24 km cross-country ski expedition up an icy mountain peak. No complaints allowed.",
        "setting": "windy mountain ridge ski track",
        "character": {"age": 29, "gender": "female", "race": "pale", "expression": "neutral", "prop": "bag", "attire": "coat"},
    },
    "Portugal": {
        "joke_text": "Invites date home to meet the mother on day 1. Mother feeds them bacalhau for 6 consecutive hours.",
        "setting": "sunlit Porto dining room table",
        "character": {"age": 28, "gender": "male", "race": "tan", "expression": "happy", "prop": "pastry", "attire": "shirt"},
    },
    "China": {
        "joke_text": "Matchmaking corner in People's Park: parents trade laminated resumes detailing salary, height, and apartment size.",
        "setting": "park path covered in matchmaking umbrellas",
        "character": {"age": 29, "gender": "female", "race": "light", "expression": "shocked", "prop": "clipboard", "attire": "jacket"},
    },
    "Argentina": {
        "joke_text": "First date consists of 4 hours of psychoanalyzing each other's childhoods over a bottle of Malbec.",
        "setting": "bohemian Palermo cafe terrace",
        "character": {"age": 30, "gender": "female", "race": "tan", "expression": "smug", "prop": "coffee_cup", "attire": "shirt"},
    },
    "South Africa": {
        "joke_text": "First date was ruined by Stage 6 loadshedding, but saved by candlelit braai and stargazing.",
        "setting": "candlelit garden patio during power outage",
        "character": {"age": 27, "gender": "male", "race": "dark", "expression": "happy", "prop": "phone", "attire": "shirt"},
    },
    "Austria": {
        "joke_text": "Courtship involves 6 months of exchanging polite polite titles before using first names over Melange coffee.",
        "setting": "grand traditional Vienna coffee house booth",
        "character": {"age": 32, "gender": "male", "race": "pale", "expression": "smug", "prop": "pastry", "attire": "jacket"},
    },
    "Belgium": {
        "joke_text": "First argument is whether Belgian fries are superior with samurai sauce or mayonnaise.",
        "setting": "late-night Brussels friterie counter",
        "character": {"age": 28, "gender": "male", "race": "fair", "expression": "annoyed", "prop": "pastry", "attire": "jacket"},
    },
    "New Zealand": {
        "joke_text": "Date at the beach: both turn up in flip-flops (jandals) and split a meat pie from the dairy.",
        "setting": "rugged black sand Kiwi beach",
        "character": {"age": 26, "gender": "male", "race": "tan", "expression": "happy", "prop": "pastry", "attire": "shirt"},
    },
    "Philippines": {
        "joke_text": "He brought his entire barkada (friend squad) on the first date for emotional and tactical backup.",
        "setting": "air-conditioned SM Mall of Asia food court",
        "character": {"age": 24, "gender": "female", "race": "tan", "expression": "happy", "prop": "phone", "attire": "shirt"},
    },
    "Thailand": {
        "joke_text": "Brought date to a street food cart. Both sweat profusely from Level 10 spicy papaya salad.",
        "setting": "lively Bangkok night market stall",
        "character": {"age": 26, "gender": "male", "race": "tan", "expression": "shocked", "prop": "tea_cup", "attire": "shirt"},
    },
    "Vietnam": {
        "joke_text": "Romantic scooter ride: she holds on with one hand while holding a giant potted kumquat tree with the other.",
        "setting": "Hanoi street lit by lanterns",
        "character": {"age": 25, "gender": "female", "race": "tan", "expression": "happy", "prop": "bag", "attire": "jacket"},
    },
    "Colombia": {
        "joke_text": "If you cannot dance salsa on the first date, you are disqualified by minute 15 with zero appeal.",
        "setting": "energetic Cali salsa club dancefloor",
        "character": {"age": 27, "gender": "female", "race": "tan", "expression": "smug", "prop": "coffee_cup", "attire": "gown"},
    },
}

_SUPERMARKETS_JOKES: dict[str, dict[str, Any]] = {
    "USA": {
        "joke_text": "Costco size: 400 rolls of toilet paper, a 55-gallon drum of mayonnaise, and a free sample of pizza.",
        "setting": "massive warehouse wholesale club aisle",
        "character": {"age": 35, "gender": "male", "race": "fair", "expression": "shocked", "prop": "bag", "attire": "jacket"},
    },
    "Germany": {
        "joke_text": "Cashier scans 180 items per second with sniper speed. If you don't pack your bag in 4 seconds, the queue executes you.",
        "setting": "high-speed Aldi checkout conveyor belt",
        "character": {"age": 38, "gender": "male", "race": "fair", "expression": "shocked", "prop": "clipboard", "attire": "jacket"},
    },
    "UK": {
        "joke_text": "'Unexpected item in bagging area.' Customer apologizes profusely to the self-checkout robot.",
        "setting": "Tesco self-checkout station with blinking yellow light",
        "character": {"age": 32, "gender": "female", "race": "fair", "expression": "tired", "prop": "tea_cup", "attire": "coat"},
    },
    "France": {
        "joke_text": "Cheese aisle has 300 varieties with distinct bacterial aromas. Baguette must be inspected with surgical precision.",
        "setting": "Carrefour gourmet cheese section",
        "character": {"age": 42, "gender": "male", "race": "light", "expression": "smug", "prop": "pastry", "attire": "coat"},
    },
    "Italy": {
        "joke_text": "Grandma spends 25 minutes gently tapping every cantaloupe to assess acoustic resonance.",
        "setting": "sunlit supermarket produce aisle",
        "character": {"age": 68, "gender": "female", "race": "light", "expression": "annoyed", "prop": "pasta_pot", "attire": "gown"},
    },
    "Japan": {
        "joke_text": "Apples individually wrapped in silky foam nets for $15 each. Cashier hands back change with both hands and bows.",
        "setting": "luxurious Tokyo department store basement food hall",
        "character": {"age": 34, "gender": "male", "race": "light", "expression": "shocked", "prop": "bag", "attire": "jacket"},
    },
    "Netherlands": {
        "joke_text": "Albert Heijn bonus card scan. Pay using pin only. Zero cash accepted, ever.",
        "setting": "compact blue Albert Heijn city store exit",
        "character": {"age": 28, "gender": "female", "race": "fair", "expression": "neutral", "prop": "phone", "attire": "shirt"},
    },
    "India": {
        "joke_text": "Kirana store uncle knows your entire family history and gives a free piece of ginger with every purchase.",
        "setting": "crammed neighborhood kirana grocery shop",
        "character": {"age": 55, "gender": "male", "race": "brown", "expression": "smug", "prop": "tea_cup", "attire": "shirt"},
    },
    "Sweden": {
        "joke_text": "Systembolaget alcohol store closes at 3 PM on Saturday. Entire town panicking in line like it's the apocalypse.",
        "setting": "orderly queue outside Swedish state liquor store",
        "character": {"age": 36, "gender": "male", "race": "pale", "expression": "tired", "prop": "bag", "attire": "jacket"},
    },
    "Poland": {
        "joke_text": "Biedronka pallets blocking every single aisle. Fire hazard, but butter is 40% off with the Moja Biedronka app.",
        "setting": "packed Biedronka aisle blocked by wooden pallets",
        "character": {"age": 45, "gender": "female", "race": "fair", "expression": "smug", "prop": "phone", "attire": "coat"},
    },
    "Australia": {
        "joke_text": "Woolies and Coles price gouging inquiry on the news. Self-checkout camera zooms in on your soul.",
        "setting": "supermarket self-service checkout area",
        "character": {"age": 30, "gender": "male", "race": "tan", "expression": "annoyed", "prop": "bill", "attire": "shirt"},
    },
    "South Korea": {
        "joke_text": "Free sample ladies aggressive marketing tactics rival Wall Street traders. Full 4-course meal in aisle 3.",
        "setting": "bustling E-Mart sample station corridor",
        "character": {"age": 27, "gender": "female", "race": "light", "expression": "happy", "prop": "phone", "attire": "jacket"},
    },
    "Canada": {
        "joke_text": "Loblaw boycott signs outside. A single bell pepper costs $8.50 plus provincial sales tax.",
        "setting": "bright Canadian supermarket produce section",
        "character": {"age": 33, "gender": "male", "race": "fair", "expression": "shocked", "prop": "bill", "attire": "coat"},
    },
    "Mexico": {
        "joke_text": "OXXO store has 2 cash registers. Register #2 has never been opened in human history.",
        "setting": "red-and-yellow OXXO convenience checkout counter",
        "character": {"age": 31, "gender": "male", "race": "tan", "expression": "tired", "prop": "bag", "attire": "shirt"},
    },
    "Brazil": {
        "joke_text": "Supermarket has an on-site churrascaria and a DJ playing sertanejo by the meat counter.",
        "setting": "festive Brazilian hypermarket meat department",
        "character": {"age": 32, "gender": "male", "race": "tan", "expression": "happy", "prop": "phone", "attire": "jersey"},
    },
    "Spain": {
        "joke_text": "Mercadona jingle 'Mercadoooona, Mercadona' permanently hard-coded into the collective Spanish consciousness.",
        "setting": "Mercadona brightly lit supermarket entrance",
        "character": {"age": 36, "gender": "female", "race": "tan", "expression": "happy", "prop": "bag", "attire": "shirt"},
    },
    "Switzerland": {
        "joke_text": "Migros and Coop battle for economic supremacy. A head of lettuce costs the equivalent of a kidney.",
        "setting": "pristine Swiss grocery produce display",
        "character": {"age": 42, "gender": "male", "race": "fair", "expression": "smug", "prop": "bill", "attire": "jacket"},
    },
    "Ireland": {
        "joke_text": "Tayto crisps and Kerrygold butter occupying 60% of shelf space. Centra chicken fillet roll counter has a queue out the door.",
        "setting": "busy Irish deli counter at lunchtime",
        "character": {"age": 28, "gender": "male", "race": "pale", "expression": "happy", "prop": "pastry", "attire": "jacket"},
    },
    "Greece": {
        "joke_text": "Feta cheese sold in 15 kg tin barrels. Fresh olive oil brought in by an uncle in an unlabelled plastic canister.",
        "setting": "traditional Greek cheese counter with tin containers",
        "character": {"age": 52, "gender": "male", "race": "tan", "expression": "smug", "prop": "pasta_pot", "attire": "shirt"},
    },
    "Turkey": {
        "joke_text": "Bazaar bargaining for 30 minutes over 500g of tomatoes. Seller and buyer hug and drink tea as brothers.",
        "setting": "vibrant covered neighborhood bazaar stall",
        "character": {"age": 48, "gender": "male", "race": "tan", "expression": "happy", "prop": "tea_cup", "attire": "shirt"},
    },
    "Finland": {
        "joke_text": "Alko store closes early. Customer leaves with a case of Lonkero in sub-zero silence.",
        "setting": "quiet Finnish shopping mall liquor branch",
        "character": {"age": 39, "gender": "male", "race": "pale", "expression": "neutral", "prop": "bag", "attire": "coat"},
    },
    "Norway": {
        "joke_text": "Everything costs 400 kroner. Sunday shopping is illegal. Norwegians drive across border to Sweden to buy bacon.",
        "setting": "expensive Oslo grocery store aisle",
        "character": {"age": 37, "gender": "female", "race": "pale", "expression": "annoyed", "prop": "bill", "attire": "coat"},
    },
    "Egypt": {
        "joke_text": "Grocer sends a basket on a rope down from the 5th floor balcony to retrieve bread and milk.",
        "setting": "narrow Cairo alley looking up at balconies",
        "character": {"age": 44, "gender": "male", "race": "tan", "expression": "smug", "prop": "bag", "attire": "shirt"},
    },
    "Singapore": {
        "joke_text": "Don Don Donki store plays theme song on a 24/7 infinite loop until your brain completely surrenders.",
        "setting": "loud neon Japanese mega store in Orchard Road",
        "character": {"age": 26, "gender": "female", "race": "light", "expression": "shocked", "prop": "bag", "attire": "shirt"},
    },
    "Portugal": {
        "joke_text": "Dried salted cod (bacalhau) section smells like Neptune's personal locker room. Everyone loves it.",
        "setting": "Pingo Doce supermarket dried fish counter",
        "character": {"age": 56, "gender": "male", "race": "tan", "expression": "happy", "prop": "pastry", "attire": "shirt"},
    },
    "China": {
        "joke_text": "Live seafood section looks like Monterey Bay Aquarium. Fish scooped directly into plastic bucket.",
        "setting": "bustling wet market style supermarket tank aisle",
        "character": {"age": 40, "gender": "female", "race": "light", "expression": "smug", "prop": "bag", "attire": "shirt"},
    },
    "Argentina": {
        "joke_text": "Prices updated twice a day with a label gun. Customers sprint down aisle to beat the 3 PM price hike.",
        "setting": "Buenos Aires supermarket aisle with price tags",
        "character": {"age": 38, "gender": "male", "race": "tan", "expression": "shocked", "prop": "clipboard", "attire": "shirt"},
    },
    "South Africa": {
        "joke_text": "Woolworths food section has biltong in 14 variations. Security guard greets you with warmth.",
        "setting": "upscale South African grocery deli section",
        "character": {"age": 33, "gender": "female", "race": "dark", "expression": "happy", "prop": "bag", "attire": "shirt"},
    },
    "Austria": {
        "joke_text": "Billa cashier politely asks if you have the jö bonus card before you can even exhale.",
        "setting": "neat Viennese Billa supermarket counter",
        "character": {"age": 35, "gender": "female", "race": "pale", "expression": "neutral", "prop": "card", "attire": "jacket"},
    },
    "Belgium": {
        "joke_text": "Beer aisle takes up 3 entire corridors with 800 artisanal abbey ales. Bread aisle has 4 baguettes.",
        "setting": "Belgian Delhaize supermarket beer alley",
        "character": {"age": 41, "gender": "male", "race": "fair", "expression": "happy", "prop": "tea_cup", "attire": "jacket"},
    },
    "New Zealand": {
        "joke_text": "Pak'nSave yellow theme everywhere. Guy shopping barefoot in board shorts while buying Whittakers chocolate.",
        "setting": "Pak'nSave warehouse grocery aisle",
        "character": {"age": 29, "gender": "male", "race": "tan", "expression": "happy", "prop": "pastry", "attire": "shirt"},
    },
    "Philippines": {
        "joke_text": "Supermarket plays Christmas carols in September. Spam and corned beef locked in glass security cases.",
        "setting": "SM Supermarket aisle decorated with tinsel",
        "character": {"age": 28, "gender": "female", "race": "tan", "expression": "happy", "prop": "phone", "attire": "shirt"},
    },
    "Thailand": {
        "joke_text": "7-Eleven every 40 meters. Toasty sandwich pressed in 60 seconds is the national culinary backbone.",
        "setting": "air-conditioned Bangkok 7-Eleven checkout counter",
        "character": {"age": 24, "gender": "male", "race": "tan", "expression": "happy", "prop": "pastry", "attire": "shirt"},
    },
    "Vietnam": {
        "joke_text": "Sidewalk vegetable vendor sets up shop directly in front of the giant modern supermarket entrance.",
        "setting": "street pavement outside modern department store",
        "character": {"age": 47, "gender": "female", "race": "tan", "expression": "smug", "prop": "bag", "attire": "shirt"},
    },
    "Colombia": {
        "joke_text": "Exito supermarket has live music, free coffee tastings, and fresh arepas grilled right in front of you.",
        "setting": "vibrant Colombian supermarket bakery section",
        "character": {"age": 30, "gender": "female", "race": "tan", "expression": "happy", "prop": "coffee_cup", "attire": "shirt"},
    },
}

_WINTER_JOKES: dict[str, dict[str, Any]] = {
    "Canada": {
        "joke_text": "-35°C blizzard. School is open. Guy in Tim Hortons drive-thru ordering an iced cappuccino in shorts.",
        "setting": "snowdrift-covered drive-thru lane",
        "character": {"age": 28, "gender": "male", "race": "fair", "expression": "happy", "prop": "coffee_cup", "attire": "jacket"},
    },
    "UK": {
        "joke_text": "2 millimeters of snow falls. Entire national rail network collapses. Schools shut down for a week.",
        "setting": "light dusting of snow on London train platform",
        "character": {"age": 38, "gender": "male", "race": "fair", "expression": "tired", "prop": "tea_cup", "attire": "coat"},
    },
    "Finland": {
        "joke_text": "-30°C in dark midday. Finn rides bicycle to work, takes an ice swim, and complains it's too warm.",
        "setting": "frozen lake with carved ice swimming hole",
        "character": {"age": 42, "gender": "male", "race": "pale", "expression": "neutral", "prop": "none", "attire": "shirt"},
    },
    "Russia": {
        "joke_text": "Car battery frozen solid. Warming the engine block with an open bonfire in the driveway.",
        "setting": "snow-covered Siberian courtyard driveway",
        "character": {"age": 46, "gender": "male", "race": "fair", "expression": "neutral", "prop": "bag", "attire": "coat"},
    },
    "USA": {
        "joke_text": "Supermarket shelves wiped completely clean of milk and bread because the weather app showed a snowflake emoji.",
        "setting": "empty grocery bread aisle before a storm",
        "character": {"age": 34, "gender": "female", "race": "fair", "expression": "shocked", "prop": "phone", "attire": "jacket"},
    },
    "Norway": {
        "joke_text": "Sun doesn't rise for 3 months. Everyone consumes 2 liters of cod liver oil daily and keeps skiing.",
        "setting": "snowy mountain ski cabin twilight",
        "character": {"age": 36, "gender": "female", "race": "pale", "expression": "smug", "prop": "clipboard", "attire": "coat"},
    },
    "Germany": {
        "joke_text": "Window tilting (Lüften) is strictly mandatory even at -15°C to achieve optimal air circulation.",
        "setting": "drafty Berlin apartment with wide open tilted window",
        "character": {"age": 40, "gender": "male", "race": "fair", "expression": "neutral", "prop": "coffee_cup", "attire": "jacket"},
    },
    "Sweden": {
        "joke_text": "Dressed in reflective neon vests like airport ground crew just to walk the dog in 2 PM pitch black.",
        "setting": "pitch black snowy suburban street lit only by streetlamp",
        "character": {"age": 32, "gender": "male", "race": "pale", "expression": "neutral", "prop": "bag", "attire": "coat"},
    },
    "Australia": {
        "joke_text": "Temperature drops to 16°C. Locals put on ski jackets, UGG boots, and complain about Arctic conditions.",
        "setting": "sunny Bondi beach promenade at 16 degrees",
        "character": {"age": 27, "gender": "female", "race": "tan", "expression": "tired", "prop": "coffee_cup", "attire": "coat"},
    },
    "Japan": {
        "joke_text": "Kotatsu heated table trap. Once you enter, you legally cannot leave until April.",
        "setting": "tatami living room with heated blanket kotatsu table",
        "character": {"age": 29, "gender": "female", "race": "light", "expression": "happy", "prop": "tea_cup", "attire": "jacket"},
    },
    "France": {
        "joke_text": "Paris shuts down over slush. Fashionistas refuse to wear functional boots because they ruin the silhouette.",
        "setting": "slushy Parisian sidewalk near Eiffel Tower",
        "character": {"age": 31, "gender": "female", "race": "light", "expression": "annoyed", "prop": "pastry", "attire": "coat"},
    },
    "Italy": {
        "joke_text": "Temperature hits 12°C. Italian mothers wrap children in 4 wool scarves to prevent 'colpo d'aria'.",
        "setting": "Milan piazza with people in heavy designer down coats",
        "character": {"age": 48, "gender": "female", "race": "light", "expression": "annoyed", "prop": "pasta_pot", "attire": "coat"},
    },
    "Poland": {
        "joke_text": "Smog alerts in Krakow rival 19th-century industrial London. Coal smoke builds character.",
        "setting": "foggy historic Krakow square under winter dusk",
        "character": {"age": 44, "gender": "male", "race": "fair", "expression": "tired", "prop": "bag", "attire": "coat"},
    },
    "Ireland": {
        "joke_text": "It's 4°C, raining horizontally, and grey. Exactly the same as summer, just slightly darker.",
        "setting": "wet cobblestone Dublin street with blowing rain",
        "character": {"age": 33, "gender": "male", "race": "pale", "expression": "happy", "prop": "tea_cup", "attire": "coat"},
    },
    "India": {
        "joke_text": "Delhi winter fog causes 8-hour train delays. Everyone wears colorful monkey caps and drinks masala chai.",
        "setting": "foggy morning street corner chai stall",
        "character": {"age": 36, "gender": "male", "race": "brown", "expression": "happy", "prop": "tea_cup", "attire": "coat"},
    },
    "South Korea": {
        "joke_text": "Long padded black coats everywhere. City looks like a convention of walking sleeping bags.",
        "setting": "freezing Seoul street packed with identical black puffer coats",
        "character": {"age": 24, "gender": "female", "race": "light", "expression": "neutral", "prop": "phone", "attire": "coat"},
    },
    "Netherlands": {
        "joke_text": "Canals freeze for 4 hours. Entire country declares national emergency so everyone can go ice skating.",
        "setting": "frozen Amsterdam canal with crowds of skaters",
        "character": {"age": 30, "gender": "male", "race": "fair", "expression": "happy", "prop": "coffee_cup", "attire": "jacket"},
    },
    "Switzerland": {
        "joke_text": "Snowplow clears mountain pass in 4 minutes flat. Bill charged to local canton with 0.0% variance.",
        "setting": "immaculately cleared Alpine mountain road",
        "character": {"age": 45, "gender": "male", "race": "fair", "expression": "smug", "prop": "clipboard", "attire": "coat"},
    },
    "Spain": {
        "joke_text": "House has zero central heating. Sitting inside wearing an outdoor coat while shivering over a space heater.",
        "setting": "tiled Madrid apartment living room with electric heater",
        "character": {"age": 32, "gender": "male", "race": "tan", "expression": "tired", "prop": "coffee_cup", "attire": "coat"},
    },
    "Mexico": {
        "joke_text": "Temperature hits 18°C. Family makes hot chocolate, wraps in San Marcos tiger blankets, and tells stories of the freeze.",
        "setting": "living room with heavy plush animal print blanket",
        "character": {"age": 38, "gender": "female", "race": "tan", "expression": "happy", "prop": "pasta_pot", "attire": "gown"},
    },
    "Brazil": {
        "joke_text": "Southern Brazil gets 0.5 cm of frost. National television news covers it live for 72 consecutive hours.",
        "setting": "frosty grass field in Curitiba with TV camera crews",
        "character": {"age": 29, "gender": "male", "race": "tan", "expression": "shocked", "prop": "phone", "attire": "jacket"},
    },
    "Greece": {
        "joke_text": "Snow in Athens. Acropolis looks pretty for 2 hours before melting into chaotic traffic.",
        "setting": "view of snow-dusted Parthenon columns",
        "character": {"age": 41, "gender": "male", "race": "tan", "expression": "happy", "prop": "coffee_cup", "attire": "jacket"},
    },
    "Turkey": {
        "joke_text": "Istanbul gets snow. Ferry cancelled. Street cats given luxury cardboard condos with heating pads.",
        "setting": "snowy Bosphorus harbor with cozy cat houses",
        "character": {"age": 37, "gender": "male", "race": "tan", "expression": "happy", "prop": "tea_cup", "attire": "coat"},
    },
    "Egypt": {
        "joke_text": "One raindrop touches Cairo pavement. Traffic stands completely still for 14 hours.",
        "setting": "Cairo highway with a light drizzle and stopped traffic",
        "character": {"age": 44, "gender": "male", "race": "tan", "expression": "shocked", "prop": "tea_cup", "attire": "jacket"},
    },
    "Singapore": {
        "joke_text": "Air conditioning in the cinema is 14°C. Only place in the country where you wear a winter coat.",
        "setting": "freezing modern shopping mall movie theater lobby",
        "character": {"age": 27, "gender": "female", "race": "light", "expression": "shocked", "prop": "phone", "attire": "coat"},
    },
    "Austria": {
        "joke_text": "Tyrol ski slopes groomed with micrometer precision. Apres-ski party begins promptly at 3:30 PM.",
        "setting": "snowy Tyrolean ski resort lodge terrace",
        "character": {"age": 36, "gender": "male", "race": "pale", "expression": "smug", "prop": "pastry", "attire": "jacket"},
    },
    "Belgium": {
        "joke_text": "Snowfall leads to a debate on whether clearing roads in Wallonia is constitutionally discriminatory.",
        "setting": "snowy Brussels roundabout with stalled traffic",
        "character": {"age": 39, "gender": "male", "race": "fair", "expression": "annoyed", "prop": "sign", "attire": "coat"},
    },
    "New Zealand": {
        "joke_text": "Skiing on an active volcano in July. Weather changes from blizzard to tropical sunshine in 6 minutes.",
        "setting": "Mt Ruapehu volcano ski trail",
        "character": {"age": 28, "gender": "male", "race": "tan", "expression": "happy", "prop": "ball", "attire": "jacket"},
    },
    "Philippines": {
        "joke_text": "Baguio City reaches 12°C. Millions of tourists arrive to see what winter feels like while eating strawberries.",
        "setting": "foggy mountain park in Baguio",
        "character": {"age": 25, "gender": "female", "race": "tan", "expression": "happy", "prop": "phone", "attire": "jacket"},
    },
    "Thailand": {
        "joke_text": "Chiang Mai hits 15°C. Northern residents bring out heavy wool beanies and declare an ice age.",
        "setting": "misty mountain viewpoint in northern Thailand",
        "character": {"age": 30, "gender": "male", "race": "tan", "expression": "happy", "prop": "tea_cup", "attire": "coat"},
    },
    "Vietnam": {
        "joke_text": "Sa Pa mountain gets 1 cm of snow. 80,000 selfie sticks simultaneously deployed from Hanoi.",
        "setting": "snowy terraced rice fields in Sa Pa",
        "character": {"age": 26, "gender": "female", "race": "tan", "expression": "happy", "prop": "phone", "attire": "coat"},
    },
    "Romania": {
        "joke_text": "Car buried under 2 meters of snow. Neighbor uses a homemade flame thrower to dig out his Dacia.",
        "setting": "heavily snowed-in Bucharest parking lot",
        "character": {"age": 42, "gender": "male", "race": "fair", "expression": "smug", "prop": "coffee_cup", "attire": "coat"},
    },
    "Colombia": {
        "joke_text": "Bogota rainstorm hits 10°C. Everyone puts on a ruana wool poncho and drinks hot chocolate with cheese.",
        "setting": "chilly colonial street in Bogota with cobblestones",
        "character": {"age": 31, "gender": "male", "race": "tan", "expression": "happy", "prop": "coffee_cup", "attire": "coat"},
    },
    "Argentina": {
        "joke_text": "Bariloche looks like Switzerland with chocolates and snow, but with 200% inflation and better beef.",
        "setting": "snowy wooden lodge in Bariloche mountains",
        "character": {"age": 35, "gender": "male", "race": "tan", "expression": "smug", "prop": "tea_cup", "attire": "jacket"},
    },
    "South Africa": {
        "joke_text": "Drakensberg mountains get 2 cm of snow. Family drives 8 hours just to make a 10 cm snowman on the hood.",
        "setting": "mountain pass with tiny snowman on car bonnet",
        "character": {"age": 32, "gender": "male", "race": "dark", "expression": "happy", "prop": "phone", "attire": "jacket"},
    },
}

_DRIVING_JOKES: dict[str, dict[str, Any]] = {
    "Italy": {
        "joke_text": "Traffic lights are suggestions. Lanes are abstract art. Horn is the primary brake system.",
        "setting": "chaotic Naples intersection with scooters and Fiats",
        "character": {"age": 38, "gender": "male", "race": "light", "expression": "happy", "prop": "phone", "attire": "shirt"},
    },
    "Germany": {
        "joke_text": "Driving 240 km/h in the left lane on the Autobahn. Flashed by an Audi going 310 km/h.",
        "setting": "Autobahn asphalt blur with fast lane sign",
        "character": {"age": 44, "gender": "male", "race": "fair", "expression": "shocked", "prop": "clipboard", "attire": "jacket"},
    },
    "India": {
        "joke_text": "Horn means: 'I'm coming', 'Thank you', 'Watch out', and 'Good morning'. Holy cow has right of way.",
        "setting": "colorful packed intersection with tuk-tuks and cows",
        "character": {"age": 35, "gender": "male", "race": "brown", "expression": "smug", "prop": "phone", "attire": "shirt"},
    },
    "USA": {
        "joke_text": "16-lane highway in Texas. Everyone driving an F-250 pickup truck the size of an aircraft carrier.",
        "setting": "sprawling concrete Houston interchange",
        "character": {"age": 40, "gender": "male", "race": "fair", "expression": "smug", "prop": "coffee_cup", "attire": "jacket"},
    },
    "UK": {
        "joke_text": "Entering a 5-exit roundabout. Everyone waves each other through in an endless polite Mexican standoff.",
        "setting": "rainy British roundabout with manicured grass centre",
        "character": {"age": 43, "gender": "female", "race": "fair", "expression": "tired", "prop": "tea_cup", "attire": "coat"},
    },
    "France": {
        "joke_text": "Arc de Triomphe roundabout has no lane markings and 400 cars playing real-time Russian roulette.",
        "setting": "dizzying Arc de Triomphe cobbled circular junction",
        "character": {"age": 36, "gender": "male", "race": "light", "expression": "annoyed", "prop": "pastry", "attire": "coat"},
    },
    "Egypt": {
        "joke_text": "Roundabout has 8 lanes with 14 rows of cars, 3 donkey carts, and headlights off to 'save the battery'.",
        "setting": "hectic Cairo square under evening dusk",
        "character": {"age": 45, "gender": "male", "race": "tan", "expression": "smug", "prop": "tea_cup", "attire": "shirt"},
    },
    "Greece": {
        "joke_text": "Motorcycle riding on the sidewalk while carrying a ladder, 2 dogs, and drinking an iced frappe.",
        "setting": "sunlit Athens boulevard with sidewalk riders",
        "character": {"age": 34, "gender": "male", "race": "tan", "expression": "happy", "prop": "coffee_cup", "attire": "shirt"},
    },
    "South Korea": {
        "joke_text": "Navigation app speaks at 400 words per minute warning of speed cameras every 200 meters.",
        "setting": "modern Seoul expressway with overhead electronic gantries",
        "character": {"age": 32, "gender": "male", "race": "light", "expression": "shocked", "prop": "phone", "attire": "jacket"},
    },
    "Japan": {
        "joke_text": "Driver bows twice and flashes hazard lights twice as a formal thank you for letting them merge.",
        "setting": "immaculate Tokyo multilane intersection",
        "character": {"age": 42, "gender": "male", "race": "light", "expression": "happy", "prop": "bag", "attire": "jacket"},
    },
    "Australia": {
        "joke_text": "Driving 6 hours through straight red dirt. Hit 0 traffic lights, saw 4 road trains and 18 dead roos.",
        "setting": "endless red dirt outback Stuart Highway",
        "character": {"age": 38, "gender": "male", "race": "tan", "expression": "neutral", "prop": "tea_cup", "attire": "shirt"},
    },
    "Canada": {
        "joke_text": "Hit a pothole so deep it opened a portal to the Earth's mantle. Driver said 'sorry' to the car.",
        "setting": "pothole-riddled springtime Montreal street",
        "character": {"age": 31, "gender": "female", "race": "fair", "expression": "shocked", "prop": "coffee_cup", "attire": "jacket"},
    },
    "Poland": {
        "joke_text": "1998 Passat 1.9 TDI doing 160 km/h in thick fog with broken taillights and a crucifix on the mirror.",
        "setting": "two-lane Polish country highway through pine forest",
        "character": {"age": 41, "gender": "male", "race": "fair", "expression": "smug", "prop": "bag", "attire": "jacket"},
    },
    "Brazil": {
        "joke_text": "Pothole slalom championship. Overtaking on the dirt shoulder while listening to pagode.",
        "setting": "cracked Brazilian highway with speed bumps",
        "character": {"age": 29, "gender": "male", "race": "tan", "expression": "happy", "prop": "phone", "attire": "jersey"},
    },
    "Mexico": {
        "joke_text": "Speed bumps (topes) designed by sadistic civil engineers to launch your suspension into orbit.",
        "setting": "colorful Mexican town street with mountain-high speed bump",
        "character": {"age": 37, "gender": "male", "race": "tan", "expression": "shocked", "prop": "phone", "attire": "shirt"},
    },
    "Netherlands": {
        "joke_text": "Cyclist kicks car door because you hesitated at a bike path crossing for 0.4 seconds.",
        "setting": "narrow canal street with swarms of bicycles",
        "character": {"age": 33, "gender": "male", "race": "fair", "expression": "annoyed", "prop": "clipboard", "attire": "shirt"},
    },
    "Sweden": {
        "joke_text": "Passed a moose test at 120 km/h. Car detected icy moose on the road and sent an email to Volvo.",
        "setting": "snowy pine-fringed Swedish highway",
        "character": {"age": 39, "gender": "male", "race": "pale", "expression": "neutral", "prop": "phone", "attire": "coat"},
    },
    "Ireland": {
        "joke_text": "Country boreen road is 1.8 meters wide with 2-meter high stone walls. Passing another car requires prayer.",
        "setting": "winding mossy Irish country lane with stone walls",
        "character": {"age": 35, "gender": "male", "race": "pale", "expression": "shocked", "prop": "tea_cup", "attire": "shirt"},
    },
    "Turkey": {
        "joke_text": "Taxi driver gives geopolitical analysis, smokes two cigarettes, and crosses 4 lanes without looking.",
        "setting": "yellow taxi driving over Bosphorus bridge",
        "character": {"age": 51, "gender": "male", "race": "tan", "expression": "smug", "prop": "tea_cup", "attire": "shirt"},
    },
    "Switzerland": {
        "joke_text": "Went 1 km/h over the speed limit. Fine arrived in mail with high-resolution photo and 300 CHF invoice.",
        "setting": "clean highway tunnel with automated radar cameras",
        "character": {"age": 47, "gender": "male", "race": "fair", "expression": "shocked", "prop": "bill", "attire": "jacket"},
    },
    "Singapore": {
        "joke_text": "Certificate of Entitlement (COE) costs $100,000 before you even buy the Toyota Corolla.",
        "setting": "spotless Singapore expressway with Electronic Road Pricing gantry",
        "character": {"age": 36, "gender": "male", "race": "light", "expression": "shocked", "prop": "bill", "attire": "jacket"},
    },
    "Finland": {
        "joke_text": "Every teenager can drift an old rear-wheel-drive Volvo on an iced gravel lake at 100 km/h.",
        "setting": "frozen lake rally practice course",
        "character": {"age": 25, "gender": "male", "race": "pale", "expression": "smug", "prop": "none", "attire": "jacket"},
    },
    "Norway": {
        "joke_text": "Electric cars everywhere. Ferry line to cross the fjord has 40 Teslas quietly charging.",
        "setting": "electric ferry dock on deep blue fjord",
        "character": {"age": 38, "gender": "female", "race": "pale", "expression": "neutral", "prop": "clipboard", "attire": "coat"},
    },
    "Portugal": {
        "joke_text": "Roundabout etiquette: staying in the outside lane is a crime punishable by immediate horn honking.",
        "setting": "sunny multi-lane roundabout in Lisbon",
        "character": {"age": 42, "gender": "male", "race": "tan", "expression": "annoyed", "prop": "pastry", "attire": "shirt"},
    },
    "South Africa": {
        "joke_text": "Taxi minibus takes the sidewalk, the ditch, and the median strip to avoid rush hour traffic.",
        "setting": "congested Johannesburg highway off-ramp",
        "character": {"age": 34, "gender": "male", "race": "dark", "expression": "smug", "prop": "phone", "attire": "shirt"},
    },
    "China": {
        "joke_text": "Automated traffic cameras scan your license plate, your face, and your social credit score in 0.2 seconds.",
        "setting": "Shanghai elevated multi-tier highway interchange",
        "character": {"age": 33, "gender": "male", "race": "light", "expression": "neutral", "prop": "phone", "attire": "jacket"},
    },
    "Argentina": {
        "joke_text": "Avenida 9 de Julio has 16 lanes. Crossing it before the light turns red requires Olympic sprint training.",
        "setting": "widest avenue in the world with Obelisco in background",
        "character": {"age": 39, "gender": "male", "race": "tan", "expression": "tired", "prop": "bag", "attire": "shirt"},
    },
    "Austria": {
        "joke_text": "Failed to buy the motorway vignette sticker. Austrian police materializes with a 300 euro terminal.",
        "setting": "Alpine motorway border checkpoint",
        "character": {"age": 44, "gender": "male", "race": "pale", "expression": "shocked", "prop": "bill", "attire": "jacket"},
    },
    "Belgium": {
        "joke_text": "You can tell you crossed the border into Belgium blindfolded because the car begins vibrating vigorously.",
        "setting": "border road transition from smooth Dutch asphalt to rough Belgian paving",
        "character": {"age": 38, "gender": "female", "race": "fair", "expression": "annoyed", "prop": "bag", "attire": "jacket"},
    },
    "New Zealand": {
        "joke_text": "One-lane wooden bridges on State Highway 1. Who gives way is decided by staring into the other driver's soul.",
        "setting": "scenic wooden one-lane bridge over turquoise river",
        "character": {"age": 29, "gender": "male", "race": "tan", "expression": "smug", "prop": "ball", "attire": "jersey"},
    },
    "Philippines": {
        "joke_text": "EDSA highway traffic stands still for 4 hours. Street vendors sell quail eggs, phone chargers, and fruit to your window.",
        "setting": "gridlocked Manila flyover highway with vendors",
        "character": {"age": 30, "gender": "female", "race": "tan", "expression": "tired", "prop": "phone", "attire": "shirt"},
    },
    "Thailand": {
        "joke_text": "Motorbike carrying a family of 5, a refrigerator, and a golden retriever effortlessly splits between two buses.",
        "setting": "busy Bangkok boulevard intersection under elevated train",
        "character": {"age": 28, "gender": "male", "race": "tan", "expression": "happy", "prop": "tea_cup", "attire": "shirt"},
    },
    "Vietnam": {
        "joke_text": "Crossing the street as a pedestrian: maintain constant walking speed and let 500 scooters flow around you like water.",
        "setting": "swirling Hanoi roundabout packed with scooters",
        "character": {"age": 26, "gender": "male", "race": "tan", "expression": "shocked", "prop": "bag", "attire": "jacket"},
    },
    "Romania": {
        "joke_text": "Horse-drawn cart on the highway being overtaken by a BMW doing 200 km/h with high beams flashing.",
        "setting": "Romanian national road passing through rural village",
        "character": {"age": 43, "gender": "male", "race": "fair", "expression": "smug", "prop": "coffee_cup", "attire": "jacket"},
    },
    "Colombia": {
        "joke_text": "Bus driver negotiates mountain hairpin turns on the edge of a 1,000-meter cliff while drinking hot tinto coffee.",
        "setting": "misty Andean mountain pass road with sheer drop",
        "character": {"age": 41, "gender": "male", "race": "tan", "expression": "smug", "prop": "coffee_cup", "attire": "shirt"},
    },
}

_TOPIC_CATALOGS: dict[str, dict[str, dict[str, Any]]] = {
    "hospital": _HOSPITAL_JOKES,
    "public_transport": _PUBLIC_TRANSPORT_JOKES,
    "school": _SCHOOL_JOKES,
    "parents": _PARENTS_JOKES,
    "dating": _DATING_JOKES,
    "supermarkets": _SUPERMARKETS_JOKES,
    "winter": _WINTER_JOKES,
    "driving": _DRIVING_JOKES,
}


# ==============================================================================
# Procedural Joke Engine for Any Custom Topic
# ==============================================================================

_PROCEDURAL_TEMPLATES: dict[str, list[str]] = {
    "generic": [
        "{country_heading} approach to {topic_clean}: {trait_a} taken to the extreme, while everyone pretends {trait_b} is completely normal.",
        "In {country_heading}, {topic_clean} is not a routine, it is an Olympic event. Expect {trait_a} and zero apologies.",
        "Rule #1 in {country_heading}: never question the {trait_a}. Rule #2: always bring {prop} just in case.",
        "Locals in {country_heading} say their {topic_clean} is the best in the world. Visitors leave with {trait_a} and mild confusion.",
    ],
}


def get_country_meta(country_name_or_alias: str) -> CountryMeta:
    """Resolve a country name, code, or alias to canonical CountryMeta."""
    key = country_name_or_alias.strip()
    norm = key.lower()
    if norm in COUNTRY_ALIASES:
        key = COUNTRY_ALIASES[norm]
    if key in COUNTRY_CATALOG:
        return COUNTRY_CATALOG[key]
    # Fuzzy match by heading or demonym
    for cm in COUNTRY_CATALOG.values():
        if norm in (cm.name.lower(), cm.heading.lower(), cm.demonym.lower(), cm.country_code.lower()):
            return cm
    # Fallback synthetic metadata
    clean_head = re.sub(r"[^a-zA-Z0-9\s]", "", key).title() or "Country"
    code = clean_head[:2].upper()
    return CountryMeta(
        name=clean_head,
        country_code=code,
        heading=clean_head,
        demonym=f"{clean_head}ian",
        default_expression="neutral",
        default_prop="none",
        default_attire="shirt",
        default_race="fair",
        cultural_traits=("local pride", "national habits"),
    )


def list_canonical_topics() -> list[str]:
    """Return all supported canonical topics."""
    return list(CANONICAL_TOPICS)


def list_available_countries() -> list[str]:
    """Return list of countries available across catalogs."""
    return list(COUNTRY_CATALOG.keys())


def get_curated_topic_data(topic_key: str) -> dict[str, dict[str, Any]] | None:
    """Return curated jokes for a canonical topic key, or None if custom."""
    canonical = normalize_wojak_topic(topic_key)
    return _TOPIC_CATALOGS.get(canonical)


def generate_procedural_joke(topic: str, country: str, seed: int | None = None) -> dict[str, Any]:
    """Procedurally generate a Wojak meme joke for any country and topic."""
    cm = get_country_meta(country)
    clean_topic = topic.replace("_", " ").strip()
    rng = random.Random(seed if seed is not None else int(hashlib.md5(f"{topic}:{country}".encode()).hexdigest()[:8], 16))

    trait_a = cm.cultural_traits[0] if cm.cultural_traits else "bureaucracy"
    trait_b = cm.cultural_traits[1] if len(cm.cultural_traits) > 1 else "tradition"

    templates = [
        f"In {cm.heading}, {clean_topic} comes down to one thing: {trait_a} at maximum volume.",
        f"Everyone in {cm.heading} has a strict rule for {clean_topic}: embrace {trait_a} and never mention {trait_b}.",
        f"Trying {clean_topic} in {cm.heading}? You'll need 4 forms, patience, and a deep appreciation for {trait_a}.",
        f"Locals in {cm.heading} treat {clean_topic} like an art form. Tourists call it organized chaos with {trait_a}.",
    ]
    joke_text = rng.choice(templates)
    expressions = ["smug", "annoyed", "shocked", "happy", "tired", "neutral"]
    chosen_expr = rng.choice(expressions) if cm.default_expression == "neutral" else cm.default_expression

    return {
        "joke_text": joke_text,
        "setting": f"authentic {cm.heading} setting during {clean_topic}",
        "character": {
            "age": rng.randint(25, 52),
            "gender": rng.choice(["male", "female"]),
            "race": cm.default_race,
            "expression": chosen_expr,
            "prop": cm.default_prop,
            "attire": cm.default_attire,
        },
    }
