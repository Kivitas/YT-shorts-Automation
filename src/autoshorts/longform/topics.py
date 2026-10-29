"""Topic and genre catalogue for long-form videos: built-in genres + dynamic custom topic synthesis."""

from __future__ import annotations

import re
from dataclasses import dataclass

TOPIC_KEYS = (
    "discipline",
    "money",
    "testosterone",
    "science",
    "self_improvement",
    "parody",
    "history",
    "comparison",
    "finance",
    "motivation",
)

GENRE_KEYS = (
    "parody",
    "history",
    "science",
    "comparison",
    "finance",
    "self_improvement",
    "motivation",
)

MAX_TITLE_CHARS = 60

_REJECTED_TOPIC_TOKENS = frozenset({"", "nope", "invalid", "unknown", "none", "null"})


@dataclass(frozen=True)
class TopicSpec:
    key: str
    label: str
    title_keywords: tuple[str, ...]  # heavily searched words; the first is the default title lead
    hashtags: tuple[str, ...]
    safety_note: str
    curated_points: tuple[tuple[str, str], ...]  # (heading, narration) pairs
    curated_hook: str = ""
    genre: str = "self_improvement"
    sub_domain: str = ""
    visual_mode: str = "auto"


_DISCIPLINE = TopicSpec(
    key="discipline",
    label="Discipline",
    genre="self_improvement",
    sub_domain="habit_loop",
    visual_mode="motivation_loop",
    title_keywords=(
        "Discipline",
        "Habits",
        "Focus",
        "Motivation",
        "Mindset",
        "Self Improvement",
    ),
    hashtags=(
        "#discipline",
        "#selfimprovement",
        "#mindset",
        "#habits",
        "#motivation",
        "#focus",
        "#consistency",
    ),
    safety_note=(
        "General self-improvement ideas only. This is not medical or mental health advice."
    ),
    curated_hook="Discipline is not a feeling, it is a decision you repeat when you do not want to.",
    curated_points=(
        (
            "Motivation fades",
            (
                "Motivation is a mood, and moods change. If you only act when you feel ready, "
                "you will act rarely. People who look disciplined simply stopped waiting for the "
                "feeling. They decide what to do in advance, so the choice is already made when "
                "the day gets hard. Your first job is to remove the daily debate from your head."
            ),
        ),
        (
            "Make it small",
            (
                "Start with a version of the habit so small it feels almost silly. Ten minutes of "
                "work. Five pushups. One page of reading. Small actions are easy to repeat, and "
                "repetition is what builds identity. Once the habit exists, you can grow it. "
                "Trying to change everything at once is the fastest way to quit by Friday."
            ),
        ),
        (
            "Design your environment",
            (
                "Willpower is limited, so stop spending it on temptation. Put your phone in "
                "another room while you work. Lay out your training clothes the night before. "
                "Keep the good choice visible and the bad choice inconvenient. When the "
                "environment does the heavy lifting, discipline becomes much less of a fight."
            ),
        ),
        (
            "Track and never miss twice",
            (
                "Mark each day you follow through on a simple calendar. The chain becomes "
                "motivating by itself. You will miss a day eventually, and that is normal. The "
                "rule is never miss twice. One slip is an accident, two slips is the start of a "
                "new habit. Return the next day, without guilt, and keep going."
            ),
        ),
        (
            "Rest is part of the plan",
            (
                "Discipline is not punishing yourself. Sleep, meals and breaks are what keep "
                "you consistent for months instead of days. Plan your rest the same way you "
                "plan your work. The goal is not one perfect week. The goal is a routine you can "
                "still follow a year from now. Start today, with the smallest step."
            ),
        ),
    ),
)

_MONEY = TopicSpec(
    key="money",
    label="Money",
    genre="finance",
    sub_domain="compounding",
    visual_mode="finance_curve",
    title_keywords=(
        "Money",
        "Budget",
        "Saving",
        "Debt",
        "Personal Finance",
        "Financial Literacy",
    ),
    hashtags=(
        "#money",
        "#personalfinance",
        "#budgeting",
        "#saving",
        "#financialliteracy",
        "#investing",
        "#moneytips",
    ),
    safety_note=(
        "General financial education only. This is not financial advice, no income or "
        "returns are guaranteed, and no specific investment is recommended. Consider "
        "speaking to a qualified professional about your own situation."
    ),
    curated_hook="Most money problems are not about earning more, they are about what happens after.",
    curated_points=(
        (
            "Know where it goes",
            (
                "You cannot manage what you do not measure. For one month, write down every "
                "expense, even small ones. Most people are surprised by how much leaks out "
                "through subscriptions and impulse buys. This is not about guilt. It is about "
                "getting a clear picture, because a clear picture makes every other decision easier."
            ),
        ),
        (
            "Pay yourself first",
            (
                "When income arrives, move a fixed amount into savings before you spend "
                "anything. Even a small amount works, because the habit matters more than the "
                "number. Automating the transfer removes the need for willpower. Saving what is "
                "left rarely works, since there is usually nothing left."
            ),
        ),
        (
            "Build a safety buffer",
            (
                "An emergency fund turns a crisis into an inconvenience. A common goal is to "
                "cover a few months of essential costs, built gradually. Having that buffer "
                "means a car repair or a gap between jobs does not push you into high interest "
                "debt. Start with a small first target and grow it over time."
            ),
        ),
        (
            "Understand debt",
            (
                "Debt has a cost, and the interest rate is the price. High interest debt grows "
                "quickly, so understanding the rate on every balance you hold is important. "
                "Many people choose to list their debts and pay minimums on all of them, then "
                "put extra on one. Whatever method you pick, consistency beats intensity."
            ),
        ),
        (
            "Learn before you act",
            (
                "Be careful with anyone promising fast or guaranteed results. Real wealth "
                "building is usually slow and boring. Learn the basics of how interest, "
                "inflation and risk work, and read from reliable sources. For decisions about "
                "your own money, speak to a qualified professional. Knowledge is the best first step."
            ),
        ),
    ),
)

_TESTOSTERONE = TopicSpec(
    key="testosterone",
    label="Testosterone",
    genre="science",
    sub_domain="biology",
    visual_mode="science_biology",
    title_keywords=(
        "Testosterone",
        "Sleep",
        "Strength",
        "Hormones",
        "Health",
        "Fitness",
        "Mens Health",
    ),
    hashtags=(
        "#testosterone",
        "#health",
        "#fitness",
        "#sleep",
        "#menshealth",
        "#strength",
        "#gymmotivation",
    ),
    safety_note=(
        "General lifestyle information only. This is not medical advice and no results are "
        "promised. If you are worried about your hormones, ask a doctor about blood tests."
    ),
    curated_hook="Your daily habits shape your hormones more than any trick you saw online.",
    curated_points=(
        (
            "Sleep comes first",
            (
                "Hormone production is closely tied to sleep. Most adults do best with seven to "
                "nine hours. Keep a regular bedtime, keep the room dark and cool, and avoid "
                "screens right before bed. Poor sleep affects energy, mood and recovery, so "
                "protecting it is the simplest upgrade you can make."
            ),
        ),
        (
            "Train with resistance",
            (
                "Lifting weights with good form is linked to better strength, body composition "
                "and general health. Focus on big movements like squats, presses and rows, "
                "train a few times a week, and increase the load slowly. Do not chase "
                "exhaustion. Recovery days are where your body actually adapts."
            ),
        ),
        (
            "Eat real food",
            (
                "Your body needs enough energy, protein, healthy fats and micronutrients. "
                "Extreme crash diets can leave you tired and run down. Build meals around whole "
                "foods, vegetables, quality protein and enough total calories for your goals. "
                "Steady and sustainable beats strict and short lived."
            ),
        ),
        (
            "Manage stress",
            (
                "Long term stress raises cortisol, which can interfere with sleep, appetite and "
                "recovery. You do not need to remove stress, only to handle it. Walking "
                "outside, time with friends, breathing exercises and clear breaks from work all "
                "help. Alcohol and excess weight are also worth reviewing honestly."
            ),
        ),
        (
            "See a doctor",
            (
                "Feeling constantly tired, low in mood or low in drive does not automatically "
                "mean low testosterone. Many things can cause those symptoms. The right move is "
                "to see a doctor, who can order blood tests and look at the whole picture. "
                "Avoid guessing, and avoid anyone selling a quick fix."
            ),
        ),
    ),
)

_SCIENCE = TopicSpec(
    key="science",
    label="Science",
    genre="science",
    sub_domain="biology",
    visual_mode="science_biology",
    title_keywords=(
        "Brain",
        "Dopamine",
        "Science",
        "Psychology",
        "Neuroscience",
        "Physics",
        "Chemistry",
        "Facts",
    ),
    hashtags=(
        "#science",
        "#brain",
        "#dopamine",
        "#psychology",
        "#neuroscience",
        "#facts",
        "#didyouknow",
    ),
    safety_note=(
        "Simplified science for general interest. This is not medical advice and ideas are "
        "simplified."
    ),
    curated_hook="Your brain is wired to chase rewards, and that explains more of your day than you think.",
    curated_points=(
        (
            "Dopamine is about wanting",
            (
                "Dopamine is often called the pleasure chemical, but research suggests it is "
                "more about anticipation and motivation. It helps drive you toward a reward, "
                "not just enjoy it. That is why the chase can feel better than the result, and "
                "why a new notification pulls at your attention so easily."
            ),
        ),
        (
            "Prediction errors",
            (
                "Your brain constantly predicts what will happen next. When reality beats the "
                "prediction, dopamine activity rises, and you learn to repeat the behavior. "
                "When it is worse, activity falls. This learning loop is how habits form, "
                "for good and bad. Unpredictable rewards, like a feed that refreshes, are especially sticky."
            ),
        ),
        (
            "Neuroplasticity",
            (
                "The brain physically changes with repeated experience. Connections that fire "
                "together tend to strengthen, and unused ones weaken. This is called "
                "neuroplasticity, and it continues through adult life. It means practice "
                "really does change you, whether you are learning a language or a bad habit."
            ),
        ),
        (
            "Sleep and memory",
            (
                "During sleep, the brain replays and organizes what you learned during the day. "
                "Studies link good sleep with better memory and attention. Cutting sleep to "
                "study more often backfires. Spacing out practice over several days, instead of "
                "cramming, also helps information stick for longer."
            ),
        ),
        (
            "Use it wisely",
            (
                "Understanding these systems gives you leverage. Reduce constant cheap rewards, "
                "protect your sleep, and practice what matters in small regular sessions. You "
                "cannot rewrite your brain overnight, but you can nudge it every day. "
                "Curiosity itself is a skill that grows with use."
            ),
        ),
    ),
)

_SELF_IMPROVEMENT = TopicSpec(
    key="self_improvement",
    label="Self Improvement",
    genre="self_improvement",
    sub_domain="habit_loop",
    visual_mode="motivation_loop",
    title_keywords=(
        "Habits",
        "Self Improvement",
        "Mindset",
        "Productivity",
        "Personal Growth",
        "Discipline",
    ),
    hashtags=(
        "#selfimprovement",
        "#habits",
        "#mindset",
        "#productivity",
        "#personalgrowth",
        "#discipline",
    ),
    safety_note=(
        "General self-improvement ideas only. This is not medical or mental health advice."
    ),
    curated_hook="You do not need a new life, you need a slightly better day, repeated.",
    curated_points=(
        (
            "Pick one thing",
            (
                "Most people fail at self improvement by trying to fix everything together. "
                "Choose one area, such as sleep, movement or learning, and give it your focus "
                "for a month. One real change you keep is worth more than ten changes you "
                "abandon. Narrow focus is how progress becomes visible."
            ),
        ),
        (
            "Build a morning anchor",
            (
                "A short, repeatable start to your day sets the tone. It could be water, a "
                "short walk and ten minutes of planning. It does not need to be fancy or "
                "early. It only needs to be consistent. A simple anchor gives you a win before "
                "the world starts asking for your attention."
            ),
        ),
        (
            "Learn on purpose",
            (
                "Read or study a little each day with a clear question in mind. Take notes in "
                "your own words, and try to apply one idea within a day. Learning that never "
                "turns into action is just entertainment. Teaching what you learned to someone "
                "else is one of the best ways to make it stick."
            ),
        ),
        (
            "Choose your circle",
            (
                "You tend to become like the people and content you spend the most time with. "
                "Spend time with people who encourage growth, and limit sources that leave you "
                "anxious or distracted. You do not need to cut anyone off. Simply give more "
                "of your attention to what pulls you forward."
            ),
        ),
        (
            "Review and adjust",
            (
                "Once a week, ask what worked, what did not, and what to change. Treat your "
                "life like an experiment, not a verdict. Progress is rarely a straight line. "
                "Be patient, stay curious, and keep your standards kind but firm. Begin with one "
                "small step today."
            ),
        ),
    ),
)

_PARODY = TopicSpec(
    key="parody",
    label="Parody & Satire",
    genre="parody",
    sub_domain="banter",
    visual_mode="parody_duet",
    title_keywords=(
        "Parody",
        "Explained",
        "Satire",
        "Comedy",
        "Reality",
        "Truth",
    ),
    hashtags=(
        "#parody",
        "#satire",
        "#comedy",
        "#animation",
        "#explained",
        "#humor",
        "#storytime",
    ),
    safety_note=(
        "Comedic satire and parody for entertainment only. Exaggerated scenarios are fictional."
    ),
    curated_hook="Everyone pretends they have a five-year master plan, until Tuesday hits at 2 PM.",
    curated_points=(
        (
            "The 5 AM Myth",
            (
                "Every internet guru wakes up at 4 AM to journal in an ice bath. Meanwhile, "
                "normal humans negotiate with three different alarm clocks just to find matching "
                "socks. Yet the strange paradox is that the guru is still tired by noon, and "
                "what happens next explains why."
            ),
        ),
        (
            "The Productivity Trap",
            (
                "Wait, so buying seventeen color-coded planners does not count as actual work? "
                "Apparently spending four hours organizing your task manager is just procrastination "
                "wearing a tuxedo. Once the stickers come out, the actual project is already doomed, "
                "unless you flip the rule upside down."
            ),
        ),
        (
            "Expert Syndrome",
            (
                "Read two articles about a subject and suddenly you are ready to debate a PhD in "
                "the comments section. Confidence spikes the exact moment before you realize how "
                "complicated the problem really is. That cliff has a name, and everyone falls off "
                "it at least twice a week."
            ),
        ),
        (
            "The Overthinking Loop",
            (
                "You ask a simple question and your brain immediately simulates fourteen worst-case "
                "scenarios from the year 2014. One awkward wave at a stranger becomes a three-act "
                "documentary at midnight. Breaking that loop takes a deadpan reality check."
            ),
        ),
        (
            "The Real Secret",
            (
                "Nobody actually knows what they are doing all the time. The people who win are "
                "just comfortable looking slightly confused while they figure out step one. Laugh "
                "at the absurdity, close the nineteen browser tabs, and do the one obvious thing."
            ),
        ),
    ),
)

_HISTORY = TopicSpec(
    key="history",
    label="History",
    genre="history",
    sub_domain="timeline",
    visual_mode="history_map",
    title_keywords=(
        "History",
        "Empire",
        "Ancient",
        "Civilization",
        "Battle",
        "Timeline",
    ),
    hashtags=(
        "#history",
        "#ancienthistory",
        "#civilization",
        "#empire",
        "#worldhistory",
        "#documentary",
        "#historical",
    ),
    safety_note=(
        "Historical educational overview only. Dates and accounts reflect mainstream consensus."
    ),
    curated_hook="One single miscalculation at a city gate rewrote a thousand years of human history.",
    curated_points=(
        (
            "The Geography of Power",
            (
                "Empires rarely rise by accident; they rise at choke points where trade routes "
                "and sea lanes collide. Whoever controlled the straits and river valleys controlled "
                "grain, gold, and information. But geography that protects a capital in one century "
                "becomes a trap when technology shifts."
            ),
        ),
        (
            "Logistics Over Legends",
            (
                "Stories remember generals, but campaigns were won by supply lines, roads, and "
                "salted rations. An army marching twenty miles a day needed tons of grain before "
                "a single arrow was fired. When the supply wagons stalled, even the largest "
                "host in the ancient world began to unravel."
            ),
        ),
        (
            "The Technological Pivot",
            (
                "For centuries, stone fortifications made cities nearly untouchable. Then metallurgy "
                "and siege engineering changed the math overnight. Walls that stood for a millennium "
                "shattered in weeks, forcing kingdoms to reinvent diplomacy, taxation, and defense."
            ),
        ),
        (
            "Trade and Cultural Flow",
            (
                "Behind every border conflict ran a quiet network of merchants carrying silk, "
                "spices, paper, and mathematical ideas across continents. When one route closed, "
                "explorers gambled on uncharted oceans, triggering the next era of global contact."
            ),
        ),
        (
            "Lessons of the Archive",
            (
                "Civilizations rarely collapse from a single blow; they fracture when institutions "
                "ignore changing reality. Studying the timeline from 480 BC to 1453 AD reveals the "
                "same recurring human pattern: adaptability outlasts raw size every time."
            ),
        ),
    ),
)

_COMPARISON = TopicSpec(
    key="comparison",
    label="Comparison & VS Battles",
    genre="comparison",
    sub_domain="vs_battle",
    visual_mode="comparison_vs",
    title_keywords=(
        "Comparison",
        "Versus",
        "Ranked",
        "Tier List",
        "Battle",
        "Breakdown",
    ),
    hashtags=(
        "#comparison",
        "#versus",
        "#tierlist",
        "#ranking",
        "#breakdown",
        "#analysis",
        "#vs",
    ),
    safety_note=(
        "Comparative analysis for educational and entertainment purposes based on standardized metrics."
    ),
    curated_hook="On paper one contender looks unstoppable, until you compare the five hidden metrics.",
    curated_points=(
        (
            "Raw Power vs Precision",
            (
                "At first glance, brute force dominates the opening exchange. Contender A brings "
                "overwhelming peak output, while Contender B relies on efficiency and timing. "
                "Yet peak numbers hide what happens after the first sixty seconds of pressure."
            ),
        ),
        (
            "Speed and Adaptability",
            (
                "When conditions shift, mobility decides who controls the tempo. High reaction "
                "speed turns defensive gaps into counter-openings. Look closely at the velocity "
                "curve and the lead flips dramatically in the middle phase."
            ),
        ),
        (
            "Intelligence and Strategy",
            (
                "Tactical planning multiplies every physical stat on the board. A contender who "
                "reads patterns early avoids wasted effort and forces errors. Still, even the "
                "smartest strategy hits a wall when resource cost enters the equation."
            ),
        ),
        (
            "Cost and Durability",
            (
                "Sustainability separates S-tier performers from fragile glass cannons. High "
                "maintenance cost drains endurance over long campaigns, while rugged durability "
                "keeps compounding small advantages round after round."
            ),
        ),
        (
            "The Final Verdict",
            (
                "When we weight Power, Speed, Intelligence, Cost, and Durability together on the "
                "tier board, balanced resilience takes the S-tier crown. Raw flash wins highlights, "
                "but complete fundamentals win the matchup."
            ),
        ),
    ),
)

_FINANCE = TopicSpec(
    key="finance",
    label="Finance & Wealth",
    genre="finance",
    sub_domain="compounding",
    visual_mode="finance_curve",
    title_keywords=(
        "Finance",
        "Wealth",
        "Compounding",
        "Investing",
        "Money",
        "Assets",
    ),
    hashtags=(
        "#finance",
        "#wealth",
        "#compounding",
        "#investing",
        "#personalfinance",
        "#financialfreedom",
        "#money",
    ),
    safety_note=(
        "General financial education only. This is not financial advice, no income or "
        "returns are guaranteed, and no specific investment is recommended."
    ),
    curated_hook="The gap between staying broke and building wealth comes down to one exponential curve.",
    curated_points=(
        (
            "The Silent Inflation Tax",
            (
                "Cash sitting idle feels safe, but purchasing power erodes quietly every single year. "
                "When inflation averages three to four percent, uninvested savings lose half their "
                "real value over two decades. Understanding that baseline is why savers must become "
                "asset owners."
            ),
        ),
        (
            "The Compounding Hockey Stick",
            (
                "In the first five years, compounding looks painfully flat and almost boring. "
                "Most people quit right before the inflection point where returns begin generating "
                "their own returns. Time in the market is the exponent that drives the curve vertical."
            ),
        ),
        (
            "Cash Flow vs Liabilities",
            (
                "High income with equally high lifestyle inflation produces zero net freedom. "
                "Every dollar directed into productive assets buys back future hours, while status "
                "liabilities lock you into perpetual monthly obligations."
            ),
        ),
        (
            "Risk and Diversification",
            (
                "Concentrated bets create headlines, but systematic allocation builds durable "
                "portfolios. Spreading exposure across broad productive categories cushions "
                "drawdowns so you never have to sell at the bottom of a cycle."
            ),
        ),
        (
            "Automate the System",
            (
                "Remove emotion from every paycheck by routing capital automatically before "
                "lifestyle spending begins. Keep fees low, extend your time horizon, and let "
                "mathematical compounding do the heavy lifting over decades."
            ),
        ),
    ),
)

_MOTIVATION = TopicSpec(
    key="motivation",
    label="Motivation & Mindset",
    genre="motivation",
    sub_domain="value_equation",
    visual_mode="motivation_value",
    title_keywords=(
        "Mindset",
        "Motivation",
        "Focus",
        "Systems",
        "Purpose",
        "Discipline",
    ),
    hashtags=(
        "#motivation",
        "#mindset",
        "#focus",
        "#systems",
        "#selfimprovement",
        "#discipline",
        "#growth",
    ),
    safety_note=(
        "General self-improvement and mindset ideas only. This is not medical or mental health advice."
    ),
    curated_hook="Your brain is not broken; it is running a feedback loop you never consciously designed.",
    curated_points=(
        (
            "The Four-Node Habit Loop",
            (
                "Every behavior follows the exact same four-step circuit: Cue, Craving, Response, "
                "and Reward. When distractions hijack the cue, your attention drains before the "
                "workday even begins. Re-engineering the cue rewires the entire loop."
            ),
        ),
        (
            "Resetting Your Dopamine Baseline",
            (
                "Constant high-stimulation inputs flood your baseline and make meaningful work "
                "feel unbearably slow. Lowering artificial noise for even ninety minutes restores "
                "sensitivity, turning deep focus from a chore into a flow state."
            ),
        ),
        (
            "The Value Equation",
            (
                "High performers maximize the dream outcome and perceived likelihood of success "
                "while ruthlessly compressing time delay and friction. Break a massive goal into "
                "an immediate ten-minute proof of progress today."
            ),
        ),
        (
            "One Percent Daily Compounding",
            (
                "Improving by one percent a day looks invisible on Tuesday, yet compounds to "
                "thirty-seven times your starting baseline over a single year. Small daily "
                "inputs beat occasional heroic bursts every single time."
            ),
        ),
        (
            "Identity Over Outcomes",
            (
                "Goals tell you where to aim, but systems determine where you actually land. "
                "Vote for your new identity with one completed block of focused action right now."
            ),
        ),
    ),
)

TOPICS: dict[str, TopicSpec] = {
    t.key: t
    for t in (
        _DISCIPLINE,
        _MONEY,
        _TESTOSTERONE,
        _SCIENCE,
        _SELF_IMPROVEMENT,
        _PARODY,
        _HISTORY,
        _COMPARISON,
        _FINANCE,
        _MOTIVATION,
    )
}


def normalize_topic(value: str) -> str:
    """Map user input (case, spaces, dashes) to a built-in topic key, or raise ValueError."""
    if not isinstance(value, str):
        raise ValueError(f"Unknown topic {value!r}; expected one of {', '.join(TOPIC_KEYS)}")
    key = re.sub(r"[\s\-]+", "_", value.strip().lower())
    if key not in TOPICS:
        raise ValueError(f"Unknown topic {value!r}; expected one of {', '.join(TOPIC_KEYS)}")
    return key


def infer_genre(topic: str, genre: str | None = None) -> tuple[str, str]:
    """Infer `(genre, sub_domain)` from any custom topic string or explicit genre."""
    raw = (topic or "").strip().lower()
    explicit = re.sub(r"[\s\-]+", "_", (genre or "").strip().lower())

    # Detect science sub-domain first
    sci_sub = ""
    if any(
        w in raw
        for w in (
            "chemistry",
            "atom",
            "electron",
            "molecule",
            "molecular",
            "chemical",
            "reaction",
            "periodic",
            "bond",
            "bohr",
            "acid",
        )
    ):
        sci_sub = "chemistry"
    elif any(
        w in raw
        for w in (
            "astrophysics",
            "black hole",
            "galaxy",
            "star",
            "planet",
            "orbit",
            "cosmos",
            "universe",
            "supernova",
            "nebula",
            "solar",
            "accretion",
        )
    ):
        sci_sub = "astrophysics"
    elif any(
        w in raw
        for w in (
            "physics",
            "quantum",
            "relativity",
            "spacetime",
            "gravity",
            "force",
            "vector",
            "wave",
            "pendulum",
            "thermodynamics",
            "entropy",
            "photon",
        )
    ):
        sci_sub = "physics"
    elif any(
        w in raw
        for w in (
            "biology",
            "dna",
            "rna",
            "cell",
            "gene",
            "genetics",
            "neuron",
            "brain",
            "dopamine",
            "evolution",
            "protein",
            "hormone",
            "testosterone",
        )
    ):
        sci_sub = "biology"

    is_vs = bool(re.search(r"\b(vs\.?|versus|tier\s*list|ranked|ranking|who\s+would\s+win)\b", raw))
    comp_sub = "tier_list" if ("tier" in raw or "rank" in raw) else "vs_battle"

    if explicit in GENRE_KEYS:
        if explicit == "science":
            return "science", (sci_sub or "physics")
        if explicit == "comparison":
            return "comparison", comp_sub
        if explicit == "history":
            return "history", "timeline"
        if explicit == "parody":
            return "parody", "banter"
        if explicit == "finance":
            return "finance", "compounding"
        if explicit in ("self_improvement", "motivation"):
            return explicit, "habit_loop"

    # Parody / Satire
    if any(
        w in raw
        for w in (
            "parody",
            "satire",
            "skit",
            "comedy",
            "funny",
            "roast",
            "skeptic",
            "casually",
            "absurd",
            "meme",
            "explained badly",
        )
    ):
        return "parody", "banter"

    # Finance / Wealth (check before self-improvement so "Why Stoicism Builds Wealth" -> finance)
    if any(
        w in raw
        for w in (
            "wealth",
            "finance",
            "financial",
            "money",
            "invest",
            "compound",
            "inflation",
            "asset",
            "portfolio",
            "passive income",
            "stock",
            "market",
            "budget",
            "debt",
            "economy",
            "capital",
            "rich",
        )
    ):
        return "finance", "compounding"

    # Motivation / Self-Improvement (Dan Koe, Hormozi, Habit Loop, Value Equation)
    if any(
        w in raw
        for w in (
            "dan koe",
            "hormozi",
            "habit loop",
            "dopamine detox",
            "dopamine baseline",
            "stoic",
            "stoicism",
            "habit",
            "discipline",
            "motivation",
            "mindset",
            "focus",
            "routine",
            "productivity",
            "value equation",
            "purpose",
        )
    ):
        return "motivation", "habit_loop"

    # Comparison / VS battles (unless it's a pure science topic without clear matchup contenders)
    if is_vs and not sci_sub:
        return "comparison", comp_sub

    # Science (Chemistry, Physics, Biology, Astrophysics)
    if sci_sub or "science" in raw:
        return "science", (sci_sub or "physics")

    if is_vs:
        return "comparison", comp_sub

    # History
    if any(
        w in raw
        for w in (
            "history",
            "constantinople",
            "rome",
            "roman",
            "empire",
            "ancient",
            "medieval",
            "dynasty",
            "war",
            "siege",
            "battle",
            "revolution",
            "civilization",
            "ottoman",
            "spartan",
            "samurai",
            "viking",
            "napoleon",
            "pharaoh",
            "century",
            "crusade",
            "kingdom",
        )
    ):
        return "history", "timeline"

    return "self_improvement", "habit_loop"


def _visual_mode_for(genre: str, sub_domain: str) -> str:
    if genre == "parody":
        return "parody_duet"
    if genre == "history":
        return "history_map"
    if genre == "science":
        return f"science_{sub_domain or 'physics'}"
    if genre == "comparison":
        return "comparison_tier" if sub_domain == "tier_list" else "comparison_vs"
    if genre == "finance":
        return "finance_curve"
    if genre in ("motivation", "self_improvement"):
        return "motivation_value" if sub_domain == "value_equation" else "motivation_loop"
    return "auto"


def _synthesize_custom_spec(topic: str, genre: str | None = None) -> TopicSpec:
    cleaned = " ".join(topic.strip().split())
    slug = re.sub(r"[^a-z0-9]+", "_", cleaned.lower()).strip("_") or "custom_topic"
    inferred_genre, sub_domain = infer_genre(cleaned, genre=genre)
    base = TOPICS.get(inferred_genre, _SELF_IMPROVEMENT)

    # Extract meaningful title keywords from the custom topic
    words = [w.strip(".,:;!?()[]{}\"'") for w in cleaned.split()]
    sig_words = [
        w.title()
        for w in words
        if len(w) >= 3 and w.lower() not in {"the", "and", "for", "why", "how", "with", "from"}
    ]
    lead_kw = cleaned[:28].strip() if len(cleaned) <= 28 else (sig_words[0] if sig_words else base.title_keywords[0])
    keywords = tuple(dict.fromkeys([lead_kw, *sig_words[:3], *base.title_keywords]))

    custom_tags = [
        "#" + re.sub(r"[^a-z0-9]", "", w.lower())
        for w in sig_words[:4]
        if len(re.sub(r"[^a-z0-9]", "", w.lower())) >= 3
    ]
    merged_tags = tuple(dict.fromkeys([*custom_tags, *base.hashtags]))[:8]
    if len(merged_tags) < 5:
        merged_tags = tuple(list(merged_tags) + [t for t in _SELF_IMPROVEMENT.hashtags if t not in merged_tags])[:8]

    hook = (
        f"Everything most people believe about {cleaned} misses the one mechanism that actually drives the outcome."
    )
    points = (
        (
            "The Hidden Premise",
            (
                f"At first glance, {cleaned} looks straightforward on the surface. Yet the conventional "
                f"explanation leaves out a critical paradox that changes the entire picture. Once you "
                f"see the underlying structure, what happens next becomes impossible to ignore."
            ),
        ),
        (
            "Core Mechanism Breakdown",
            (
                f"To understand {cleaned}, we have to follow the primary forces at work step by step. "
                f"Small shifts in initial conditions amplify rapidly across the system. That feedback "
                f"loop explains why early predictions often fail right before the turning point."
            ),
        ),
        (
            "The Crucial Turning Point",
            (
                f"Here is where the conventional wisdom around {cleaned} hits a wall. When pressure "
                f"peaks, secondary constraints take over and flip the expected advantage upside down. "
                f"Look closely at the data and a clear winner begins to emerge."
            ),
        ),
        (
            "Real World Consequences",
            (
                f"The ripple effects of {cleaned} extend far beyond the textbook model. Whether you "
                f"measure efficiency, resilience, or long-term compounding impact, the gap between "
                f"average strategy and optimal execution widens every cycle."
            ),
        ),
        (
            "The Ultimate Takeaway",
            (
                f"When you connect all five pieces of {cleaned}, the lesson is remarkably practical. "
                f"Focus on the fundamental drivers, ignore superficial noise, and apply the core "
                f"principle consistently starting today."
            ),
        ),
    )

    return TopicSpec(
        key=slug,
        label=cleaned,
        title_keywords=keywords,
        hashtags=merged_tags,
        safety_note=base.safety_note,
        curated_points=points,
        curated_hook=hook,
        genre=inferred_genre,
        sub_domain=sub_domain,
        visual_mode=_visual_mode_for(inferred_genre, sub_domain),
    )


def resolve_topic_spec(
    topic: str, genre: str | None = None, allow_custom: bool = True
) -> TopicSpec:
    """Resolve a built-in topic key OR dynamically synthesize a TopicSpec for any custom topic."""
    if not isinstance(topic, str):
        raise ValueError(f"Unknown topic {topic!r}; expected one of {', '.join(TOPIC_KEYS)}")
    stripped = topic.strip()
    if stripped.lower() in _REJECTED_TOPIC_TOKENS:
        raise ValueError(f"Unknown topic {topic!r}; expected one of {', '.join(TOPIC_KEYS)}")
    slug = re.sub(r"[\s\-]+", "_", stripped.lower())
    if slug in TOPICS and not genre:
        return TOPICS[slug]
    if not allow_custom:
        raise ValueError(f"Unknown topic {topic!r}; expected one of {', '.join(TOPIC_KEYS)}")
    if slug in TOPICS and genre:
        base = TOPICS[slug]
        inferred_genre, sub_domain = infer_genre(stripped, genre=genre)
        return TopicSpec(
            key=base.key,
            label=base.label,
            title_keywords=base.title_keywords,
            hashtags=base.hashtags,
            safety_note=base.safety_note,
            curated_points=base.curated_points,
            curated_hook=base.curated_hook,
            genre=inferred_genre,
            sub_domain=sub_domain,
            visual_mode=_visual_mode_for(inferred_genre, sub_domain),
        )
    return _synthesize_custom_spec(stripped, genre=genre)


def get_topic(topic: str, allow_custom: bool = False) -> TopicSpec:
    if allow_custom:
        return resolve_topic_spec(topic, allow_custom=True)
    return TOPICS[normalize_topic(topic)]


def title_for(topic: str, subject: str = "") -> str:
    """Return a title of at most 60 chars with a heavily searched keyword first."""
    spec = resolve_topic_spec(topic, allow_custom=True)
    subject = " ".join((subject or "").split()).strip(" .:-")
    keyword = spec.title_keywords[0]
    if subject:
        lowered = subject.lower()
        # Prefer a keyword already present in the subject; move it to the front.
        for kw in spec.title_keywords:
            if kw.lower() in lowered:
                keyword = kw
                subject = re.sub(re.escape(kw), "", subject, count=1, flags=re.IGNORECASE)
                subject = " ".join(subject.split()).strip(" .:-")
                break
    title = f"{keyword}: {subject}" if subject else keyword
    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS]
        if " " in title[len(keyword) :]:
            title = title.rsplit(" ", 1)[0]
        title = title.rstrip(" :,-")
    return title


def hashtags_for(topic: str) -> list[str]:
    """5 to 8 tags describing the topic only."""
    return list(resolve_topic_spec(topic, allow_custom=True).hashtags)[:8]
