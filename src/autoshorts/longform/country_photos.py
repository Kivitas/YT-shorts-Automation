"""Free-licence photo sourcing for country-themed longform scenes.

Searches Wikimedia Commons (``action=query``, ``generator=search``, File namespace), keeps only
files whose licence is CC0, Public domain, CC BY or CC BY-SA (any version), requires at least
``MIN_WIDTH`` pixels and a JPEG or PNG mime type, and downloads them under
``assets/photos/<country-slug>/``. Each country folder has a ``manifest.json`` listing every file
with its licence, licence URL, artist and the attribution string to show.

Network use is confined to ``search_country_photos``; a search whose results are already in the
manifest is answered from disk. ``pick_photo`` never touches the network and never raises, so it
is safe on the render path.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

_log = logging.getLogger(__name__)

API_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "AutoShorts/1.0 (free-licence photo sourcing)"
PHOTOS_ROOT: Path = Path(__file__).resolve().parents[3] / "assets" / "photos"

MIN_WIDTH = 1280
THUMB_WIDTH = 1920
ALLOWED_MIME = frozenset({"image/jpeg", "image/png"})
_MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png"}
_MAGIC = {"image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",)}
API_CANDIDATE_LIMIT = 50
MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024
REQUEST_TIMEOUT = 30.0
MANIFEST_NAME = "manifest.json"
_HEADERS = {"User-Agent": USER_AGENT}

LICENCE_CC0 = "CC0"
LICENCE_PD = "Public domain"
LICENCE_CC_BY = "CC BY"
LICENCE_CC_BY_SA = "CC BY-SA"

# "CC BY", "CC BY-SA", optional version, optional "International"/"Unported". NC, ND and any other
# suffix fail the fullmatch, so they are rejected.
_CC_BY_RE = re.compile(r"cc[ -]by(-sa)?(?:[ -](\d(?:\.\d)?))?(?:\s+(?:international|unported))?")
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def classify_licence(value: str | None) -> str | None:
    """Return the accepted licence family for a licence name, or ``None`` if it is not accepted."""
    if not value:
        return None
    text = _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub("", value))).strip().lower()
    if text in {"cc0", "cc0 1.0", "cc-zero", "cc0 1.0 universal"}:
        return LICENCE_CC0
    if text == "public domain":
        return LICENCE_PD
    match = _CC_BY_RE.fullmatch(text)
    if match:
        return LICENCE_CC_BY_SA if match.group(1) else LICENCE_CC_BY
    return None


def _clean_text(value: str) -> str:
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub("", value))).strip()


def _meta(ext: dict[str, Any], key: str) -> str:
    entry = ext.get(key)
    if isinstance(entry, dict):
        value = entry.get("value")
        if isinstance(value, str):
            return value
    return ""


def _attribution(
    title: str, artist: str, licence_short: str, licence_url: str, page_url: str
) -> str:
    name = title[len("File:") :] if title.startswith("File:") else title
    stem = name.rsplit(".", 1)[0].replace("_", " ")
    licence_part = licence_short + (f" ({licence_url})" if licence_url else "")
    return (
        f'"{stem}" by {artist}, licence: {licence_part}. Source: {page_url} (via Wikimedia Commons)'
    )


def _candidate_from_page(page: dict[str, Any]) -> dict[str, Any] | None:
    """Apply the licence, mime and size filters to one Commons page. ``None`` means rejected."""
    title = str(page.get("title") or "")
    infos = page.get("imageinfo")
    if not title or not isinstance(infos, list) or not infos or not isinstance(infos[0], dict):
        return None
    info = infos[0]
    ext = info.get("extmetadata") if isinstance(info.get("extmetadata"), dict) else {}

    licence_short = _clean_text(_meta(ext, "LicenseShortName") or _meta(ext, "License"))
    family = classify_licence(licence_short)
    if family is None:
        _log.debug("country_photos: rejected licence %r for %s", licence_short, title)
        return None

    mime = str(info.get("mime") or "")
    if mime not in ALLOWED_MIME:
        _log.debug("country_photos: rejected mime %r for %s", mime, title)
        return None

    width = int(info.get("width") or 0)
    if width < MIN_WIDTH:
        _log.debug("country_photos: rejected width %s for %s", width, title)
        return None

    # Prefer the sized thumbnail (THUMB_WIDTH) so the download is not the full original.
    thumb_url = info.get("thumburl")
    thumb_width = int(info.get("thumbwidth") or 0)
    if thumb_url and thumb_width >= MIN_WIDTH:
        download_url, dl_width, dl_height = (
            str(thumb_url),
            thumb_width,
            int(info.get("thumbheight") or 0),
        )
    else:
        download_url, dl_width, dl_height = (
            str(info.get("url") or ""),
            width,
            int(info.get("height") or 0),
        )
    if not download_url:
        return None

    page_url = str(info.get("descriptionurl") or "")
    licence_url = _clean_text(_meta(ext, "LicenseUrl"))
    artist = _clean_text(_meta(ext, "Artist")) or "Unknown author"
    return {
        "title": title,
        "page_url": page_url,
        "download_url": download_url,
        "width": dl_width,
        "height": dl_height,
        "mime": mime,
        "licence_short": licence_short,
        "licence_family": family,
        "licence_url": licence_url,
        "artist": artist,
        "attribution": _attribution(title, artist, licence_short, licence_url, page_url),
    }


def _slugify(country: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", country).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if not slug:
        raise ValueError(f"country name {country!r} has no usable characters")
    return slug


def _country_dir(country: str, root: Path | None) -> Path:
    return (root if root is not None else PHOTOS_ROOT) / _slugify(country)


def _filename_for(title: str, mime: str) -> str:
    name = title[len("File:") :] if title.startswith("File:") else title
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name.rsplit(".", 1)[0]).strip("._")[:100] or "photo"
    digest = hashlib.sha1(title.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"{stem}_{digest}{_MIME_EXT[mime]}"


def _new_manifest(country: str) -> dict[str, Any]:
    return {
        "country": country,
        "slug": _slugify(country),
        "user_agent": USER_AGENT,
        "searches": {},
        "files": [],
    }


def _load_manifest(base: Path, country: str) -> dict[str, Any]:
    path = base / MANIFEST_NAME
    if not path.is_file():
        return _new_manifest(country)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _log.warning("country_photos: unreadable manifest %s (%s); starting empty", path, exc)
        return _new_manifest(country)
    if not isinstance(data, dict):
        return _new_manifest(country)
    data.setdefault("searches", {})
    data.setdefault("files", [])
    return data


def _save_manifest(base: Path, manifest: dict[str, Any]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    manifest["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
    path = base / MANIFEST_NAME
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _file_ok(record: dict[str, Any], base: Path) -> bool:
    """A manifest record is usable when its file exists on disk with the recorded size."""
    try:
        path = base / str(record["filename"])
        return path.is_file() and path.stat().st_size == int(record["bytes"])
    except (KeyError, TypeError, ValueError, OSError):
        return False


def _find_file(manifest: dict[str, Any], title: str) -> dict[str, Any] | None:
    for record in manifest["files"]:
        if record.get("title") == title:
            return record
    return None


def _upsert_file(manifest: dict[str, Any], record: dict[str, Any]) -> None:
    manifest["files"] = [r for r in manifest["files"] if r.get("title") != record["title"]]
    manifest["files"].append(record)


def _records_for_titles(
    manifest: dict[str, Any], titles: Sequence[str], base: Path
) -> list[dict[str, Any]] | None:
    """Return the usable records for ``titles`` in order, or ``None`` if any is missing."""
    out: list[dict[str, Any]] = []
    for title in titles:
        record = _find_file(manifest, title)
        if record is None or not _file_ok(record, base):
            return None
        out.append(record)
    return out


def _download(url: str, dest: Path, client: httpx.Client) -> int:
    """Stream ``url`` to ``dest`` through a temp file. Returns the byte count written."""
    tmp = dest.with_name(dest.name + ".part")
    try:
        with client.stream("GET", url, headers=_HEADERS, follow_redirects=True) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype not in ALLOWED_MIME:
                raise ValueError(f"unexpected content type {ctype!r}")
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
                raise ValueError("file too large")
            total = 0
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ValueError("file too large")
                    fh.write(chunk)
        with tmp.open("rb") as fh:
            head = fh.read(8)
        if not any(head.startswith(magic) for magic in _MAGIC[ctype]):
            raise ValueError("payload does not match its image type")
        os.replace(tmp, dest)
        return total
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _query_commons(query: str, client: httpx.Client) -> list[dict[str, Any]]:
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": str(API_CANDIDATE_LIMIT),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|size|mime",
        "iiurlwidth": str(THUMB_WIDTH),
    }
    resp = client.get(API_URL, params=params, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    pages = (data.get("query") or {}).get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    pages = [p for p in pages if isinstance(p, dict)]
    pages.sort(key=lambda p: int(p.get("index") or 0))
    return pages


def _fetch_query(
    query: str,
    need: int,
    manifest: dict[str, Any],
    base: Path,
    client: httpx.Client,
) -> tuple[list[str], bool]:
    """Search Commons for ``query`` and download up to ``need`` accepted files.

    Returns the accepted titles in rank order and whether the search ran out of candidates.
    """
    pages = _query_commons(query, client)
    accepted: list[str] = []
    for page in pages:
        if len(accepted) >= need:
            break
        candidate = _candidate_from_page(page)
        if candidate is None:
            continue
        title = candidate["title"]
        existing = _find_file(manifest, title)
        if existing is not None and _file_ok(existing, base):
            accepted.append(title)
            continue
        dest_name = _filename_for(title, candidate["mime"])
        dest = base / dest_name
        base.mkdir(parents=True, exist_ok=True)
        try:
            size = _download(candidate["download_url"], dest, client)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            _log.warning("country_photos: skipping %s: %s", title, exc)
            continue
        record = {
            "title": title,
            "filename": dest_name,
            "bytes": size,
            "width": candidate["width"],
            "height": candidate["height"],
            "mime": candidate["mime"],
            "page_url": candidate["page_url"],
            "source_url": candidate["download_url"],
            "licence_short": candidate["licence_short"],
            "licence_family": candidate["licence_family"],
            "licence_url": candidate["licence_url"],
            "artist": candidate["artist"],
            "attribution": candidate["attribution"],
            "query": query,
            "retrieved": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        _upsert_file(manifest, record)
        accepted.append(title)
    exhausted = len(accepted) < need and len(pages) < API_CANDIDATE_LIMIT
    return accepted, exhausted


def _queries_for(country: str, terms: str | Sequence[str]) -> list[str]:
    term_list = [terms] if isinstance(terms, str) else list(terms)
    queries = [f"{country} {t.strip()}" for t in term_list if t and t.strip()]
    return queries or [country]


def _public_record(record: dict[str, Any], country: str, base: Path) -> dict[str, Any]:
    out = dict(record)
    out["country"] = country
    out["path"] = str((base / record["filename"]).resolve())
    return out


def search_country_photos(
    country: str,
    terms: str | Sequence[str],
    limit: int = 5,
    *,
    root: Path | None = None,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` licence-cleared photo records for ``country``.

    Each term is searched as ``"<country> <term>"``. Results already in the country manifest
    are returned from disk without a request. Network or API failures are logged and the
    records available so far are returned, so this never raises for HTTP errors.
    """
    country_name = (country or "").strip()
    if limit <= 0 or not country_name:
        return []
    base = _country_dir(country_name, root)
    manifest = _load_manifest(base, country_name)

    owns_client = client is None
    active: list[httpx.Client | None] = [client]

    def _client() -> httpx.Client:
        if active[0] is None:
            active[0] = httpx.Client(headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        return active[0]

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        for query in _queries_for(country_name, terms):
            need = limit - len(results)
            if need <= 0:
                break
            entry = manifest["searches"].get(query)
            records: list[dict[str, Any]] | None = None
            if entry is not None:
                cached = list(entry.get("titles", []))
                if len(cached) >= need or entry.get("exhausted"):
                    records = _records_for_titles(manifest, cached[:need], base)

            if records is None:
                try:
                    accepted, exhausted = _fetch_query(query, need, manifest, base, _client())
                except (httpx.HTTPError, ValueError) as exc:
                    _log.warning("country_photos: search failed for %r: %s", query, exc)
                    cached = list((entry or {}).get("titles", []))
                    records = [
                        r
                        for r in (_find_file(manifest, t) for t in cached[:need])
                        if r is not None and _file_ok(r, base)
                    ]
                else:
                    manifest["searches"][query] = {"titles": accepted, "exhausted": exhausted}
                    _save_manifest(base, manifest)
                    records = _records_for_titles(manifest, accepted, base) or []

            for record in records:
                if record["title"] in seen:
                    continue
                seen.add(record["title"])
                results.append(_public_record(record, country_name, base))
    finally:
        if owns_client and active[0] is not None:
            active[0].close()

    if not results:
        _log.info("country_photos: no licence-cleared photos found for %s", country_name)
    return results[:limit]


def pick_photo(country: str, seed: int | str, *, root: Path | None = None) -> Path | None:
    """Deterministically pick one downloaded photo for ``country`` from its manifest.

    Reads only the local manifest and files (no network). Returns ``None`` and logs when no
    usable photo exists. Never raises, so it is safe to call from the render path.
    """
    try:
        base = _country_dir((country or "").strip(), root)
        manifest_path = base / MANIFEST_NAME
        if not manifest_path.is_file():
            _log.info("country_photos: no manifest for %s; no photo to pick", country)
            return None
        manifest = _load_manifest(base, country)
        usable = [r for r in manifest["files"] if _file_ok(r, base)]
        if not usable:
            _log.info("country_photos: no downloaded photos for %s", country)
            return None
        usable.sort(key=lambda r: str(r["filename"]))
        digest = hashlib.sha256(f"{_slugify(country)}|{seed}".encode()).digest()
        index = int.from_bytes(digest[:8], "big") % len(usable)
        return (base / usable[index]["filename"]).resolve()
    except Exception as exc:  # render path: never propagate
        _log.warning("country_photos: pick_photo failed for %r: %s", country, exc)
        return None
