"""Comprehensive test suite for broadcast lower-third chyron overlay."""

import re
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

from autoshorts.core.config import BrandingConfig, JobDefaultsConfig
from autoshorts.core.paths import escape_ffmpeg_filter_path
from autoshorts.media.chyron_overlay import (
    ChyronData,
    build_chyron_ass,
    build_chyron_filter,
    calculate_chyron_y,
    derive_automated_chyron,
    escape_ffmpeg_drawtext,
    merge_chyron_into_ass,
)

# ---------------------------------------------------------------------------
# 1. ChyronData Initialization and Validation Tests
# ---------------------------------------------------------------------------


def test_chyron_data_defaults_and_validation():
    """Test ChyronData default field initialization and basic normalization."""
    chyron = ChyronData(headline_text="TEST HEADLINE")
    assert chyron.category_badge == "BREAKING NEWS"
    assert chyron.headline_text == "TEST HEADLINE"
    assert chyron.style == "broadcast_red"
    assert chyron.position == "seam"
    assert chyron.start_sec == 0.0
    assert chyron.duration_sec is None
    assert chyron.animation == "fade"

    # Custom attributes and normalization
    custom = ChyronData(
        category_badge="exclusive",
        headline_text="  custom headline  ",
        style="CYBER_NEON",
        position="LOWER_THIRD",
        start_sec=-5.0,
        duration_sec=12.5,
        animation="NONE",
    )
    assert custom.category_badge == "EXCLUSIVE"
    assert custom.headline_text == "custom headline"
    assert custom.style == "cyber_neon"
    assert custom.position == "lower_third"
    assert custom.start_sec == 0.0
    assert custom.duration_sec == 12.5
    assert custom.animation == "none"


def test_chyron_data_styles_validation():
    """Test ChyronData across all supported styles and fallback on invalid style."""
    for style in ("broadcast_red", "breaking_news", "minimal_dark", "cyber_neon", "alert_amber"):
        data = ChyronData(headline_text="HEADLINE", style=style)
        assert data.style == style

    # Fallback to broadcast_red on unknown style
    invalid_style = ChyronData(headline_text="HEADLINE", style="unknown_random_style")
    assert invalid_style.style == "broadcast_red"

    # Fallback to seam on unknown position
    invalid_pos = ChyronData(headline_text="HEADLINE", position="bottom_right_corner")
    assert invalid_pos.position == "seam"

    # Fallback to fade on unknown animation
    invalid_anim = ChyronData(headline_text="HEADLINE", animation="spin_zoom")
    assert invalid_anim.animation == "fade"

    # Duration clamping
    clamped_dur = ChyronData(headline_text="HEADLINE", duration_sec=-1.0)
    assert clamped_dur.duration_sec == 0.1


# ---------------------------------------------------------------------------
# 2. Automated Headline / Badge Extraction Tests
# ---------------------------------------------------------------------------


def test_derive_automated_chyron_with_clean_title():
    """Test deriving chyron from video title with clickbait and emojis."""
    title = 'BREAKING: Anthropic Researcher: "AI Could Kill Us All by End of the Decade" [MUST WATCH] 😱 #shorts'
    chyron = derive_automated_chyron(title=title)

    assert chyron.category_badge == "BREAKING NEWS"
    assert chyron.headline_text == "ANTHROPIC RESEARCHER: AI COULD KILL US ALL BY END OF THE DECADE"
    assert chyron.style == "broadcast_red"
    assert chyron.position == "seam"


def test_derive_automated_chyron_from_video_metadata():
    """Test deriving chyron when accessing video_metadata.title."""
    video_metadata = SimpleNamespace(
        title="BREAKING NEWS: Historic Treaty Signed by World Leaders in Geneva"
    )
    chyron = derive_automated_chyron(title=video_metadata.title)
    assert chyron.category_badge == "BREAKING NEWS"
    assert "HISTORIC TREATY SIGNED BY WORLD LEADERS" in chyron.headline_text


def test_derive_automated_chyron_badge_detection():
    """Test detection and normalization of various category badge prefixes."""
    # EXCLUSIVE prefix
    c1 = derive_automated_chyron(title="EXCLUSIVE: Pentagon Confirms Alien Mothership Detection")
    assert c1.category_badge == "EXCLUSIVE"
    assert c1.headline_text == "PENTAGON CONFIRMS ALIEN MOTHERSHIP DETECTION"

    # Bracketed badge prefix
    c2 = derive_automated_chyron(title="[SPECIAL REPORT] Global Energy Crisis Looms (FULL STORY)")
    assert c2.category_badge == "SPECIAL REPORT"
    assert c2.headline_text == "GLOBAL ENERGY CRISIS LOOMS"

    # DEVELOPING prefix
    c3 = derive_automated_chyron(title="DEVELOPING: Volcano Erupts in Iceland, Flights Halted")
    assert c3.category_badge == "DEVELOPING"
    assert "VOLCANO ERUPTS IN ICELAND" in c3.headline_text

    # WARNING prefix normalized to ALERT
    c4 = derive_automated_chyron(title="WARNING: Massive Solar Flare Heading for Earth")
    assert c4.category_badge == "ALERT"
    assert "MASSIVE SOLAR FLARE HEADING FOR EARTH" in c4.headline_text

    # JUST IN prefix normalized to BREAKING NEWS
    c5 = derive_automated_chyron(title="JUST IN: Central Bank Cuts Interest Rates by 50 Bps")
    assert c5.category_badge == "BREAKING NEWS"
    assert "CENTRAL BANK CUTS INTEREST RATES" in c5.headline_text


def test_derive_automated_chyron_fallback_and_cleaning():
    """Test extraction precedence, clickbait stripping, and default fallbacks."""
    # Empty inputs fallback to default badge and headline
    empty = derive_automated_chyron()
    assert empty.category_badge == "BREAKING NEWS"
    assert "SPECIAL REPORT" in empty.headline_text

    # Hook text fallback when title is missing
    hook = derive_automated_chyron(
        title=None, hook_text="Wait for it... this one trick saves 50% on flights! #travel"
    )
    assert hook.headline_text == "THIS ONE TRICK SAVES 50% ON FLIGHTS"

    # Transcript snippet fallback when title and hook are missing
    transcript = derive_automated_chyron(
        title="",
        hook_text=None,
        transcript_snippet="Scientists announced a major breakthrough in quantum computing today.",
    )
    assert transcript.category_badge == "BREAKING NEWS"
    assert "SCIENTISTS ANNOUNCED A MAJOR BREAKTHROUGH" in transcript.headline_text

    # Double quotes, smart quotes, and bracketed tags cleaning
    quotes = derive_automated_chyron(
        title='“Secret” Plan Revealed By "Tech Giant" [SHOCKING] (WATCH TILL END)'
    )
    assert quotes.headline_text == "SECRET PLAN REVEALED BY TECH GIANT"

    # Custom default badge preserved when no prefix detected
    custom_badge = derive_automated_chyron(
        title="Local Community Garden Wins National Award",
        default_badge="COMMUNITY SPOTLIGHT",
    )
    assert custom_badge.category_badge == "COMMUNITY SPOTLIGHT"
    assert "LOCAL COMMUNITY GARDEN WINS NATIONAL AWARD" in custom_badge.headline_text


def test_derive_automated_chyron_length_clamping_and_wrapping():
    """Test single-line truncation clamping and two-line wrapping for 1080px width."""
    # Single line truncation with ellipsis
    long_title = (
        "THIS IS A VERY LONG BROADCAST HEADLINE DESIGNED TO TEST CLAMPING ON A SINGLE 1080PX BANNER"
    )
    clamped = derive_automated_chyron(title=long_title, max_chars=50, wrap_two_lines=False)
    assert len(clamped.headline_text) <= 50
    assert clamped.headline_text.endswith("...")
    assert " " not in clamped.headline_text[-4:-3]  # No trailing space before ellipsis

    # Two-line wrapping
    long_text = "Scientists Have Finally Discovered The Deepest Secret Hidden In The Ocean Trench"
    chyron = derive_automated_chyron(title=long_text, wrap_two_lines=True)
    assert "\n" in chyron.headline_text
    lines = chyron.headline_text.splitlines()
    assert len(lines) == 2
    assert "SCIENTISTS HAVE" in lines[0]


# ---------------------------------------------------------------------------
# 3. ASS Subtitle Overlay Generator Tests
# ---------------------------------------------------------------------------


def test_calculate_chyron_y_positions():
    """Verify vertical Y position calculations across different layout modes."""
    # split_50_50 sits right above seam_y=960 (around Y=850-950)
    banner_y, badge_y = calculate_chyron_y(position="seam", layout_mode="split_50_50", height=1920)
    assert 850 <= banner_y <= 950
    assert badge_y < banner_y

    # split_70_30 sits right above seam_y=1344
    banner_y_70, badge_y_70 = calculate_chyron_y(
        position="seam", layout_mode="split_70_30", height=1920
    )
    assert banner_y_70 < 1344
    assert banner_y_70 + 72 <= 1344

    # lower_third sits at standard Y=1450
    banner_y_lt, _ = calculate_chyron_y(position="lower_third", height=1920)
    assert banner_y_lt == 1450

    # full_center_crop sits at standard lower-third Y=1450
    banner_y_fc, _ = calculate_chyron_y(
        position="seam", layout_mode="full_center_crop", height=1920
    )
    assert banner_y_fc == 1450

    # top sits near top safe area at Y=220
    banner_y_top, _ = calculate_chyron_y(position="top", height=1920)
    assert banner_y_top == 220


def test_build_chyron_ass():
    """Test ASS document structure, header styles, dialogue timing, and coordinates."""
    chyron = ChyronData(
        category_badge="LIVE",
        headline_text="MASSIVE PROTEST IN DOWNTOWN TOKYO",
        style="cyber_neon",
        position="seam",
        start_sec=1.5,
        duration_sec=10.0,
        animation="fade",
    )
    ass = build_chyron_ass(chyron=chyron, total_duration_sec=30.0, layout_mode="split_50_50")

    # Header and styles
    assert "[Script Info]" in ass
    assert "[V4+ Styles]" in ass
    assert "[Events]" in ass
    assert "Style: ChyronBadge" in ass
    assert "Style: ChyronHeadline" in ass

    # Dialogue timing and contents
    assert "Dialogue: 2,0:00:01.50,0:00:11.50,ChyronBadge" in ass
    assert "Dialogue: 2,0:00:01.50,0:00:11.50,ChyronHeadline" in ass
    assert "LIVE" in ass
    assert "MASSIVE PROTEST IN DOWNTOWN TOKYO" in ass
    assert r"\fad(200,200)" in ass

    # Verify coordinates sit near seam (Y=850-950)
    pos_matches = re.findall(r"\\pos\((\d+),(\d+)\)", ass)
    assert len(pos_matches) == 2
    badge_y = int(pos_matches[0][1])
    head_y = int(pos_matches[1][1])
    assert 800 <= badge_y <= 950
    assert 850 <= head_y <= 950


def test_build_chyron_ass_full_frame_and_no_animation():
    """Test ASS generation for full-frame crop and without fade animation."""
    chyron = ChyronData(
        category_badge="EXCLUSIVE",
        headline_text="TECH GIANTS ANNOUNCE HISTORIC MERGER",
        style="broadcast_red",
        position="lower_third",
        animation="none",
    )
    ass = build_chyron_ass(chyron=chyron, total_duration_sec=15.0, layout_mode="full_center_crop")
    assert r"\fad" not in ass  # No fade animation tag

    pos_matches = re.findall(r"\\pos\((\d+),(\d+)\)", ass)
    banner_y = int(pos_matches[1][1])
    assert banner_y == 1450  # Lower third coordinate for full frame


# ---------------------------------------------------------------------------
# 4. ASS Merging Tests
# ---------------------------------------------------------------------------


def test_merge_chyron_into_ass():
    """Test merging chyron overlay into existing caption ASS document."""
    base_ass = (
        "[Script Info]\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize\n"
        "Style: Default,Arial,60\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,Spoken subtitle text\n"
    )
    chyron = ChyronData(
        category_badge="EXCLUSIVE",
        headline_text="CONFIDENTIAL DOCUMENTS UNSEALED",
    )
    merged = merge_chyron_into_ass(base_ass, chyron, total_duration_sec=15.0)

    assert merged.count("[Events]") == 1
    assert "Style: Default,Arial,60" in merged
    assert "Style: ChyronBadge" in merged
    assert "Style: ChyronHeadline" in merged
    assert "Spoken subtitle text" in merged
    assert "CONFIDENTIAL DOCUMENTS UNSEALED" in merged
    assert merged.index("Style: ChyronBadge") < merged.index("[Events]")
    assert merged.index("Style: ChyronHeadline") < merged.index("[Events]")
    assert merged.count("Dialogue:") == 1 + 2


def test_merge_chyron_into_ass_requires_events_section():
    """Test that merge_chyron_into_ass raises ValueError if base ASS lacks [Events]."""
    invalid_ass = "[Script Info]\nPlayResX: 1080\n\n[V4+ Styles]\nStyle: Caption,Arial,60\n"
    chyron = ChyronData(headline_text="TEST")
    with pytest.raises(ValueError, match="base ASS document has no \\[Events\\] section"):
        merge_chyron_into_ass(invalid_ass, chyron, total_duration_sec=10.0)


# ---------------------------------------------------------------------------
# 5. FFmpeg Filtergraph Generator Tests
# ---------------------------------------------------------------------------


def test_escape_ffmpeg_drawtext():
    """Test robust two-level escaping of special characters in FFmpeg drawtext."""
    text = "TEST: 'HELLO' [WORLD]; BACK\\SLASH, PERCENT %"
    escaped = escape_ffmpeg_drawtext(text)
    assert "\\:" in escaped
    assert "\\[" in escaped
    assert "\\]" in escaped
    assert "\\;" in escaped
    assert "\\," in escaped


def test_build_chyron_filter():
    """Test FFmpeg drawbox and drawtext filterchain construction."""
    chyron = ChyronData(
        category_badge="ALERT",
        headline_text="MAJOR HURRICANE APPROACHING COASTLINE",
        style="alert_amber",
        position="seam",
        start_sec=2.0,
        duration_sec=8.0,
    )
    filter_graph, out_label = build_chyron_filter(
        chyron=chyron,
        input_label="in_stream",
        output_label="out_stream",
        width=1080,
        height=1920,
        layout_mode="split_50_50",
        duration_sec=20.0,
    )

    assert out_label == "out_stream"
    assert filter_graph.startswith("[in_stream]")
    assert filter_graph.endswith("[out_stream]")
    assert "drawbox=" in filter_graph
    assert "drawtext=" in filter_graph
    assert "between(t,2.00,10.00)" in filter_graph
    assert "ALERT" in filter_graph
    assert "MAJOR HURRICANE APPROACHING COASTLINE" in filter_graph


def test_build_chyron_filter_empty_pass_through():
    """Test that build_chyron_filter gracefully returns pass-through copy if empty."""
    empty_chyron = ChyronData(category_badge="", headline_text="")
    empty_chyron.category_badge = ""
    empty_chyron.headline_text = ""
    filter_graph, out_label = build_chyron_filter(
        chyron=empty_chyron,
        input_label="v_in",
        output_label="v_out",
    )
    assert filter_graph == "[v_in]copy[v_out]"
    assert out_label == "v_out"


def test_build_chyron_filter_multiline():
    """Test build_chyron_filter with two-line headline generates multiple drawtext filters."""
    chyron = ChyronData(
        category_badge="SPECIAL REPORT",
        headline_text="SPACE PROBE DETECTS UNKNOWN SIGNAL\nSCIENTISTS RACE TO DECODE ORIGIN",
        style="cyber_neon",
    )
    filter_graph, _ = build_chyron_filter(chyron=chyron, input_label="in", output_label="out")
    assert filter_graph.count("drawtext=") == 3  # 1 badge + 2 headline lines
    assert "SPACE PROBE DETECTS UNKNOWN SIGNAL" in filter_graph
    assert "SCIENTISTS RACE TO DECODE ORIGIN" in filter_graph


# ---------------------------------------------------------------------------
# 6. Configuration Integration Tests (BrandingConfig & JobDefaultsConfig)
# ---------------------------------------------------------------------------


def test_branding_config_chyron_fields():
    """Verify chyron configuration fields in BrandingConfig and JobDefaultsConfig."""
    branding = BrandingConfig()
    assert branding.enable_chyron is False
    assert branding.chyron_badge == "BREAKING NEWS"
    assert branding.chyron_style == "broadcast_red"

    custom_branding = BrandingConfig(
        enable_chyron=True,
        chyron_badge="EXCLUSIVE",
        chyron_style="breaking_news",
    )
    assert custom_branding.enable_chyron is True
    assert custom_branding.chyron_badge == "EXCLUSIVE"
    assert custom_branding.chyron_style == "breaking_news"

    job_defaults = JobDefaultsConfig()
    assert job_defaults.enable_chyron is False
    assert job_defaults.chyron_badge == "BREAKING NEWS"
    assert job_defaults.chyron_style == "broadcast_red"


# ---------------------------------------------------------------------------
# 7. CLI Argument Parsing Tests
# ---------------------------------------------------------------------------


def test_cli_argument_parsing():
    """Verify CLI argument parsing for --chyron, --breaking-news, --chyron-badge, --chyron-text."""
    from autoshorts.cli import build_parser

    parser = build_parser()

    # Default CLI invocation without chyron flags
    args_default = parser.parse_args(["run", "--links", "https://youtu.be/sample1"])
    assert args_default.chyron is False
    assert args_default.chyron_badge is None
    assert args_default.chyron_text is None

    # Invocation with --chyron and custom options
    args_custom = parser.parse_args(
        [
            "run",
            "--links",
            "https://youtu.be/sample2",
            "--chyron",
            "--chyron-badge",
            "DEVELOPING STORY",
            "--chyron-text",
            "MASSIVE FLOODING REPORTED ACROSS FIVE COUNTIES",
        ]
    )
    assert args_custom.chyron is True
    assert args_custom.chyron_badge == "DEVELOPING STORY"
    assert args_custom.chyron_text == "MASSIVE FLOODING REPORTED ACROSS FIVE COUNTIES"

    # Invocation with alias --breaking-news
    args_alias = parser.parse_args(
        ["run", "--links", "https://youtu.be/sample3", "--breaking-news"]
    )
    assert args_alias.chyron is True


# ---------------------------------------------------------------------------
# 8. End-to-End FFmpeg Execution Tests (ASS Subtitles & Filterchain)
# ---------------------------------------------------------------------------


def test_ffmpeg_execution_with_ass_subtitles():
    """Verify that FFmpeg can decode and render the generated ASS chyron script."""
    chyron = ChyronData(
        category_badge="BREAKING NEWS",
        headline_text="ANTHROPIC RESEARCHER: AI COULD KILL US ALL BY END OF THE DECADE",
        style="broadcast_red",
        position="seam",
    )
    ass_text = build_chyron_ass(chyron=chyron, total_duration_sec=3.0)

    with tempfile.NamedTemporaryFile(suffix=".ass", mode="w", delete=False, encoding="utf-8") as f:
        f.write(ass_text)
        ass_path = f.name

    esc = escape_ffmpeg_filter_path(ass_path)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=navy:s=1080x1920:d=1",
        "-vf",
        f"subtitles={esc}",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"FFmpeg failed rendering ASS subtitles: {res.stderr}"


def test_ffmpeg_execution_with_drawbox_drawtext():
    """Verify that FFmpeg executes the drawbox/drawtext filter chain without error."""
    chyron = ChyronData(
        category_badge="BREAKING NEWS",
        headline_text="TECH STOCKS RALLY: NASDAQ SURGES 500 POINTS",
        style="broadcast_red",
        position="seam",
    )
    filter_graph, out_label = build_chyron_filter(
        chyron=chyron,
        input_label="0:v",
        output_label="out",
        fontfile="C\\\\\\:/Windows/Fonts/arial.ttf",
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=1080x1920:d=1",
        "-filter_complex",
        filter_graph,
        "-map",
        f"[{out_label}]",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"FFmpeg filtergraph failed: {res.stderr}"
