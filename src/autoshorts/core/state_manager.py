"""Thread-safe SQLite State Manager with WAL mode and dual-bucket quota tracking.

Persists pipeline state, jobs, execution stages/artifacts, the scheduler queue,
and YouTube Data API dual-bucket quota usage keyed by Pacific Time date (PLAN.md section 8.1 & 8.2).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import zoneinfo

from autoshorts.core.exceptions import StateError
from autoshorts.core.models import Job, JobStatus, VideoMetadata
from autoshorts.core.paths import get_data_dir


def get_pt_timezone() -> timezone:
    """Return the America/Los_Angeles timezone, with safe fallback on systems without tzdata."""
    try:
        return zoneinfo.ZoneInfo("America/Los_Angeles")  # type: ignore[return-value]
    except Exception:
        # Fallback to standard US Pacific Time (UTC-7 PDT / UTC-8 PST heuristic)
        # 2nd Sun in Mar to 1st Sun in Nov is PDT (UTC-7), else PST (UTC-8)
        now_utc = datetime.now(timezone.utc)
        month = now_utc.month
        offset_hours = -7 if (3 < month < 11) else -8
        return timezone(timedelta(hours=offset_hours))


def get_current_pt_date(dt: datetime | None = None) -> str:
    """Return the date formatted as YYYY-MM-DD in Pacific Time (quota reset timezone)."""
    pt_tz = get_pt_timezone()
    if dt is None:
        target = datetime.now(pt_tz)
    else:
        if dt.tzinfo is None:
            # Assume UTC if naive
            target = dt.replace(tzinfo=timezone.utc).astimezone(pt_tz)
        else:
            target = dt.astimezone(pt_tz)
    return target.strftime("%Y-%m-%d")


class StateManager:
    """Thread-safe SQLite-backed state and quota manager."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        """Initialize the state manager, ensuring directory and idempotent tables exist."""
        if db_path is None:
            self.db_path = get_data_dir() / "state.db"
        else:
            self.db_path = Path(db_path).resolve()

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create a connection with WAL mode and foreign keys enabled."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode; transactions managed explicitly
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self) -> None:
        """Run idempotent migrations to create schema tables and indexes."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("BEGIN IMMEDIATE;")

                # 1. Jobs table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY,
                        source_url TEXT NOT NULL,
                        status TEXT NOT NULL,
                        custom_instructions TEXT,
                        payload_json TEXT NOT NULL,
                        error_message TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);")

                # 2. Stages and artifacts table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS stages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL,
                        stage_name TEXT NOT NULL,
                        status TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        completed_at TEXT,
                        artifacts_json TEXT,
                        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
                    );
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_stages_job_id ON stages(job_id);")

                # 3. Scheduler queue table (per PLAN.md section 8.2)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS scheduler_queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT UNIQUE NOT NULL,
                        video_path TEXT NOT NULL,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        tags TEXT,
                        privacy_status TEXT NOT NULL,
                        scheduled_time TEXT NOT NULL,
                        slot_jitter_minutes INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'SCHEDULED',
                        attempts INTEGER NOT NULL DEFAULT 0,
                        quota_spent INTEGER NOT NULL DEFAULT 0,
                        youtube_video_id TEXT,
                        created_at TEXT NOT NULL,
                        uploaded_at TEXT,
                        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
                    );
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_scheduler_status_time ON scheduler_queue(status, scheduled_time);"
                )

                # 4. Quota usage table: dual buckets ('upload' and 'general') keyed by Pacific Time date
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS quota_usage (
                        date_pt TEXT NOT NULL,
                        bucket TEXT NOT NULL,
                        units_spent INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (date_pt, bucket)
                    );
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_quota_date_bucket ON quota_usage(date_pt, bucket);"
                )

                # 5. Metadata / Key-Value table (scheduler pause flag, runtime state)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS kv_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )

                cursor.execute("COMMIT;")
            except Exception as e:
                conn.execute("ROLLBACK;")
                raise StateError(f"Database initialization failed: {e}") from e
            finally:
                conn.close()

    # -------------------------------------------------------------------------
    # Jobs Management
    # -------------------------------------------------------------------------

    def save_job(self, job: Job) -> None:
        """Insert or update a Job record atomically."""
        with self._lock:
            conn = self._get_connection()
            try:
                now_str = datetime.now(timezone.utc).isoformat()
                payload = job.model_dump_json()
                conn.execute(
                    """
                    INSERT INTO jobs (id, source_url, status, custom_instructions, payload_json, error_message, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_url = excluded.source_url,
                        status = excluded.status,
                        custom_instructions = excluded.custom_instructions,
                        payload_json = excluded.payload_json,
                        error_message = excluded.error_message,
                        updated_at = excluded.updated_at;
                    """,
                    (
                        job.id,
                        job.source_url,
                        job.status.value,
                        job.custom_instructions,
                        payload,
                        job.error_message,
                        job.created_at.isoformat(),
                        now_str,
                    ),
                )
            except Exception as e:
                raise StateError(f"Failed to save job {job.id}: {e}") from e
            finally:
                conn.close()

    def get_job(self, job_id: str) -> Job | None:
        """Retrieve a Job by ID, or None if not found."""
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute(
                    "SELECT payload_json FROM jobs WHERE id = ?;", (job_id,)
                ).fetchone()
                if not row:
                    return None
                return Job.model_validate_json(row["payload_json"])
            except Exception as e:
                raise StateError(f"Failed to load job {job_id}: {e}") from e
            finally:
                conn.close()

    def list_jobs(self, status: JobStatus | None = None, limit: int = 100) -> list[Job]:
        """List recent jobs, optionally filtered by status."""
        with self._lock:
            conn = self._get_connection()
            try:
                if status:
                    rows = conn.execute(
                        "SELECT payload_json FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?;",
                        (status.value, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT payload_json FROM jobs ORDER BY created_at DESC LIMIT ?;",
                        (limit,),
                    ).fetchall()
                return [Job.model_validate_json(r["payload_json"]) for r in rows]
            except Exception as e:
                raise StateError(f"Failed to list jobs: {e}") from e
            finally:
                conn.close()

    def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        error_message: str | None = None,
    ) -> None:
        """Update job status and error message in both SQLite column and JSON payload."""
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                raise StateError(f"Job {job_id} not found for status update")
            job.transition_to(status)
            if error_message is not None:
                job.error_message = error_message
            self.save_job(job)

    # -------------------------------------------------------------------------
    # Stage & Artifact Tracking
    # -------------------------------------------------------------------------

    def record_stage_start(self, job_id: str, stage_name: str) -> int:
        """Record the start of a pipeline processing stage."""
        with self._lock:
            conn = self._get_connection()
            try:
                now_str = datetime.now(timezone.utc).isoformat()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO stages (job_id, stage_name, status, started_at)
                    VALUES (?, ?, 'RUNNING', ?);
                    """,
                    (job_id, stage_name, now_str),
                )
                stage_id = cursor.lastrowid
                return stage_id or 0
            except Exception as e:
                raise StateError(f"Failed to record stage start for job {job_id}: {e}") from e
            finally:
                conn.close()

    def record_stage_complete(
        self,
        stage_id: int,
        status: str = "COMPLETED",
        artifacts: dict[str, Any] | None = None,
    ) -> None:
        """Record stage completion along with serialized artifact references."""
        with self._lock:
            conn = self._get_connection()
            try:
                now_str = datetime.now(timezone.utc).isoformat()
                artifacts_json = json.dumps(artifacts) if artifacts else None
                conn.execute(
                    """
                    UPDATE stages
                    SET status = ?, completed_at = ?, artifacts_json = ?
                    WHERE id = ?;
                    """,
                    (status, now_str, artifacts_json, stage_id),
                )
            except Exception as e:
                raise StateError(f"Failed to record stage completion for stage {stage_id}: {e}") from e
            finally:
                conn.close()

    def get_job_stages(self, job_id: str) -> list[dict[str, Any]]:
        """Retrieve all recorded stages and artifacts for a job."""
        with self._lock:
            conn = self._get_connection()
            try:
                rows = conn.execute(
                    "SELECT * FROM stages WHERE job_id = ? ORDER BY id ASC;",
                    (job_id,),
                ).fetchall()
                results = []
                for r in rows:
                    item = dict(r)
                    if item.get("artifacts_json"):
                        item["artifacts"] = json.loads(item["artifacts_json"])
                    results.append(item)
                return results
            finally:
                conn.close()

    # -------------------------------------------------------------------------
    # Scheduler Queue Management (Pacing & Persistence)
    # -------------------------------------------------------------------------

    def enqueue_for_upload(
        self,
        job_id: str,
        video_path: str,
        title: str,
        description: str,
        scheduled_time: datetime,
        slot_jitter_minutes: int = 0,
        privacy_status: str = "unlisted",
        tags: list[str] | None = None,
    ) -> int:
        """Enqueue a pre-rendered master Short into the persistent scheduler queue."""
        with self._lock:
            conn = self._get_connection()
            try:
                now_str = datetime.now(timezone.utc).isoformat()
                sched_str = (
                    scheduled_time.astimezone(timezone.utc).isoformat()
                    if scheduled_time.tzinfo
                    else scheduled_time.isoformat()
                )
                tags_str = ",".join(tags) if tags else ""
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO scheduler_queue (
                        job_id, video_path, title, description, tags, privacy_status,
                        scheduled_time, slot_jitter_minutes, status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'SCHEDULED', ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        video_path = excluded.video_path,
                        title = excluded.title,
                        description = excluded.description,
                        tags = excluded.tags,
                        privacy_status = excluded.privacy_status,
                        scheduled_time = excluded.scheduled_time,
                        slot_jitter_minutes = excluded.slot_jitter_minutes,
                        status = 'SCHEDULED';
                    """,
                    (
                        job_id,
                        video_path,
                        title,
                        description,
                        tags_str,
                        privacy_status,
                        sched_str,
                        slot_jitter_minutes,
                        now_str,
                    ),
                )
                return cursor.lastrowid or 0
            except Exception as e:
                raise StateError(f"Failed to enqueue job {job_id} for upload: {e}") from e
            finally:
                conn.close()

    def get_pending_scheduled_slots(self) -> list[dict[str, Any]]:
        """Retrieve queued upload slots sorted chronologically."""
        with self._lock:
            conn = self._get_connection()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM scheduler_queue
                    WHERE status = 'SCHEDULED'
                    ORDER BY scheduled_time ASC;
                    """
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def update_scheduled_slot_status(
        self,
        job_id: str,
        status: str,
        youtube_video_id: str | None = None,
        quota_spent: int = 0,
    ) -> None:
        """Update scheduler item status upon dispatch, success, or skip."""
        with self._lock:
            conn = self._get_connection()
            try:
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    UPDATE scheduler_queue
                    SET status = ?,
                        youtube_video_id = COALESCE(?, youtube_video_id),
                        quota_spent = quota_spent + ?,
                        uploaded_at = CASE WHEN ? = 'COMPLETED' THEN ? ELSE uploaded_at END
                    WHERE job_id = ?;
                    """,
                    (status, youtube_video_id, quota_spent, status, now_str, job_id),
                )
            except Exception as e:
                raise StateError(f"Failed to update scheduler slot for job {job_id}: {e}") from e
            finally:
                conn.close()

    def count_uploads_since(self, since: datetime) -> int:
        """Count completed uploads since a timestamp (used for rolling 60-min rate limit)."""
        with self._lock:
            conn = self._get_connection()
            try:
                since_str = (
                    since.astimezone(timezone.utc).isoformat()
                    if since.tzinfo
                    else since.isoformat()
                )
                row = conn.execute(
                    """
                    SELECT COUNT(*) as count FROM scheduler_queue
                    WHERE status = 'COMPLETED' AND uploaded_at >= ?;
                    """,
                    (since_str,),
                ).fetchone()
                return int(row["count"]) if row else 0
            finally:
                conn.close()

    def count_uploads_for_pt_date(self, date_pt: str | None = None) -> int:
        """Count completed uploads for a specific Pacific Time date (used for 50/day cap)."""
        if date_pt is None:
            date_pt = get_current_pt_date()

        # Date range for date_pt in Pacific Time converted to UTC bounds
        pt_tz = get_pt_timezone()
        year, month, day = map(int, date_pt.split("-"))
        pt_start = datetime(year, month, day, 0, 0, 0, tzinfo=pt_tz)
        pt_end = pt_start + timedelta(days=1)

        utc_start = pt_start.astimezone(timezone.utc).isoformat()
        utc_end = pt_end.astimezone(timezone.utc).isoformat()

        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute(
                    """
                    SELECT COUNT(*) as count FROM scheduler_queue
                    WHERE status = 'COMPLETED' AND uploaded_at >= ? AND uploaded_at < ?;
                    """,
                    (utc_start, utc_end),
                ).fetchone()
                return int(row["count"]) if row else 0
            finally:
                conn.close()

    def is_scheduler_paused(self) -> bool:
        """Check if upload scheduler has been paused via dashboard."""
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute(
                    "SELECT value FROM kv_meta WHERE key = 'scheduler_paused';"
                ).fetchone()
                if row:
                    return row["value"] == "1"
                return False
            finally:
                conn.close()

    def set_scheduler_paused(self, paused: bool) -> None:
        """Pause or resume upload scheduler."""
        with self._lock:
            conn = self._get_connection()
            try:
                now_str = datetime.now(timezone.utc).isoformat()
                val = "1" if paused else "0"
                conn.execute(
                    """
                    INSERT INTO kv_meta (key, value, updated_at)
                    VALUES ('scheduler_paused', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
                    """,
                    (val, now_str),
                )
            finally:
                conn.close()

    # -------------------------------------------------------------------------
    # Dual-Bucket Quota Tracking (Upload Bucket vs. General Bucket)
    # -------------------------------------------------------------------------

    def record_quota_spend(
        self,
        bucket: str,
        units: int = 1,
        when: datetime | None = None,
    ) -> int:
        """Atomically record quota usage for a bucket keyed by Pacific Time date.

        Args:
            bucket: 'upload' (videos.insert, 100/day limit) or 'general' (videos.list, 10000/day pool).
            units: Cost in units/calls (typically 1).
            when: Optional timestamp of expenditure (defaults to now).

        Returns:
            The new cumulative units spent for this bucket on that Pacific Time date.
        """
        date_pt = get_current_pt_date(when)
        now_str = datetime.now(timezone.utc).isoformat()

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                conn.execute(
                    """
                    INSERT INTO quota_usage (date_pt, bucket, units_spent, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(date_pt, bucket) DO UPDATE SET
                        units_spent = quota_usage.units_spent + excluded.units_spent,
                        updated_at = excluded.updated_at;
                    """,
                    (date_pt, bucket, units, now_str),
                )
                row = conn.execute(
                    "SELECT units_spent FROM quota_usage WHERE date_pt = ? AND bucket = ?;",
                    (date_pt, bucket),
                ).fetchone()
                conn.execute("COMMIT;")
                return int(row["units_spent"]) if row else units
            except Exception as e:
                conn.execute("ROLLBACK;")
                raise StateError(f"Failed to record quota spend for bucket '{bucket}': {e}") from e
            finally:
                conn.close()

    def get_quota_spend(self, bucket: str, date_pt: str | None = None) -> int:
        """Return total units spent for a bucket on a Pacific Time date (defaults to today)."""
        if date_pt is None:
            date_pt = get_current_pt_date()

        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute(
                    "SELECT units_spent FROM quota_usage WHERE date_pt = ? AND bucket = ?;",
                    (date_pt, bucket),
                ).fetchone()
                return int(row["units_spent"]) if row else 0
            finally:
                conn.close()

    def reset_quota_for_date(self, date_pt: str | None = None) -> None:
        """Reset quota counters for a given Pacific Time date."""
        if date_pt is None:
            date_pt = get_current_pt_date()

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM quota_usage WHERE date_pt = ?;", (date_pt,))
            finally:
                conn.close()
