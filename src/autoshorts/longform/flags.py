"""National Flag Loader and Formatter for Wojak Meme Videos.

Loads high-resolution raster flag PNGs from assets/flags/<iso>.png, scales them
proportionally to target header height, applies a crisp 1px neutral border,
and provides seamless ISO code resolution for 40+ countries.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

FLAGS_DIR: Final[Path] = Path(__file__).resolve().parents[3] / "assets" / "flags"

COUNTRY_TO_ISO: Final[dict[str, str]] = {
    "usa": "us",
    "united states": "us",
    "america": "us",
    "uk": "gb",
    "united kingdom": "gb",
    "britain": "gb",
    "england": "gb",
    "scotland": "gb",
    "wales": "gb",
    "germany": "de",
    "deutschland": "de",
    "canada": "ca",
    "japan": "jp",
    "france": "fr",
    "australia": "au",
    "india": "in",
    "italy": "it",
    "brazil": "br",
    "switzerland": "ch",
    "sweden": "se",
    "poland": "pl",
    "norway": "no",
    "spain": "es",
    "netherlands": "nl",
    "holland": "nl",
    "new zealand": "nz",
    "mexico": "mx",
    "south korea": "kr",
    "korea": "kr",
    "china": "cn",
    "finland": "fi",
    "greece": "gr",
    "argentina": "ar",
    "ireland": "ie",
    "austria": "at",
    "belgium": "be",
    "portugal": "pt",
    "denmark": "dk",
    "turkey": "tr",
    "south africa": "za",
    "singapore": "sg",
    "russia": "ru",
    "egypt": "eg",
    "thailand": "th",
    "vietnam": "vn",
    "philippines": "ph",
    "indonesia": "id",
    "malaysia": "my",
    "pakistan": "pk",
    "saudi arabia": "sa",
}


def resolve_iso_code(country_name: str) -> str | None:
    """Resolve a country name to its 2-letter ISO 3166-1 alpha-2 code."""
    c_clean = country_name.strip().lower()
    if c_clean in COUNTRY_TO_ISO:
        return COUNTRY_TO_ISO[c_clean]
    for key, iso in COUNTRY_TO_ISO.items():
        if key in c_clean or c_clean in key:
            return iso
    return None


def get_country_flag(country_name: str, target_height: int = 50) -> Image.Image | None:
    """Load, scale, and border a country flag image for video header banners.

    Args:
        country_name: Name of the country (e.g. 'USA', 'Germany', 'South Korea').
        target_height: Height of the flag in pixels (width scales proportionally).

    Returns:
        RGBA Image of the flag with a subtle 1px border, or None if unavailable.
    """
    iso = resolve_iso_code(country_name)
    if not iso:
        return None

    flag_path = FLAGS_DIR / f"{iso}.png"
    if not flag_path.is_file():
        # Try downloading if missing
        try:
            import urllib.request
            FLAGS_DIR.mkdir(parents=True, exist_ok=True)
            url = f"https://flagcdn.com/w160/{iso}.png"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                flag_path.write_bytes(resp.read())
        except Exception as exc:
            logger.debug("Could not fetch flag for %s (%s): %s", country_name, iso, exc)
            return None

    try:
        with Image.open(flag_path) as im:
            rgba = im.convert("RGBA")
            aspect = rgba.width / max(1, rgba.height)
            target_width = max(1, round(target_height * aspect))
            resized = rgba.resize((target_width, target_height), Image.Resampling.LANCZOS)
            # Add subtle 1px border so light flags (like Japan or Finland) contrast on white headers
            return ImageOps.expand(resized, border=1, fill=(210, 215, 222, 255))
    except Exception as exc:
        logger.warning("Error processing flag for %s: %s", country_name, exc)
        return None
