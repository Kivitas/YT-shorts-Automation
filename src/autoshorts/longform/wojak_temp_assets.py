"""On-demand, single-image Wojak character assets fetched into a temporary directory.

Nothing is cached on disk. For one video segment the flow is:

1. Pick ONE category that fits the scene, either deterministically from the
   character traits (``category_from_context``) or with the configured cloud LLM
   (``choose_category_with_llm``).
2. Inside ``temporary_wojak_asset``, list the public Drive folder to find the
   matching subfolder, pick one image from it, download only that file into a
   fresh temp directory, and yield its path.
3. Delete the temp directory when the ``with`` block exits.

The Drive fetchers are injectable (``fetch_listing`` / ``fetch_file``) so tests never
touch the network. The default fetchers need ``GOOGLE_DRIVE_API_KEY``; without it the
context manager yields ``None`` and callers fall back to their bundled characters.
"""

from __future__ import annotations

import logging
import random
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger("autoshorts.longform.wojak_temp_assets")

CATEGORIES: tuple[str, ...] = (
    "Brainiaks",
    "Brainlets",
    "Chads",
    "Doomers",
    "Female wojaks",
    "Mask wojaks",
    "More characters",
    "NPCs",
    "Political wojaks",
    "Rage wojaks",
    "Regular wojaks",
    "Soyjaks",
)

PUBLIC_FOLDER_ID = "1fjYfXHC50jUlS5n1u6Rr45Tgpp4rfYq_"
DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
PLAYWRIGHT_CHANNEL = "msedge"  # already installed on Windows; no browser download needed
USER_AGENT = "AutoShortsWojak/1.0 (local tool)"
IMAGE_SUFFIX_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
ALLOWED_IMAGE_MIMES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_REDIRECTS = 3
_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{5,128}$")

ListingFetcher = Callable[[str], list[dict[str, Any]]]
FileFetcher = Callable[[str], bytes]

_CANONICAL_BY_KEY: dict[str, str] = {name.casefold(): name for name in CATEGORIES}

# Priority-ordered whole-word keyword rules. The first category with a hit wins.
_KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Political wojaks", ("political", "politics", "politician", "election")),
    ("Mask wojaks", ("mask", "masked", "masks")),
    ("NPCs", ("npc", "npcs")),
    ("Soyjaks", ("soyjak", "soyjaks", "smug")),
    ("Brainlets", ("brainlet", "brainlets")),
    (
        "Brainiaks",
        (
            "brainiak",
            "brainiaks",
            "brainiac",
            "genius",
            "form",
            "forms",
            "paperwork",
            "clipboard",
            "bureaucracy",
            "documents",
        ),
    ),
    ("Female wojaks", ("female", "tradwife", "doomer girl", "woman", "women", "girl")),
    (
        "Rage wojaks",
        ("rage", "angry", "furious", "enraged", "shocked", "annoyed", "irritated", "outraged"),
    ),
    (
        "Doomers",
        (
            "doomer",
            "doomers",
            "sad",
            "sadness",
            "depressed",
            "hopeless",
            "tired",
            "exhausted",
            "waiting",
        ),
    ),
    (
        "Chads",
        (
            "chad",
            "chads",
            "gigachad",
            "buff",
            "muscular",
            "alpha",
            "happy",
            "cheerful",
            "confident",
            "proud",
        ),
    ),
    ("Regular wojaks", ("regular", "neutral")),
)
_KEYWORD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        category,
        re.compile(r"\b(?:" + "|".join(re.escape(k) for k in keywords) + r")\b"),
    )
    for category, keywords in _KEYWORD_RULES
)


def category_from_context(
    expression: str | None,
    archetype: str | None,
    gender: str | None,
    build: str | None,
) -> str | None:
    """Map character traits to a Drive category using fixed keyword rules.

    Returns ``None`` when no keyword matches, so the caller can ask the LLM or skip.
    """
    parts = [str(p) for p in (expression, archetype, gender, build) if p]
    text = " ".join(parts).lower().replace("_", " ").replace("-", " ")
    if not text.strip():
        return None
    for category, pattern in _KEYWORD_PATTERNS:
        if pattern.search(text):
            return category
    return None


def _parse_category_reply(reply: object) -> str | None:
    if not isinstance(reply, str):
        return None
    text = reply.strip().strip("\"'`*.").strip()
    return _CANONICAL_BY_KEY.get(text.casefold())


def _build_default_provider() -> Any | None:
    """Build a cloud LLM provider from settings, preferring the configured one.

    Returns ``None`` when no Gemini or OpenRouter key is configured or construction fails.
    """
    try:
        from autoshorts.core.config import load_config
        from autoshorts.core.providers import get_provider

        cfg = load_config()
        preferred = str(getattr(cfg.llm, "provider", "") or "").lower()
        order = [preferred] if preferred in ("gemini", "openrouter") else []
        order += [name for name in ("gemini", "openrouter") if name not in order]
        for name in order:
            key = (
                cfg.get_gemini_api_key_str()
                if name == "gemini"
                else cfg.get_openrouter_api_key_str()
            )
            if key:
                return get_provider(name=name, settings=cfg)
    except Exception as exc:
        logger.info("No LLM provider available for wojak category choice: %s", type(exc).__name__)
    return None


def choose_category_with_llm(scene_summary: str, provider: Any | None = None) -> str | None:
    """Ask the LLM to pick exactly one category for a scene. Never raises.

    Any reply that is not exactly one category name (case-insensitive, surrounding
    whitespace and quotes ignored) is rejected and yields ``None``.
    """
    try:
        active = provider if provider is not None else _build_default_provider()
        if active is None:
            return None
        scene = " ".join(str(scene_summary or "").split())[:800]
        prompt = (
            "Pick the single best Wojak character category for this video scene.\n"
            f"Scene: {scene}\n"
            f"Categories: {', '.join(CATEGORIES)}\n"
            "Answer with exactly one category name from the list above and nothing else."
        )
        reply = active.complete(prompt)
        return _parse_category_reply(reply)
    except Exception as exc:
        logger.info("LLM wojak category choice failed: %s", type(exc).__name__)
        return None


def _valid_drive_id(value: str) -> str:
    if not _DRIVE_ID_RE.match(value or ""):
        raise ValueError("invalid Drive id")
    return value


_ROWS_JS = """() => Array.from(document.querySelectorAll('tr[data-id]')).slice(0, 200).map(e => {
  const tips = Array.from(e.querySelectorAll('[data-tooltip],[aria-label]'))
    .map(x => x.getAttribute('data-tooltip') || x.getAttribute('aria-label') || '');
  const lines = (e.innerText || '').split(String.fromCharCode(10));
  return {id: e.getAttribute('data-id'), name: (lines[0] || '').trim(), tips: tips.join(' | ')};
})"""
_SIZE_RE = re.compile(r"Size:\s*([\d.,]+)\s*(bytes|KB|MB|GB)", re.IGNORECASE)
_SIZE_SCALE = {"BYTES": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}


def _parse_size(text: str) -> int | None:
    match = _SIZE_RE.search(text or "")
    if not match:
        return None
    return int(float(match.group(1).replace(",", "")) * _SIZE_SCALE[match.group(2).upper()])


def _row_to_entry(row: dict[str, Any]) -> dict[str, Any] | None:
    """Turn one rendered Drive row into the entry shape the selectors use."""
    name = str(row.get("name") or "")
    tips = str(row.get("tips") or "")
    if not name or not row.get("id"):
        return None
    if "folder" in tips.lower():
        mime = DRIVE_FOLDER_MIME
    else:
        mime = IMAGE_SUFFIX_MIME.get(Path(name).suffix.lower())
        if mime is None:
            return None
    return {"id": str(row["id"]), "name": name, "mimeType": mime, "size": _parse_size(tips)}


@contextmanager
def _browser_context() -> Iterator[Any]:
    """A fresh, profile-less Edge context (anonymous: no account cookies are used)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=PLAYWRIGHT_CHANNEL, headless=True)
        try:
            yield browser.new_context(user_agent=USER_AGENT)
        finally:
            browser.close()


_SCROLL_JS = """() => {
  const first = document.querySelector('tr[data-id]');
  let el = first ? first.parentElement : null;
  while (el && el.scrollHeight <= el.clientHeight + 4) el = el.parentElement;
  if (!el) return false;
  el.scrollTop = el.scrollTop + el.clientHeight;
  return true;
}"""
MAX_SCROLL_STEPS = 60


def _collect_all_rows(page: Any) -> list[dict[str, Any]]:
    """Scroll the Drive listing until every row has rendered (Drive virtualises long lists)."""
    seen: dict[str, dict[str, Any]] = {}
    stable = 0
    for _ in range(MAX_SCROLL_STEPS):
        for row in page.evaluate(_ROWS_JS):
            if row.get("id"):
                seen.setdefault(str(row["id"]), row)
        before = len(seen)
        page.evaluate(_SCROLL_JS)
        page.wait_for_timeout(700)
        for row in page.evaluate(_ROWS_JS):
            if row.get("id"):
                seen.setdefault(str(row["id"]), row)
        stable = stable + 1 if len(seen) == before else 0
        if stable >= 3:
            break
    return list(seen.values())


def _playwright_fetchers(context: Any) -> tuple[ListingFetcher, FileFetcher]:
    timeout_ms = int(REQUEST_TIMEOUT_SECONDS * 1000)

    def listing(folder_id: str) -> list[dict[str, Any]]:
        page = context.new_page()
        try:
            page.goto(
                f"https://drive.google.com/drive/folders/{_valid_drive_id(folder_id)}",
                wait_until="domcontentloaded",
                timeout=timeout_ms * 3,
            )
            page.wait_for_selector("tr[data-id]", timeout=timeout_ms * 2)
            rows = _collect_all_rows(page)
        finally:
            page.close()
        return [entry for entry in (_row_to_entry(r) for r in rows) if entry is not None]

    def download(file_id: str) -> bytes:
        response = context.request.get(
            f"https://drive.google.com/uc?export=download&id={_valid_drive_id(file_id)}",
            timeout=timeout_ms * 3,
            max_redirects=MAX_REDIRECTS,
        )
        return response.body() if response.status == 200 else b""

    return listing, download


def _normalise_name(name: object) -> str:
    return " ".join(str(name or "").split()).casefold()


def _match_subfolder(entries: list[dict[str, Any]], category: str) -> dict[str, Any] | None:
    wanted = _normalise_name(category)
    for entry in entries:
        if entry.get("mimeType") == DRIVE_FOLDER_MIME and _normalise_name(entry.get("name")) == (
            wanted
        ):
            return entry
    return None


def _entry_size(entry: dict[str, Any]) -> int | None:
    try:
        return int(entry["size"]) if entry.get("size") is not None else None
    except (TypeError, ValueError):
        return None


def _pick_image(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = []
    for entry in entries:
        if entry.get("mimeType") not in ALLOWED_IMAGE_MIMES or not entry.get("id"):
            continue
        size = _entry_size(entry)
        if size is not None and size > MAX_IMAGE_BYTES:
            continue
        candidates.append(entry)
    return random.choice(candidates) if candidates else None


def _canonical_category(category: str) -> str | None:
    return _CANONICAL_BY_KEY.get(_normalise_name(category))


def _fetch_one_image(
    dest_dir: Path,
    category: str,
    listing_fn: ListingFetcher,
    file_fn: FileFetcher,
) -> Path | None:
    """Find and download exactly one image for ``category`` into ``dest_dir``."""
    try:
        canonical = _canonical_category(category)
        if canonical is None:
            return None
        folder = _match_subfolder(listing_fn(PUBLIC_FOLDER_ID), canonical)
        if folder is None:
            return None
        choice = _pick_image(listing_fn(str(folder["id"])))
        if choice is None:
            return None
        data = file_fn(str(choice["id"]))
        if not isinstance(data, bytes | bytearray) or not data:
            return None
        if len(data) > MAX_IMAGE_BYTES:
            logger.info("Wojak asset rejected: larger than %d bytes", MAX_IMAGE_BYTES)
            return None
        suffix = ALLOWED_IMAGE_MIMES[str(choice["mimeType"])]
        path = dest_dir / f"wojak{suffix}"
        path.write_bytes(bytes(data))
        return path
    except Exception as exc:
        logger.info("Wojak temp asset unavailable: %s", type(exc).__name__)
        return None


@contextmanager
def temporary_wojak_asset(
    category: str,
    *,
    fetch_listing: ListingFetcher | None = None,
    fetch_file: FileFetcher | None = None,
    temp_root: Path | None = None,
) -> Iterator[Path | None]:
    """Yield the path of one Wojak image fetched for ``category``; delete it on exit.

    The default fetchers drive headless Microsoft Edge through Playwright and read the public
    Drive folder listing; no API key is needed. Injected fetchers replace them (tests use this).
    Yields ``None`` when the category is unknown, the browser cannot start, or no acceptable
    image could be fetched.
    """
    with ExitStack() as stack:
        listing_fn, file_fn = fetch_listing, fetch_file
        if listing_fn is None or file_fn is None:
            try:
                context = stack.enter_context(_browser_context())
            except Exception as exc:
                logger.info("Playwright browser unavailable: %s", type(exc).__name__)
                yield None
                return
            default_listing, default_file = _playwright_fetchers(context)
            listing_fn = listing_fn or default_listing
            file_fn = file_fn or default_file

        if temp_root is not None:
            temp_root.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix="wojak_asset_", dir=str(temp_root) if temp_root else None)
        )
        try:
            yield _fetch_one_image(temp_dir, category, listing_fn, file_fn)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\- ]+", "_", name).strip() or "wojak"
    return cleaned[:120]


def fetch_category_tree(
    dest_dir: Path,
    *,
    listing: ListingFetcher | None = None,
    file_fetch: FileFetcher | None = None,
    max_depth: int = 4,
    max_images: int = 5000,
) -> list[dict[str, Any]]:
    """Download every image in the public Wojak folder tree into ``dest_dir`` in one pass.

    Walks nested folders up to ``max_depth``. Each top-level folder is a category; files keep
    their category and sub-path so the selector can use them. Writes ``index.json`` next to
    the images and returns the index records. One browser session is used unless fetchers
    are injected (tests).
    """
    import json

    def run(list_fn: ListingFetcher, file_fn: FileFetcher) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        def walk(folder_id: str, parts: tuple[str, ...], category: str | None, depth: int) -> None:
            if depth > max_depth or len(records) >= max_images:
                return
            try:
                entries = list_fn(folder_id)
            except Exception as exc:
                logger.info(
                    "Skipping folder %s: %s", "/".join(parts) or folder_id, type(exc).__name__
                )
                return
            for entry in entries:
                if len(records) >= max_images:
                    return
                entry_id = str(entry.get("id") or "")
                if not entry_id or entry_id in seen_ids:
                    continue
                seen_ids.add(entry_id)
                name = str(entry.get("name") or entry_id)
                if entry.get("mimeType") == DRIVE_FOLDER_MIME:
                    child_category = category or _canonical_category(name) or name
                    walk(entry_id, (*parts, name), child_category, depth + 1)
                    continue
                if entry.get("mimeType") not in ALLOWED_IMAGE_MIMES:
                    continue
                size = _entry_size(entry)
                if size is not None and size > MAX_IMAGE_BYTES:
                    continue
                try:
                    data = file_fn(entry_id)
                except Exception as exc:
                    logger.info("Skipping file %s: %s", name, type(exc).__name__)
                    continue
                if (
                    not isinstance(data, bytes | bytearray)
                    or not data
                    or len(data) > MAX_IMAGE_BYTES
                ):
                    continue
                cat_name = category or "uncategorised"
                target_dir = dest_dir / _safe_filename(cat_name)
                target_dir.mkdir(parents=True, exist_ok=True)
                stem = Path(_safe_filename(name)).stem
                suffix = ALLOWED_IMAGE_MIMES[str(entry["mimeType"])]
                target = target_dir / f"{stem}_{entry_id[:8]}{suffix}"
                target.write_bytes(bytes(data))
                records.append(
                    {
                        "path": str(target.relative_to(dest_dir)).replace("\\", "/"),
                        "category": cat_name,
                        "subpath": "/".join(parts),
                        "id": entry_id,
                        "bytes": len(data),
                    }
                )

        walk(PUBLIC_FOLDER_ID, (), None, 0)
        return records

    dest_dir.mkdir(parents=True, exist_ok=True)
    if listing is not None and file_fetch is not None:
        records = run(listing, file_fetch)
    else:
        with _browser_context() as context:
            default_listing, default_file = _playwright_fetchers(context)
            records = run(listing or default_listing, file_fetch or default_file)
    (dest_dir / "index.json").write_text(json.dumps(records, indent=1), encoding="utf-8")
    return records


__all__ = [
    "CATEGORIES",
    "PUBLIC_FOLDER_ID",
    "category_from_context",
    "choose_category_with_llm",
    "fetch_category_tree",
    "temporary_wojak_asset",
]
