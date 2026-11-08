"""Topic catalog, category mappings, and hashtag engine for Country Memes and Types Of... videos.

Includes the 25-hashtag viral library, curated topic catalogs for both modes,
procedural topic ideation, and smart hashtag selection based on semantic relevance.
"""

from __future__ import annotations

import random
from typing import Final

# The official 25-hashtag library for meme shorts
HASHTAG_LIBRARY: Final[list[str]] = [
    "#CountryMemes",
    "#CountryComparison",
    "#EveryCountry",
    "#CountryBeLike",
    "#FunnyMemes",
    "#RelatableMemes",
    "#AnimeMemes",
    "#AnimeEdit",
    "#MemeCompilation",
    "#WorldMemes",
    "#CultureMemes",
    "#POVMemes",
    "#FunnyVideos",
    "#ComedyVideos",
    "#GeographyMemes",
    "#Countries",
    "#AroundTheWorld",
    "#DailyMemes",
    "#InternetMemes",
    "#ViralMemes",
    "#FunnyMoments",
    "#YouTubeComedy",
    "#Relatable",
    "#MemeCulture",
    "#Shorts",
]

# Supported standard content categories
VALID_CATEGORIES: Final[list[str]] = [
    "countries",
    "school",
    "food",
    "relatable",
    "dark_comedy",
    "anime",
    "custom",
]

# Curated Country Meme topics
COUNTRY_MEME_TOPICS: Final[list[str]] = [
    "Every Country's Public Transport Be Like",
    "Every Country's School Life Be Like",
    "Every Country's Parents Be Like",
    "Every Country's Dating Culture Be Like",
    "Every Country's Supermarkets Be Like",
    "Every Country's Winter Be Like",
    "Every Country's Internet Be Like",
    "Every Country's Fast Food Be Like",
    "Every Country's Driving & Traffic Be Like",
    "Every Country's Survival Skills",
    "Every Country's Breakfast Routine Be Like",
    "Every Country's Police Officers Be Like",
    "Every Country's Grandmas When You Say You're Hungry",
    "Every Country's Customer Service Be Like",
    "Every Country's Summer Heatwave Be Like",
]

# Curated Types Of... topics
TYPES_OF_TOPICS: Final[list[str]] = [
    "Types of Students in Every Classroom",
    "Types of Gamers",
    "Types of Friends",
    "Types of Employees",
    "Types of People at a Party",
    "Types of Introverts",
    "Types of Gym People",
    "Types of Parents",
    "Types of People on Public Transport",
    "Types of Rich People",
    "Types of Overthinkers",
    "Types of People During Exams",
    "Types of People in Relationships",
    "Types of People on the Internet",
    "Types of People in a Zombie Apocalypse",
    "Anime Characters in Real Life",
    "Types of Neighbors You Can't Avoid",
    "Types of People in Group Projects",
    "Types of Siblings in Every Household",
    "Types of Morning People vs Night Owls",
]

# Mapping category keywords for topic classification
CATEGORY_TOPIC_TEMPLATES: Final[dict[str, list[str]]] = {
    "countries": [
        "Every Country's Public Transport Be Like",
        "Every Country's Winter Be Like",
        "Every Country's Driving & Traffic Be Like",
        "Every Country's Supermarkets Be Like",
        "Every Country's Internet Be Like",
        "Every Country's Survival Skills",
        "Every Country's Emergency Services Be Like",
        "Every Country's Airport Security Be Like",
    ],
    "school": [
        "Types of Students in Every Classroom",
        "Types of People During Exams",
        "Types of Teachers in High School",
        "Every Country's School Life Be Like",
        "Types of Students in Group Projects",
        "Types of People on the School Bus",
    ],
    "food": [
        "Every Country's Fast Food Be Like",
        "Every Country's Breakfast Routine Be Like",
        "Types of Eaters at a Buffet",
        "Types of People Cooking Dinner",
        "Every Country's Spicy Food Reaction",
    ],
    "relatable": [
        "Types of Friends",
        "Types of Overthinkers",
        "Types of Gym People",
        "Types of People at a Party",
        "Types of Morning People vs Night Owls",
        "Types of People on Public Transport",
        "Types of Gamers",
    ],
    "dark_comedy": [
        "Types of People in a Zombie Apocalypse",
        "Types of Employees on Monday Morning",
        "Types of People During an Alien Invasion",
        "Types of Drivers in Sudden Traffic Jams",
        "Every Country's Survival Instincts",
    ],
    "anime": [
        "Anime Characters in Real Life",
        "Types of Shonen Protagonists in Daily Life",
        "Every Country According to Anime Logic",
        "Types of Anime Villains at School",
        "Types of Weebs in Public",
    ],
}


def normalize_category(category: str | None) -> str:
    """Normalize user-provided category string into canonical form.

    Args:
        category: Input category name or None.

    Returns:
        One of the canonical category strings in VALID_CATEGORIES.
    """
    if not category:
        return "countries"
    cleaned = category.strip().lower().replace(" ", "_").replace("-", "_")
    if cleaned in VALID_CATEGORIES:
        return cleaned

    # Keyword matching heuristics
    if any(k in cleaned for k in ("country", "nation", "geo", "world")):
        return "countries"
    if any(k in cleaned for k in ("school", "class", "student", "exam", "college")):
        return "school"
    if any(k in cleaned for k in ("food", "eat", "cook", "kitchen", "snack")):
        return "food"
    if any(k in cleaned for k in ("dark", "apocalypse", "zombie", "cringe")):
        return "dark_comedy"
    if any(k in cleaned for k in ("anime", "manga", "otaku", "shonen")):
        return "anime"
    if any(k in cleaned for k in ("relatable", "life", "friend", "people", "gamer", "work")):
        return "relatable"

    return "custom"


def select_hashtags(
    mode: str,
    category: str = "",
    topic: str = "",
    count: int = 4,
) -> list[str]:
    """Select 3 to 5 highly relevant hashtags from the 25-hashtag library.

    Uses weighted scoring based on mode, category, and semantic tokens from the topic.

    Args:
        mode: Video generation mode ('country_meme' or 'types_of').
        category: Thematic category name.
        topic: Specific topic string.
        count: Desired number of hashtags (clamped between 3 and 5).

    Returns:
        List of selected hashtags.
    """
    clamped_count = max(3, min(5, count))
    scores: dict[str, float] = {tag: 1.0 for tag in HASHTAG_LIBRARY}

    # Base mode relevance
    norm_mode = mode.lower().strip()
    if norm_mode == "country_meme":
        scores["#CountryMemes"] += 10.0
        scores["#CountryComparison"] += 8.0
        scores["#EveryCountry"] += 7.0
        scores["#CountryBeLike"] += 6.0
        scores["#GeographyMemes"] += 5.0
        scores["#WorldMemes"] += 4.0
        scores["#AroundTheWorld"] += 4.0
        scores["#CultureMemes"] += 4.0
        scores["#Countries"] += 4.0
    else:  # types_of or general
        scores["#RelatableMemes"] += 9.0
        scores["#POVMemes"] += 8.0
        scores["#Relatable"] += 7.0
        scores["#MemeCompilation"] += 6.0
        scores["#FunnyMoments"] += 5.0
        scores["#ViralMemes"] += 4.0

    # Category relevance
    norm_cat = normalize_category(category)
    if norm_cat == "anime":
        scores["#AnimeMemes"] += 12.0
        scores["#AnimeEdit"] += 10.0
    elif norm_cat == "countries":
        scores["#WorldMemes"] += 6.0
        scores["#CultureMemes"] += 5.0
        scores["#GeographyMemes"] += 5.0
    elif norm_cat in ("school", "relatable"):
        scores["#RelatableMemes"] += 6.0
        scores["#Relatable"] += 6.0
        scores["#POVMemes"] += 4.0
    elif norm_cat == "dark_comedy":
        scores["#ComedyVideos"] += 5.0
        scores["#FunnyVideos"] += 4.0

    # Topic keyword matching
    topic_lower = topic.lower()
    if "anime" in topic_lower:
        scores["#AnimeMemes"] += 8.0
        scores["#AnimeEdit"] += 6.0
    if any(k in topic_lower for k in ("country", "nation", "world", "geography")):
        scores["#CountryMemes"] += 6.0
        scores["#WorldMemes"] += 5.0
    if any(
        k in topic_lower
        for k in ("student", "exam", "friend", "party", "people", "introvert", "gym", "gamer")
    ):
        scores["#RelatableMemes"] += 5.0
        scores["#Relatable"] += 5.0
        scores["#POVMemes"] += 4.0

    # Guarantee #Shorts is considered high-value
    scores["#Shorts"] += 3.0
    scores["#FunnyMemes"] += 3.0

    # Sort descending by score, add slight deterministic jitter to avoid exact duplicate sets
    # We use a seed based on the topic string
    rng = random.Random(topic or mode)
    sorted_tags = sorted(
        HASHTAG_LIBRARY,
        key=lambda tag: scores.get(tag, 0.0) + rng.uniform(-0.1, 0.1),
        reverse=True,
    )

    selected = sorted_tags[:clamped_count]
    # Always ensure #Shorts is in the top tags if space permits
    if "#Shorts" not in selected and len(selected) > 0:
        selected[-1] = "#Shorts"

    return selected


def resolve_topic_and_category(
    topic: str | None = None,
    category: str | None = None,
    mode: str = "country_meme",
) -> tuple[str, str]:
    """Resolve, normalize, or generate fallback topic and category.

    Args:
        topic: Optional input topic string.
        category: Optional input category string.
        mode: Target mode ('country_meme' or 'types_of').

    Returns:
        Tuple of (resolved_topic, resolved_category).
    """
    norm_cat = normalize_category(category) if category else None

    if topic and topic.strip():
        resolved_topic = topic.strip()
        # Deduce category if not explicitly specified
        if not category:
            topic_lower = resolved_topic.lower()
            if any(k in topic_lower for k in ("anime", "manga", "shonen")):
                resolved_cat = "anime"
            elif any(k in topic_lower for k in ("country", "japan", "germany", "usa", "be like")):
                resolved_cat = "countries"
            elif any(
                k in topic_lower for k in ("student", "classroom", "school", "exam", "teacher")
            ):
                resolved_cat = "school"
            elif any(k in topic_lower for k in ("food", "eating", "burger", "pizza", "buffet")):
                resolved_cat = "food"
            elif any(k in topic_lower for k in ("zombie", "dark", "apocalypse", "monday")):
                resolved_cat = "dark_comedy"
            else:
                resolved_cat = "relatable"
        else:
            resolved_cat = norm_cat or "relatable"
        return resolved_topic, resolved_cat

    # If topic is not provided, pick an appropriate one from catalog
    if norm_cat and norm_cat in CATEGORY_TOPIC_TEMPLATES and CATEGORY_TOPIC_TEMPLATES[norm_cat]:
        picked_topic = random.choice(CATEGORY_TOPIC_TEMPLATES[norm_cat])
        return picked_topic, norm_cat

    if mode == "types_of":
        picked_topic = random.choice(TYPES_OF_TOPICS)
        return picked_topic, "relatable"

    # Default to country meme
    picked_topic = random.choice(COUNTRY_MEME_TOPICS)
    return picked_topic, "countries"


def generate_topic_idea(
    category: str | None = None,
    mode: str | None = None,
) -> tuple[str, str]:
    """Generate a creative, hilarious new topic idea procedurally.

    Creates novel topics not just from static lists, but by combining cultural
    and situational meme templates.

    Args:
        category: Optional category constraint.
        mode: Optional video mode ('country_meme' or 'types_of'). If omitted, inferred from category.

    Returns:
        Tuple of (topic_title, category_name).
    """
    cat = normalize_category(category) if category else None

    # Infer mode if not explicitly specified
    if not mode:
        if cat == "countries":
            resolved_mode = "country_meme"
        elif cat in ("school", "food", "relatable", "dark_comedy", "anime"):
            resolved_mode = "types_of"
        else:
            resolved_mode = "country_meme"
    else:
        resolved_mode = mode

    # Country meme dynamic concept generators
    country_situations = [
        "Handling Flight Delays",
        "Reacting to an Earthquake",
        "Coffee Culture & Addiction",
        "Dealing With Rude Customers",
        "Going to the Beach in Summer",
        "Grandmas Preparing a 'Light Snack'",
        "Reacting to 1cm of Snow",
        "Finding a Bug in Their Room",
        "Waiting in Line for 5 Minutes",
        "Their Version of 'Fast' Food",
        "Attending a Formal Dinner",
        "Reacting to a Horror Movie",
    ]

    # Types of dynamic concept generators
    types_subjects = [
        "People Who Sleep 2 Hours vs 12 Hours",
        "People During Black Friday Sales",
        "Coworkers in Zoom Meetings",
        "Cooks in the Kitchen",
        "People at Airport Baggage Claim",
        "Siblings Sharing a Single Charger",
        "People Who Refuse to Ask for Directions",
        "Gamers Playing Ranked at 3 AM",
        "People Dealing With a Spider",
        "Students 5 Minutes Before the Deadline",
        "People Walking Their Dogs",
        "Online Shoppers Tracking Delivery",
    ]

    if resolved_mode == "country_meme":
        chosen_cat = "countries"
        if random.random() < 0.4:
            # Pick from curated list
            return random.choice(COUNTRY_MEME_TOPICS), chosen_cat
        sit = random.choice(country_situations)
        return f"Every Country's {sit} Be Like", chosen_cat

    # types_of mode
    chosen_cat = cat or "relatable"
    if chosen_cat in CATEGORY_TOPIC_TEMPLATES and random.random() < 0.4:
        return random.choice(CATEGORY_TOPIC_TEMPLATES[chosen_cat]), chosen_cat
    sit = random.choice(types_subjects)
    return f"Types of {sit}", chosen_cat
