"""Playwright Microsoft Edge YouTube Studio Browser Automation Engine.

Implements browser-driven upload and authentication via YouTube Studio web interface
using Microsoft Edge persistent profiles with anti-automation flags (PLAN.md section 8.1 & 8.4).
Zero Google API quota consumption fallback for restricted or exhausted API projects.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from autoshorts.core.config import Settings
from autoshorts.core.exceptions import UploadError
from autoshorts.core.models import UploadResult
from autoshorts.core.paths import get_data_dir

logger = logging.getLogger(__name__)

_LOGIN_STATE_LOCK = threading.Lock()
LOGIN_SESSION_STATE: dict[str, Any] = {
    "status": "idle",
    "message": "",
    "error": None,
    "channel_name": None,
}


def get_login_session_state() -> dict[str, Any]:
    """Return a thread-safe copy of LOGIN_SESSION_STATE."""
    with _LOGIN_STATE_LOCK:
        return dict(LOGIN_SESSION_STATE)


def update_login_session_state(**kwargs: Any) -> dict[str, Any]:
    """Update LOGIN_SESSION_STATE in a thread-safe manner."""
    with _LOGIN_STATE_LOCK:
        LOGIN_SESSION_STATE.update(kwargs)
        return dict(LOGIN_SESSION_STATE)


class PlaywrightStudioUploader:
    """YouTube Studio Playwright browser upload engine using Microsoft Edge.

    Uses dedicated persistent profile in ./data/edge_profile and msedge channel.
    Operates without API quotas by automating the YouTube Studio desktop web app.
    """

    def __init__(
        self,
        edge_profile_dir: Path | str | None = None,
        channel: str = "msedge",
        headless: bool = False,
        config: Settings | None = None,
        context_factory: Callable[..., Any] | None = None,
        profile_dir: Path | str | None = None,
    ) -> None:
        """Initialize Studio uploader with dedicated Edge profile directory.

        Args:
            edge_profile_dir: Path to isolated Edge automation user data directory.
            channel: Browser channel (default 'msedge').
            headless: Whether to run Edge headless (False for interactive sessions).
            config: Optional Settings instance for default privacy/templates.
            context_factory: Optional mockable factory callable returning browser context.
            profile_dir: Optional alias/override for Edge automation user data directory.
        """
        self.config = config
        target_dir = profile_dir or edge_profile_dir
        self.edge_profile_dir = (
            Path(target_dir).resolve()
            if target_dir
            else (get_data_dir() / "edge_profile").resolve()
        )
        self.profile_dir = self.edge_profile_dir  # Backward-compatible alias
        self.channel = channel
        self.headless = headless
        self.context_factory = context_factory
        # Set by verify(); Studio results are DOM-derived, never API-confirmed.
        self.last_verification_kind: str = "unverified"

    def _get_playwright(self) -> Any:
        """Lazy import playwright sync API."""
        try:
            from playwright.sync_api import sync_playwright

            return sync_playwright
        except ImportError as e:
            raise ImportError(
                "Playwright is not installed. To use YouTube Studio browser automation fallback, "
                f"install playwright: 'uv pip install playwright' and 'playwright install msedge': {e}"
            ) from e

    @contextmanager
    def _launch_browser_session(self, headless: bool) -> Iterator[tuple[Any, Any]]:
        """Launch or return persistent browser context and active main page.

        Yields:
            Tuple of (BrowserContext, Page).
        """
        args = ["--disable-blink-features=AutomationControlled"]
        if self.context_factory is not None:
            try:
                ctx = self.context_factory(
                    user_data_dir=str(self.edge_profile_dir),
                    channel=self.channel,
                    headless=headless,
                    args=args,
                )
            except Exception as e:
                logger.warning(
                    "context_factory failed with channel=%r: %s. Retrying without channel.",
                    self.channel,
                    e,
                )
                ctx = self.context_factory(
                    user_data_dir=str(self.edge_profile_dir),
                    headless=headless,
                    args=args,
                )
            page = (
                ctx.pages[0]
                if (hasattr(ctx, "pages") and ctx.pages)
                else (ctx.new_page() if hasattr(ctx, "new_page") else ctx)
            )
            try:
                yield ctx, page
            finally:
                if hasattr(ctx, "close") and callable(ctx.close):
                    try:
                        ctx.close()
                    except Exception:
                        pass
            return

        sync_playwright = self._get_playwright()
        self.edge_profile_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            context = None
            if self.channel:
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=str(self.edge_profile_dir),
                        channel=self.channel,
                        headless=headless,
                        args=args,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to launch browser with channel=%r: %s. Falling back to default chromium.",
                        self.channel,
                        e,
                    )
                    context = None

            if context is None:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.edge_profile_dir),
                    headless=headless,
                    args=args,
                )

            try:
                page = context.pages[0] if context.pages else context.new_page()
                yield context, page
            finally:
                context.close()

    def _extract_channel_name(self, page: Any) -> str | None:
        """Attempt to extract YouTube channel or creator name from Studio DOM."""
        selectors = [
            "#entity-name",
            "ytcp-entity-badge #entity-name",
            "ytcp-app #entity-name",
            "#channel-title",
            "button#avatar-btn img",
            "img.ytcp-avatar",
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if hasattr(loc, "first"):
                    loc = loc.first
                is_vis = loc.is_visible() if callable(getattr(loc, "is_visible", None)) else False
                if is_vis is True or (is_vis and not isinstance(is_vis, type(loc))):
                    text = ""
                    if hasattr(loc, "inner_text") and callable(loc.inner_text):
                        text = (loc.inner_text() or "").strip()
                    if text:
                        return text
                    if hasattr(loc, "get_attribute") and callable(loc.get_attribute):
                        alt = loc.get_attribute("alt") or loc.get_attribute("aria-label") or ""
                        if alt and isinstance(alt, str) and alt.strip():
                            clean = re.sub(
                                r"^(Google Account:\s*|Avatar image for\s*)",
                                "",
                                alt.strip(),
                                flags=re.IGNORECASE,
                            )
                            if clean:
                                return clean
            except Exception:
                pass
        return None

    def _has_saved_profile(self) -> bool:
        """Check if Edge profile directory exists and contains cookies or session state."""
        if self.context_factory is not None:
            return True
        if not self.edge_profile_dir.is_dir():
            return False

        cookie_candidates = [
            self.edge_profile_dir / "Default" / "Network" / "Cookies",
            self.edge_profile_dir / "Default" / "Cookies",
            self.edge_profile_dir / "Default" / "Preferences",
            self.edge_profile_dir / "Default" / "Local Storage",
            self.edge_profile_dir / "Local State",
        ]
        if any(p.exists() for p in cookie_candidates):
            for cand in [
                self.edge_profile_dir / "Default" / "Network" / "Cookies",
                self.edge_profile_dir / "Default" / "Cookies",
            ]:
                if cand.is_file():
                    try:
                        import sqlite3

                        uri = f"file:{cand.as_posix()}?mode=ro&immutable=1"
                        with sqlite3.connect(uri, uri=True, timeout=0.5) as conn:
                            cur = conn.cursor()
                            cur.execute(
                                "SELECT 1 FROM cookies WHERE host_key LIKE '%google%' OR host_key LIKE '%youtube%' LIMIT 1"
                            )
                            row = cur.fetchone()
                            if row is not None:
                                return True
                    except Exception:
                        return True
            return True

        try:
            return any(self.edge_profile_dir.iterdir())
        except Exception:
            return False

    def check_login_status(self) -> dict[str, Any]:
        """Check if Edge persistent profile is authenticated with YouTube Studio.

        Checks if ./data/edge_profile exists and contains cookies or session state.
        Performs a quick headless check to https://studio.youtube.com with an 8-second timeout.
        If it redirects to accounts.google.com, returns {"signed_in": False, "channel_name": None}.
        If it lands on Studio, attempts to extract creator name or returns {"signed_in": True, "channel_name": "Active Creator"}.

        Returns:
            dict with 'signed_in' (bool) and 'channel_name' (str | None).
        """
        if not self._has_saved_profile():
            return {"signed_in": False, "channel_name": None}

        # Avoid collision if another process is actively running the interactive session
        if LOGIN_SESSION_STATE.get("status") == "running" and self.context_factory is None:
            return {
                "signed_in": False,
                "channel_name": LOGIN_SESSION_STATE.get("channel_name"),
            }

        try:
            with self._launch_browser_session(headless=True) as (_context, page):
                timeout_ms = 8000
                if hasattr(page, "set_default_timeout") and callable(page.set_default_timeout):
                    page.set_default_timeout(timeout_ms)

                try:
                    page.goto(
                        "https://studio.youtube.com",
                        timeout=timeout_ms,
                        wait_until="domcontentloaded",
                    )
                except Exception as goto_err:
                    logger.debug("check_login_status goto warning: %s", goto_err)

                raw_url = (
                    page.url() if callable(getattr(page, "url", None)) else getattr(page, "url", "")
                )
                url = raw_url if isinstance(raw_url, str) else str(raw_url)

                if "accounts.google.com" in url:
                    return {"signed_in": False, "channel_name": None}

                channel_name = self._extract_channel_name(page)
                if "studio.youtube.com" in url or channel_name is not None:
                    return {
                        "signed_in": True,
                        "channel_name": channel_name or "Active Creator",
                    }

                for selector in [
                    "ytcp-button#create-icon",
                    "#upload-icon",
                    "#avatar-btn",
                    "button#avatar-btn",
                    "img.ytcp-avatar",
                ]:
                    try:
                        loc = page.locator(selector)
                        if hasattr(loc, "first"):
                            loc = loc.first
                        is_vis = (
                            loc.is_visible()
                            if callable(getattr(loc, "is_visible", None))
                            else False
                        )
                        if is_vis is True or (is_vis and not isinstance(is_vis, type(loc))):
                            return {
                                "signed_in": True,
                                "channel_name": channel_name or "Active Creator",
                            }
                    except Exception:
                        pass

                return {"signed_in": False, "channel_name": None}

        except Exception as e:
            logger.debug("check_login_status error: %s", e)
            return {"signed_in": False, "channel_name": None}

    def open_login_session(self, timeout_sec: float = 300.0) -> bool:
        """Launch headed MS Edge to let operator log into YouTube Studio manually.

        Waits until YouTube Studio avatar or upload icon is detected or browser window
        is closed or timeout is reached. Cookies and auth tokens are persisted cleanly
        in edge_profile_dir. Never touches or types operator passwords.

        Args:
            timeout_sec: Maximum seconds to wait for operator login completion.

        Returns:
            True if YouTube Studio authenticated session is detected, False otherwise.
        """
        logger.info(
            "Opening MS Edge interactive login session for profile at %s...",
            self.edge_profile_dir,
        )
        update_login_session_state(
            status="running",
            message="Microsoft Edge opened. Please sign in to Google/YouTube Studio...",
            error=None,
            channel_name=None,
        )
        detected = False
        window_closed = False
        channel_name: str | None = None
        try:
            with self._launch_browser_session(headless=False) as (_context, page):
                try:
                    page.goto("https://studio.youtube.com", wait_until="domcontentloaded")
                except Exception as goto_err:
                    logger.debug("Goto studio.youtube.com warning: %s", goto_err)

                start_time = time.time()
                while time.time() - start_time < timeout_sec:
                    if hasattr(page, "is_closed") and callable(page.is_closed) and page.is_closed():
                        logger.info("Browser window closed by operator.")
                        window_closed = True
                        break

                    raw_url = (
                        page.url()
                        if callable(getattr(page, "url", None))
                        else getattr(page, "url", "")
                    )
                    url = raw_url if isinstance(raw_url, str) else str(raw_url)
                    if "accounts.google.com" not in url:
                        # Check for authenticated Studio indicators
                        for selector in [
                            "ytcp-button#create-icon",
                            "#upload-icon",
                            "#avatar-btn",
                            "button#avatar-btn",
                            "img.ytcp-avatar",
                        ]:
                            try:
                                loc = page.locator(selector)
                                if hasattr(loc, "first"):
                                    loc = loc.first
                                is_vis = (
                                    loc.is_visible()
                                    if callable(getattr(loc, "is_visible", None))
                                    else False
                                )
                                if is_vis is True or (is_vis and not isinstance(is_vis, type(loc))):
                                    logger.info(
                                        "Successfully detected authenticated YouTube Studio session!"
                                    )
                                    detected = True
                                    channel_name = (
                                        self._extract_channel_name(page) or "Active Creator"
                                    )
                                    break
                            except Exception:
                                pass
                        if detected:
                            break

                    time.sleep(1.0)

                # Final post-loop check
                if not detected and not window_closed:
                    is_closed = (
                        page.is_closed() if callable(getattr(page, "is_closed", None)) else False
                    )
                    if is_closed:
                        window_closed = True
                    else:
                        for selector in ["ytcp-button#create-icon", "#upload-icon", "#avatar-btn"]:
                            try:
                                loc = page.locator(selector)
                                if hasattr(loc, "first"):
                                    loc = loc.first
                                is_vis = (
                                    loc.is_visible()
                                    if callable(getattr(loc, "is_visible", None))
                                    else False
                                )
                                if is_vis is True or (is_vis and not isinstance(is_vis, type(loc))):
                                    detected = True
                                    channel_name = (
                                        self._extract_channel_name(page) or "Active Creator"
                                    )
                                    break
                            except Exception:
                                pass

            if detected:
                ch_name = channel_name or "Active Creator"
                update_login_session_state(
                    status="signed_in",
                    message="Successfully signed in to YouTube Studio! Session saved.",
                    channel_name=ch_name,
                    error=None,
                )
                return True
            else:
                msg = (
                    "Edge closed before sign-in completed."
                    if window_closed
                    else "Edge login session timed out before sign-in completed."
                )
                update_login_session_state(
                    status="not_signed_in",
                    message=msg,
                    error=None,
                    channel_name=None,
                )
                return False

        except Exception as e:
            logger.error("Failed during Edge login session: %s", e)
            update_login_session_state(
                status="error",
                message=str(e),
                error=str(e),
                channel_name=None,
            )
            return False

    def upload(
        self,
        path: Path | None = None,
        title: str = "",
        description: str = "",
        tags: Sequence[str] | None = None,
        privacy: str = "unlisted",
        *,
        video_path: Path | None = None,
        privacy_status: str | None = None,
        altered_content: bool = False,
        related_video_url: str | None = None,
        publish_at: str | None = None,
        profile_dir: Path | str | None = None,
        **kwargs: Any,
    ) -> UploadResult:
        """Upload video via YouTube Studio web interface using MS Edge persistent profile.

        Args:
            path: Target MP4 video path (Uploader protocol compatibility).
            title: YouTube Shorts title.
            description: Description with source attribution and hashtags.
            tags: Video tags.
            privacy: Visibility status ('private', 'unlisted', 'public').
            video_path: Keyword alias for target video path.
            privacy_status: Keyword alias for visibility status.
            altered_content: Whether to disclose synthetic or altered content in Details.
            related_video_url: Long-form source video link to attach as Shorts Related Video.
            publish_at: ISO-8601 UTC timestamp string for native scheduled publishing.
            profile_dir: Optional Edge profile user data directory override.

        Returns:
            UploadResult with assigned YouTube video ID and shorts URL.

        Raises:
            UploadError: If file not found, unauthenticated redirect occurs, or DOM automation fails.
        """
        if profile_dir is not None:
            self.edge_profile_dir = Path(profile_dir).resolve()
            self.profile_dir = self.edge_profile_dir

        target_path = Path(video_path if video_path is not None else (path or ""))
        if not target_path.is_file():
            raise UploadError(f"Video file does not exist: {target_path}")

        default_privacy = (
            (
                getattr(self.config.upload, "default_privacy_status", None)
                or getattr(self.config.upload, "privacy_status", None)
                or "unlisted"
            )
            if self.config and hasattr(self.config, "upload")
            else "unlisted"
        )
        effective_privacy = str(privacy_status or privacy or default_privacy).strip().lower()
        effective_tags = list(tags) if tags is not None else []

        logger.info(
            "Starting YouTube Studio Playwright upload for %s (title=%r, privacy=%s)",
            target_path,
            title,
            effective_privacy,
        )

        with self._launch_browser_session(headless=self.headless) as (_context, page):
            try:
                page.goto("https://studio.youtube.com", wait_until="domcontentloaded")

                # Validate authenticated session
                raw_url = (
                    page.url() if callable(getattr(page, "url", None)) else getattr(page, "url", "")
                )
                current_url = raw_url if isinstance(raw_url, str) else str(raw_url)
                if "accounts.google.com" in current_url:
                    raise UploadError(
                        "MS Edge profile is not signed in. Run 'autoshorts edge-login' first."
                    )

                # Trigger upload dialog: Create -> Upload videos or direct file chooser
                try:
                    create_btn = page.locator("ytcp-button#create-icon, #create-icon").first
                    if create_btn.is_visible(timeout=5000):
                        create_btn.click()
                        upload_item = page.locator(
                            "#text-item-0, ytcp-text-menu #text-item-0"
                        ).first
                        if upload_item.is_visible(timeout=3000):
                            upload_item.click()
                except Exception as ex:
                    logger.debug("Create button click bypass: %s", ex)

                # Set file input
                file_input = page.locator("input[type='file']").first
                file_input.set_input_files(str(target_path.resolve()))

                # Ensure #Shorts is in title
                shorts_title = (
                    title if ("#Shorts" in title or "#shorts" in title) else f"{title} #Shorts"
                )

                # Fill title textbox (first #textbox)
                title_box = page.locator("#textbox").nth(0)
                if hasattr(title_box, "wait_for") and callable(title_box.wait_for):
                    title_box.wait_for(state="visible", timeout=30000)
                title_box.fill(shorts_title)

                # Fill description textbox (second #textbox)
                if description:
                    try:
                        desc_box = page.locator("#textbox").nth(1)
                        if desc_box.is_visible(timeout=5000):
                            desc_box.fill(description)
                    except Exception as ex:
                        logger.debug("Description box fill warning: %s", ex)

                # Mark "No, it's not made for kids"
                try:
                    not_mfk = page.locator(
                        'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"], [name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]'
                    ).first
                    not_mfk.click()
                except Exception as ex:
                    logger.debug("Made for kids selection warning: %s", ex)

                # Altered Content Selection in Details
                try:
                    toggle_btn = page.locator(
                        "#toggle-button, ytcp-button#toggle-button, button:has-text('Show more')"
                    ).first
                    if hasattr(toggle_btn, "is_visible") and toggle_btn.is_visible(timeout=2500):
                        toggle_btn.click()
                        if hasattr(page, "wait_for_timeout") and callable(page.wait_for_timeout):
                            page.wait_for_timeout(400)
                        else:
                            time.sleep(0.4)

                    if altered_content:
                        alt_radio = page.locator(
                            'tp-yt-paper-radio-button[name="IS_ALTERED_CONTENT"], '
                            'tp-yt-paper-radio-button[name="ALTERED_CONTENT_YES"], '
                            'tp-yt-paper-radio-button:has-text("Yes")'
                        ).first
                    else:
                        alt_radio = page.locator(
                            'tp-yt-paper-radio-button[name="NOT_ALTERED_CONTENT"], '
                            'tp-yt-paper-radio-button[name="ALTERED_CONTENT_NO"], '
                            'tp-yt-paper-radio-button:has-text("No")'
                        ).first

                    if (
                        hasattr(alt_radio, "is_visible")
                        and alt_radio.is_visible(timeout=2500)
                        or hasattr(alt_radio, "click")
                    ):
                        alt_radio.click()
                except Exception as ex:
                    logger.debug("Altered content selection warning: %s", ex)

                # Step 1: Advance to Video elements step
                next_btn = page.locator("#next-button").first
                if next_btn.is_visible(timeout=8000):
                    next_btn.click()
                    if hasattr(page, "wait_for_timeout") and callable(page.wait_for_timeout):
                        page.wait_for_timeout(800)
                    else:
                        time.sleep(0.8)

                # Related Video Link in Video elements step
                if related_video_url:
                    try:
                        rel_btn = page.locator(
                            "#related-video-button, ytcp-button#related-video-button, "
                            "ytcp-video-metadata-editor-sidepanel #related-video-button, "
                            "button[aria-label*='Related video'], button:has-text('Related video'), "
                            "#add-related-video-button"
                        ).first
                        if hasattr(rel_btn, "is_visible") and rel_btn.is_visible(timeout=4000):
                            rel_btn.click()
                            search_box = page.locator(
                                "input#search-input, input[type='text'], #search-box input, #textbox"
                            ).first
                            if hasattr(search_box, "is_visible") and search_box.is_visible(
                                timeout=3000
                            ):
                                search_box.fill(str(related_video_url))
                                if hasattr(page, "keyboard") and hasattr(page.keyboard, "press"):
                                    page.keyboard.press("Enter")
                                first_res = page.locator(
                                    "ytcp-video-row, div.video-row, div#results-container ytcp-entity-badge"
                                ).first
                                if hasattr(first_res, "is_visible") and first_res.is_visible(
                                    timeout=2500
                                ):
                                    first_res.click()
                    except Exception as ex:
                        logger.debug("Related video link warning: %s", ex)

                # Step 2 & 3: Advance through Checks to Visibility step
                for _ in range(2):
                    if next_btn.is_visible(timeout=8000):
                        next_btn.click()
                        if hasattr(page, "wait_for_timeout") and callable(page.wait_for_timeout):
                            page.wait_for_timeout(800)
                        else:
                            time.sleep(0.8)

                # Visibility selection or scheduled publishing
                is_scheduled = False
                if publish_at:
                    try:
                        sched_radio = page.locator(
                            'tp-yt-paper-radio-button[name="SCHEDULE"], #schedule-radio-button, [name="SCHEDULE"]'
                        ).first
                        if hasattr(sched_radio, "is_visible") and sched_radio.is_visible(
                            timeout=3000
                        ):
                            sched_radio.click()
                            is_scheduled = True
                            try:
                                date_in = page.locator(
                                    "input[aria-label*='Date'], #datepicker-trigger input"
                                ).first
                                if hasattr(date_in, "is_visible") and date_in.is_visible(
                                    timeout=1500
                                ):
                                    date_in.fill(str(publish_at)[:10])
                            except Exception:
                                pass
                    except Exception as ex:
                        logger.debug("Schedule radio selection warning: %s", ex)

                if not is_scheduled:
                    clean_privacy = effective_privacy.upper()
                    if clean_privacy in ("PUBLIC", "UNLISTED", "PRIVATE"):
                        vis_key = clean_privacy
                    elif "PUB" in clean_privacy:
                        vis_key = "PUBLIC"
                    elif "PRIV" in clean_privacy:
                        vis_key = "PRIVATE"
                    else:
                        vis_key = "UNLISTED"

                    logger.info("Selecting YouTube Studio visibility radio: %s", vis_key)
                    try:
                        vis_selector = (
                            f'tp-yt-paper-radio-button[name="{vis_key}"], [name="{vis_key}"]'
                        )
                        vis_radio = page.locator(vis_selector).first
                        if hasattr(vis_radio, "wait_for") and callable(vis_radio.wait_for):
                            try:
                                vis_radio.wait_for(state="visible", timeout=5000)
                            except Exception:
                                pass
                        is_vis = (
                            vis_radio.is_visible(timeout=5000)
                            if callable(getattr(vis_radio, "is_visible", None))
                            else True
                        )
                        if is_vis:
                            vis_radio.click()
                        else:
                            vis_radio.click()
                    except Exception as ex:
                        logger.warning("Could not select visibility radio %s: %s", vis_key, ex)

                # Extract video ID from link: a.style-scope.ytcp-video-info
                video_id = ""
                try:
                    video_link = page.locator(
                        "a.style-scope.ytcp-video-info, a.ytcp-video-info"
                    ).first
                    if video_link.is_visible(timeout=5000):
                        raw_href = video_link.get_attribute("href") or video_link.inner_text() or ""
                        href = raw_href if isinstance(raw_href, str) else ""
                        match = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", href) or re.search(
                            r"v=([a-zA-Z0-9_-]{11})", href
                        )
                        if match:
                            video_id = match.group(1)
                except Exception as ex:
                    logger.debug("Failed extracting video link from DOM: %s", ex)

                if not video_id:
                    # Fallback to page content scan
                    raw_content = (
                        page.content()
                        if hasattr(page, "content") and callable(page.content)
                        else ""
                    )
                    content = raw_content if isinstance(raw_content, str) else ""
                    match = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", content) or re.search(
                        r"shorts/([a-zA-Z0-9_-]{11})", content
                    )
                    if match:
                        video_id = match.group(1)
                    else:
                        video_id = f"studio_{uuid4().hex[:11]}"

                # Complete upload wizard (clicks #done-button, which is labeled "Publish" when Public is selected)
                done_btn = page.locator("#done-button, #save-button").first
                try:
                    if hasattr(done_btn, "wait_for") and callable(done_btn.wait_for):
                        try:
                            done_btn.wait_for(state="visible", timeout=8000)
                        except Exception:
                            pass
                    is_done_vis = (
                        done_btn.is_visible(timeout=5000)
                        if callable(getattr(done_btn, "is_visible", None))
                        else True
                    )
                    if is_done_vis:
                        done_btn.click()
                    else:
                        done_btn.click()
                    if hasattr(page, "wait_for_timeout") and callable(page.wait_for_timeout):
                        page.wait_for_timeout(1500)
                    else:
                        time.sleep(1.5)
                except Exception as ex:
                    logger.warning("Error clicking done/publish button: %s", ex)

                ret_privacy = "scheduled" if is_scheduled else effective_privacy
                # Studio automation is a WEAKER success signal than the Data API.
                # The video id (and even its creation) is inferred by scraping the Studio
                # DOM, so this must never be reported as YouTube-confirmed.
                logger.warning(
                    "Job upload for %s completed via YouTube Studio automation; the video id "
                    "was parsed from the Studio DOM, not confirmed by the Data API.",
                    video_id,
                )
                return UploadResult(
                    video_id=video_id,
                    url=f"https://youtube.com/shorts/{video_id}",
                    status="uploaded_unconfirmed",
                    verified=False,
                    privacy_status=ret_privacy,
                    quota_spent=0,
                    verification_kind="studio_dom",
                    verification_note=(
                        "Uploaded through Studio browser automation. Video id and upload state "
                        "were read from the Studio DOM, not confirmed by videos.list. Verify "
                        "manually in YouTube Studio before treating this as published."
                    ),
                    raw_response={
                        "mode": "studio_playwright",
                        "channel": self.channel,
                        "profile_dir": str(self.edge_profile_dir),
                        "tags": effective_tags,
                        "altered_content": altered_content,
                        "related_video_url": related_video_url,
                        "publish_at": publish_at,
                        "is_scheduled": is_scheduled,
                    },
                )

            except Exception:
                try:
                    log_dir = get_data_dir() / "logs"
                    log_dir.mkdir(parents=True, exist_ok=True)
                    screenshot_path = log_dir / f"studio_error_{int(time.time())}.png"
                    if hasattr(page, "screenshot") and callable(page.screenshot):
                        page.screenshot(path=str(screenshot_path))
                        logger.info("Saved diagnostic failure screenshot to %s", screenshot_path)
                except Exception as s_err:
                    logger.debug("Could not save diagnostic screenshot: %s", s_err)
                raise

    def verify(self, video_id: str) -> bool:
        """Verify video status in YouTube Studio or via Shorts URL.

        Args:
            video_id: YouTube video ID.

        Returns:
            True if video is accessible/uploaded, False otherwise.
        """
        if not video_id:
            return False
        self.last_verification_kind = "unverified"
        try:
            with self._launch_browser_session(headless=True) as (_context, page):
                resp = page.goto(
                    f"https://www.youtube.com/shorts/{video_id}", wait_until="domcontentloaded"
                )
                if (
                    resp is not None
                    and hasattr(resp, "status")
                    and isinstance(resp.status, int)
                    and resp.status >= 400
                ):
                    return False
                raw_title = page.title() if hasattr(page, "title") and callable(page.title) else ""
                title = raw_title if isinstance(raw_title, str) else ""
                raw_content = (
                    page.content() if hasattr(page, "content") and callable(page.content) else ""
                )
                content = raw_content if isinstance(raw_content, str) else ""
                reachable = not ("Video unavailable" in title or "Video unavailable" in content)
                if reachable:
                    # Still DOM-derived: the Shorts page loaded, but this is not an API
                    # confirmation of processing status or visibility.
                    self.last_verification_kind = "studio_dom"
                return reachable
        except Exception as e:
            logger.warning("Studio video verification error for %s: %s", video_id, e)
            return False
