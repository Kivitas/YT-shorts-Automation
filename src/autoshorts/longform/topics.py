"""Topic catalogue for long-form videos: titles, hashtags, safety notes, curated content."""

from __future__ import annotations

import re
from dataclasses import dataclass

TOPIC_KEYS = ("discipline", "money", "testosterone", "science", "self_improvement")

MAX_TITLE_CHARS = 60


@dataclass(frozen=True)
class TopicSpec:
    key: str
    label: str
    title_keywords: tuple[str, ...]  # heavily searched words; the first is the default title lead
    hashtags: tuple[str, ...]
    safety_note: str
    curated_points: tuple[tuple[str, str], ...]  # (heading, narration) pairs
    curated_hook: str = ""


_DISCIPLINE = TopicSpec(
    key="discipline",
    label="Discipline",
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
    title_keywords=(
        "Brain",
        "Dopamine",
        "Science",
        "Psychology",
        "Neuroscience",
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

TOPICS: dict[str, TopicSpec] = {
    t.key: t for t in (_DISCIPLINE, _MONEY, _TESTOSTERONE, _SCIENCE, _SELF_IMPROVEMENT)
}


def normalize_topic(value: str) -> str:
    """Map user input (case, spaces, dashes) to a topic key, or raise ValueError."""
    if not isinstance(value, str):
        raise ValueError(f"Unknown topic {value!r}; expected one of {', '.join(TOPIC_KEYS)}")
    key = re.sub(r"[\s\-]+", "_", value.strip().lower())
    if key not in TOPICS:
        raise ValueError(f"Unknown topic {value!r}; expected one of {', '.join(TOPIC_KEYS)}")
    return key


def get_topic(topic: str) -> TopicSpec:
    return TOPICS[normalize_topic(topic)]


def title_for(topic: str, subject: str = "") -> str:
    """Return a title of at most 60 chars with a heavily searched keyword first."""
    spec = get_topic(topic)
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
    return list(get_topic(topic).hashtags)[:8]
