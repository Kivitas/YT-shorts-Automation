"""Gemini Spark companion integration."""

from autoshorts.integrations.spark.inbox_watcher import (
    SparkClipHint,
    SparkInboxWatcher,
    generate_spark_skill_prompt,
    parse_spark_import,
    parse_timestamp_to_seconds,
)

__all__ = [
    "SparkClipHint",
    "SparkInboxWatcher",
    "generate_spark_skill_prompt",
    "parse_spark_import",
    "parse_timestamp_to_seconds",
]
