import itertools

import pytest

from autoshorts.media.ranking_overlay import (
    RankItem,
    build_ranking_ass,
    merge_ass_documents,
    ranking_timeline,
)


def test_build_ass_contains_dialogue_and_every_rank():
    items = [
        RankItem(rank=3, label="Third", start=0.0, end=2.0),
        RankItem(rank=2, label="Second", start=2.0, end=4.0),
        RankItem(rank=1, label="", start=4.0, end=6.0),
    ]
    ass = build_ranking_ass(items, title="Top 3")
    assert "[Events]" in ass
    dialogue_lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert any("RankCard" in ln for ln in dialogue_lines)
    for rank in (1, 2, 3):
        assert f"#{rank}" in ass
    assert "Third" in ass
    assert "TOP 3" in ass


def test_timeline_counts_down_and_sums_to_total():
    items = ranking_timeline(5, 10.0)
    assert [it.rank for it in items] == [5, 4, 3, 2, 1]
    assert all(it.label == "" for it in items)
    assert items[0].start == pytest.approx(0.0)
    assert items[-1].end == pytest.approx(10.0)
    total = sum(it.end - it.start for it in items)
    assert total == pytest.approx(10.0)
    for prev, cur in itertools.pairwise(items):
        assert cur.start == pytest.approx(prev.end)


def test_overlap_rejected():
    items = [
        RankItem(rank=2, label="a", start=0.0, end=3.0),
        RankItem(rank=1, label="b", start=2.5, end=5.0),
    ]
    with pytest.raises(ValueError):
        build_ranking_ass(items)


def test_empty_list_rejected():
    with pytest.raises(ValueError):
        build_ranking_ass([])


@pytest.mark.parametrize("bad_rank", [0, -1, 2.5, True])
def test_invalid_rank_rejected(bad_rank):
    items = [RankItem(rank=bad_rank, label="x", start=0.0, end=1.0)]
    with pytest.raises(ValueError):
        build_ranking_ass(items)


def test_merge_ass_documents_adds_overlay_styles_and_events():
    base = (
        "[Script Info]\nPlayResX: 1080\n\n[V4+ Styles]\nStyle: Caption,Arial,60\n\n"
        "[Events]\nDialogue: 0,0:00:00.00,0:00:01.00,Caption,,0,0,0,,hi\n"
    )
    overlay = build_ranking_ass(ranking_timeline(2, 4.0))
    merged = merge_ass_documents(base, overlay)

    assert merged.index("Style: RankCard") < merged.index("[Events]")
    assert merged.count("[Events]") == 1
    assert "Style: Caption,Arial,60" in merged
    assert merged.count("Dialogue:") == 1 + 2


def test_merge_ass_documents_requires_events_section():
    with pytest.raises(ValueError):
        merge_ass_documents("[Script Info]\n", build_ranking_ass(ranking_timeline(2, 4.0)))
