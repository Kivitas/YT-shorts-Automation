from types import SimpleNamespace

from autoshorts.core.config import SchedulerConfig, UploadConfig
from autoshorts.scheduler.originality import (
    find_similar_title,
    resolve_altered_content,
    title_similarity,
)


def test_title_similarity_ignores_hashtags_and_stopwords():
    assert title_similarity("The secret of wealth #Shorts", "Secret of Wealth") == 1.0
    assert title_similarity("Black holes explained", "Cooking pasta fast") == 0.0
    assert title_similarity("", "anything") == 0.0


def test_find_similar_title_returns_best_match():
    recent = ["Cooking pasta fast", "5 habits of rich people", "Rich people habits you need"]
    hit = find_similar_title("5 habits of rich people!", recent, 0.7)
    assert hit is not None and hit[0] == "5 habits of rich people"
    assert find_similar_title("Quantum computing basics", recent, 0.7) is None


def _job(altered=False, diagnostics=None):
    return SimpleNamespace(altered_content=altered, diagnostics=diagnostics or {})


def test_auto_discloses_only_when_synthetic_recorded():
    cfg = UploadConfig()
    assert resolve_altered_content(_job(), cfg) is False
    assert resolve_altered_content(_job(diagnostics={"ai_generated_footage": True}), cfg) is True
    assert resolve_altered_content(_job(altered=True), cfg) is True


def test_always_and_never_modes():
    assert resolve_altered_content(_job(), UploadConfig(synthetic_disclosure="always")) is True
    never = UploadConfig(synthetic_disclosure="never")
    assert resolve_altered_content(_job(diagnostics={"synthetic_media": True}), never) is False
    assert resolve_altered_content(_job(altered=True), never) is True


def test_originality_cap_is_stricter_than_daily_cap():
    cfg = SchedulerConfig()
    assert cfg.originality.enabled
    assert min(cfg.daily_upload_cap, cfg.originality.max_daily_uploads) == 12
