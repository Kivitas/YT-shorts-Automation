"""Static Safari 15+ compatibility checks for the AutoShorts dashboard frontend.

These tests read the dashboard files as plain text. They do not run Safari.
They guard against regressions in known Safari 15 gaps; see the README
"Apple Safari & WebKit Compatibility Notes" section.
"""

from __future__ import annotations

import re
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "src" / "autoshorts" / "dashboard"
STYLE_CSS = DASHBOARD_DIR / "static" / "style.css"
APP_JS = DASHBOARD_DIR / "static" / "app.js"
INDEX_HTML = DASHBOARD_DIR / "templates" / "index.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_backdrop_filter_has_webkit_prefix_on_same_or_adjacent_line() -> None:
    """(a) Every unprefixed backdrop-filter must sit next to -webkit-backdrop-filter."""
    lines = _read(STYLE_CSS).splitlines()
    unprefixed = [
        i for i, line in enumerate(lines) if re.search(r"(?<!-)backdrop-filter\s*:", line)
    ]
    assert unprefixed, "expected backdrop-filter declarations in style.css"
    missing = []
    for i in unprefixed:
        window = lines[max(0, i - 1) : i + 2]
        if not any("-webkit-backdrop-filter:" in line for line in window):
            missing.append(i + 1)
    assert not missing, (
        f"backdrop-filter without -webkit-backdrop-filter at style.css lines {missing}"
    )


def test_app_js_avoids_safari_15_4_plus_only_apis() -> None:
    """(b) app.js must not use APIs that Safari 15.0-15.3 lacks."""
    js = _read(APP_JS)
    banned = [".at(", "Object.hasOwn(", "structuredClone(", "findLast(", "static {"]
    found = [token for token in banned if token in js]
    assert not found, f"Safari 15.4+ only tokens found in app.js: {found}"


def test_index_html_has_viewport_meta() -> None:
    """(c) The served index.html must declare a viewport meta tag for iOS/Safari layout."""
    html = _read(INDEX_HTML)
    assert '<meta name="viewport"' in html


def test_no_raw_space_separated_timestamps_fed_to_new_date() -> None:
    """(d) Static guard: no new Date() call receives a raw space-separated timestamp.

    Limits: this is line-based regex matching. It catches string literals that
    contain "YYYY-MM-DD HH:MM" and direct member arguments such as
    new Date(job.updated_at). It cannot follow values through variables, so a
    raw timestamp stored in a variable and passed later is not detected.
    The conversion itself is checked by asserting parseSafariSafeDate exists
    and replaces the space with 'T'.
    """
    js = _read(APP_JS)

    literal_ts = re.compile(r"new Date\(\s*(['\"`])\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}")
    raw_field = re.compile(r"new Date\(\s*[\w.\[\]'\"]*\.(\w*_at|\w*_time|\w*_date)\s*\)")
    offenders = []
    for lineno, line in enumerate(js.splitlines(), start=1):
        if literal_ts.search(line) or raw_field.search(line):
            offenders.append(lineno)
    assert not offenders, f"raw timestamp passed to new Date() at app.js lines {offenders}"

    assert "function parseSafariSafeDate" in js
    assert re.search(r's\.replace\(/\\s\+/,\s*"T"\)', js), (
        "space-to-T conversion missing in parseSafariSafeDate"
    )
