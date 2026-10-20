"""Resilience, network probe, and sleep/offline recovery engine for AutoShorts."""

from __future__ import annotations

import http.client
import logging
import socket
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.error import HTTPError, URLError

from pydantic import BaseModel, Field

from autoshorts.core.state_manager import StateManager

logger = logging.getLogger(__name__)


class ConnectivityStatus(BaseModel):
    """Network, YouTube API, and SponsorBlock availability report."""

    internet_online: bool
    youtube_reachable: bool
    sponsorblock_reachable: bool
    latency_ms: float | None = None
    status_mode: Literal["online", "youtube_outage", "pc_offline"]
    message: str
    checked_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def _check_http_youtube(timeout: float = 2.5) -> int | None:
    """Fast HTTP HEAD probe to check if YouTube returns 5xx outage status codes."""
    try:
        conn = http.client.HTTPSConnection("www.youtube.com", timeout=timeout)
        conn.request("HEAD", "/generate_204")
        resp = conn.getresponse()
        status = int(resp.status)
        conn.close()
        return status
    except Exception:
        return None


def probe_connectivity(timeout: float = 2.5) -> ConnectivityStatus:
    """Performs fast socket/HEAD checks to distinguish internet, YouTube outage, or offline PC.

    Checks:
      1. DNS / Public Gateway reachability (Cloudflare 1.1.1.1:53, Google 8.8.8.8:53)
      2. YouTube reachability (www.youtube.com:443 + 5xx check)
      3. SponsorBlock reachability (sponsor.ajay.app:443)

    Returns:
        ConnectivityStatus with status_mode in ("online", "youtube_outage", "pc_offline").
    """
    start_time = time.perf_counter()
    checked_at = datetime.now(UTC).isoformat()

    # 1. Probe Internet reachability (DNS resolvers: Cloudflare 1.1.1.1, Google 8.8.8.8)
    internet_online = False
    for host, port in (("1.1.1.1", 53), ("8.8.8.8", 53)):
        try:
            s = socket.create_connection((host, port), timeout=timeout)
            s.close()
            internet_online = True
            break
        except Exception as e:
            logger.debug("DNS probe to %s:%s failed: %s", host, port, e)
            continue

    if not internet_online:
        elapsed = (time.perf_counter() - start_time) * 1000.0
        return ConnectivityStatus(
            internet_online=False,
            youtube_reachable=False,
            sponsorblock_reachable=False,
            latency_ms=round(elapsed, 2),
            status_mode="pc_offline",
            message="PC is offline. Cannot reach internet gateway or DNS (1.1.1.1 / 8.8.8.8).",
            checked_at=checked_at,
        )

    # 2. Probe YouTube reachability
    youtube_reachable = False
    try:
        s = socket.create_connection(("www.youtube.com", 443), timeout=timeout)
        s.close()
        youtube_reachable = True
    except Exception:
        youtube_reachable = False

    # Optional fast HEAD check for HTTP 5xx outage if socket connection succeeded
    if youtube_reachable:
        http_code = _check_http_youtube(timeout)
        if http_code is not None and http_code in (500, 502, 503, 504):
            youtube_reachable = False

    # 3. Probe SponsorBlock reachability
    sponsorblock_reachable = False
    try:
        s = socket.create_connection(("sponsor.ajay.app", 443), timeout=timeout)
        s.close()
        sponsorblock_reachable = True
    except Exception:
        sponsorblock_reachable = False

    elapsed = (time.perf_counter() - start_time) * 1000.0
    status_mode: Literal["online", "youtube_outage", "pc_offline"] = (
        "online" if youtube_reachable else "youtube_outage"
    )
    if status_mode == "online":
        message = f"All network services online (latency: {elapsed:.1f}ms)."
    else:
        message = "PC has internet access, but YouTube is unreachable or experiencing a 5xx outage."

    return ConnectivityStatus(
        internet_online=True,
        youtube_reachable=youtube_reachable,
        sponsorblock_reachable=sponsorblock_reachable,
        latency_ms=round(elapsed, 2),
        status_mode=status_mode,
        message=message,
        checked_at=checked_at,
    )


def classify_network_or_outage_error(exc: BaseException | str) -> dict[str, Any]:
    """Inspects an exception or error string to classify network or service outages.

    Returns:
        dict with keys:
            - is_transient_outage: bool
            - outage_type: "pc_offline" | "youtube_outage" | "rate_limited" | "none"
            - recommended_backoff_sec: int
            - user_message: str
    """
    msg = str(exc).lower()
    exc_type = type(exc).__name__ if isinstance(exc, BaseException) else ""

    status_code: int | None = None
    if isinstance(exc, HTTPError):
        status_code = getattr(exc, "code", None)
    elif hasattr(exc, "status_code"):
        try:
            status_code = int(exc.status_code)
        except Exception:
            pass
    elif hasattr(exc, "code"):
        try:
            status_code = int(exc.code)
        except Exception:
            pass
    elif hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        try:
            status_code = int(exc.response.status_code)
        except Exception:
            pass

    # 1. Rate limiting (429 or quota limit)
    if (
        status_code == 429
        or "429" in msg
        or "too many requests" in msg
        or "rate limit" in msg
        or "quotaexceeded" in msg
        or "user rate limit exceeded" in msg
    ):
        return {
            "is_transient_outage": True,
            "outage_type": "rate_limited",
            "recommended_backoff_sec": 300,
            "user_message": "YouTube rate limit or quota throttle detected (HTTP 429). Backing off before retrying.",
        }

    # 2. YouTube service outage / 5xx server errors
    if (
        (status_code in (500, 502, 503, 504))
        or "503" in msg
        or "502" in msg
        or "504" in msg
        or "service unavailable" in msg
        or "bad gateway" in msg
        or "gateway timeout" in msg
        or ("500" in msg and "server" in msg)
        or "youtube_outage" in msg
        or "youtube outage" in msg
    ):
        return {
            "is_transient_outage": True,
            "outage_type": "youtube_outage",
            "recommended_backoff_sec": 600,
            "user_message": "YouTube service outage or 5xx server error detected. Upload will be deferred until service recovers.",
        }

    # 3. DNS resolution failure (PC offline)
    if (
        isinstance(exc, socket.gaierror)
        or "gaierror" in exc_type
        or "getaddrinfo failed" in msg
        or "name or service not known" in msg
        or "nameresolutionerror" in msg
        or "dns resolution failed" in msg
        or "failed to resolve" in msg
    ):
        return {
            "is_transient_outage": True,
            "outage_type": "pc_offline",
            "recommended_backoff_sec": 120,
            "user_message": "DNS resolution failed. Local PC is disconnected from the internet.",
        }

    # 4. Connection dropped, reset, timeout, or unreachable (PC offline / network drop)
    is_conn_error = isinstance(
        exc,
        (
            ConnectionResetError,
            ConnectionRefusedError,
            ConnectionAbortedError,
            ConnectionError,
            TimeoutError,
            socket.timeout,
            URLError,
        ),
    )
    conn_type_match = exc_type in (
        "ConnectError",
        "ConnectTimeout",
        "NetworkError",
        "ReadTimeout",
        "WriteTimeout",
        "ProtocolError",
        "RemoteDisconnected",
    )
    conn_msg_match = any(
        phrase in msg
        for phrase in (
            "connection reset",
            "connection refused",
            "connection aborted",
            "network is unreachable",
            "host is unreachable",
            "connection timed out",
            "timed out",
            "remote end closed connection",
            "network unreachable",
            "winerror 10054",
            "winerror 10060",
            "winerror 10061",
            "winerror 10065",
            "pc_offline",
        )
    )

    if is_conn_error or conn_type_match or conn_msg_match:
        return {
            "is_transient_outage": True,
            "outage_type": "pc_offline",
            "recommended_backoff_sec": 120,
            "user_message": "Network connection dropped or reset. Local internet connection interrupted.",
        }

    # 5. Non-transient error
    return {
        "is_transient_outage": False,
        "outage_type": "none",
        "recommended_backoff_sec": 0,
        "user_message": f"Non-network error: {exc}",
    }


class OfflineRecoveryManager:
    """Manages system sleep/hibernation detection and overdue slot rescheduling."""

    @staticmethod
    def detect_clock_jump_or_sleep(
        last_tick_utc: datetime,
        now_utc: datetime | None = None,
        threshold_sec: float = 120.0,
    ) -> dict[str, Any]:
        """Detects when the PC was asleep, hibernated, or powered off for > threshold_sec.

        Args:
            last_tick_utc: UTC timestamp of previous scheduler iteration.
            now_utc: Optional current UTC time override.
            threshold_sec: Inactivity gap in seconds denoting sleep/hibernation (default 120s).

        Returns:
            dict containing jump_detected (bool), elapsed_sec (float), and descriptive message.
        """
        now = now_utc or datetime.now(UTC)
        if last_tick_utc.tzinfo is None and now.tzinfo is not None:
            last_tick_utc = last_tick_utc.replace(tzinfo=UTC)
        elif last_tick_utc.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        elapsed = (now - last_tick_utc).total_seconds()
        jump_detected = elapsed > threshold_sec or elapsed < -5.0

        if elapsed > threshold_sec:
            message = (
                f"Clock jump detected: elapsed time ({elapsed:.1f}s) exceeded threshold ({threshold_sec:.1f}s). "
                "System was likely asleep, hibernated, or suspended."
            )
        elif elapsed < -5.0:
            message = (
                f"Clock jump backwards detected: time shifted backwards by {abs(elapsed):.1f}s."
            )
        else:
            message = f"Clock normal ({elapsed:.1f}s elapsed)."

        return {
            "jump_detected": jump_detected,
            "elapsed_sec": elapsed,
            "threshold_sec": threshold_sec,
            "last_tick_utc": last_tick_utc.isoformat(),
            "now_utc": now.isoformat(),
            "message": message,
        }

    @staticmethod
    def recover_overdue_scheduled_slots(
        state_manager: StateManager,
        min_gap_minutes: int = 12,
        now_utc: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Finds all SCHEDULED slots in the past and cleanly re-spaces them without bursting.

        Rescheduling starts at now_utc + 2 minutes, with min_gap_minutes spacing between
        each recovered slot so that waking a PC after hours never bursts uploads simultaneously.

        Args:
            state_manager: StateManager instance with SQLite scheduler queue.
            min_gap_minutes: Minimum minutes between recovered slots (default 12).
            now_utc: Optional current UTC time override.

        Returns:
            List of rescheduled slot dictionaries.
        """
        now = now_utc or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        pending = state_manager.get_pending_scheduled_slots()
        overdue_slots: list[dict[str, Any]] = []

        for slot in pending:
            st_str = slot.get("scheduled_time")
            if not st_str:
                continue
            st = datetime.fromisoformat(st_str)
            if st.tzinfo is None:
                st = st.replace(tzinfo=UTC)

            if st < now:
                overdue_slots.append(slot)

        if not overdue_slots:
            return []

        # Sort overdue slots chronologically by original scheduled_time
        overdue_slots.sort(key=lambda s: s["scheduled_time"])

        recovered: list[dict[str, Any]] = []
        for i, slot in enumerate(overdue_slots):
            # First slot at now + 2 minutes; subsequent slots spaced by min_gap_minutes
            stagger_minutes = 2 + (i * min_gap_minutes)
            new_sched_time = now + timedelta(minutes=stagger_minutes)

            tags_list = slot["tags"].split(",") if slot.get("tags") else []
            state_manager.enqueue_for_upload(
                job_id=slot["job_id"],
                video_path=slot["video_path"],
                title=slot["title"],
                description=slot["description"],
                scheduled_time=new_sched_time,
                slot_jitter_minutes=slot.get("slot_jitter_minutes", 0),
                privacy_status=slot.get("privacy_status", "unlisted"),
                tags=tags_list,
            )

            updated_slot = dict(slot)
            updated_slot["scheduled_time"] = new_sched_time.isoformat()
            updated_slot["rescheduled_time"] = new_sched_time.isoformat()
            updated_slot["rescheduled_from_overdue"] = True
            recovered.append(updated_slot)

            logger.info(
                f"Rescheduled overdue slot for job {slot['job_id']} to {new_sched_time.isoformat()} "
                f"(stagger: +{stagger_minutes}m from wake)."
            )

        return recovered
