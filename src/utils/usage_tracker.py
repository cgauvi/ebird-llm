"""
usage_tracker.py — DynamoDB-backed usage tracking and rate limiting.

Two tables (names derived from ``DYNAMODB_TABLE_PREFIX`` env-var, defaulting
to the ``{project}-{env}`` naming used by Terraform):

1. ``{prefix}-usage``      — monthly LLM-call counter per user.
2. ``{prefix}-llm-calls``  — per-call audit log for analytics.

Rate limit (configurable via env-var):
    MAX_LLM_CALLS_PER_MONTH – default 40

All writes use conditional expressions / atomic increments so concurrent
requests from the same user are safe.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_LLM_CALLS_PER_MONTH = int(os.getenv("MAX_LLM_CALLS_PER_MONTH", "40"))

_TABLE_PREFIX = os.getenv("DYNAMODB_TABLE_PREFIX", "ebird-llm-dev")

# ---------------------------------------------------------------------------
# DynamoDB resource (lazy singleton)
# ---------------------------------------------------------------------------

_dynamodb = None


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource(
            "dynamodb",
            region_name=os.getenv("AWS_REGION", "us-east-2"),
        )
    return _dynamodb


def _usage_table():
    return _get_dynamodb().Table(f"{_TABLE_PREFIX}-usage")


def _calls_table():
    return _get_dynamodb().Table(f"{_TABLE_PREFIX}-llm-calls")


def _current_month() -> str:
    """Return the current month as 'YYYY-MM' in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Usage counters — rate limiting
# ---------------------------------------------------------------------------


def get_usage(user_id: str, month: str | None = None) -> dict:
    """Return current month's usage for *user_id*.

    Returns ``{"llm_call_count": int}``.
    """
    month = month or _current_month()
    try:
        resp = _usage_table().get_item(
            Key={"user_id": user_id, "month": month},
        )
        item = resp.get("Item", {})
        return {"llm_call_count": int(item.get("llm_call_count", 0))}
    except ClientError:
        logger.exception("get_usage failed for %s", user_id)
        return {"llm_call_count": 0}


def increment_llm_call(user_id: str) -> dict:
    """Atomically increment ``llm_call_count`` for the current month and
    return whether the user is still within the monthly cap.

    No-op in non-prod environments (``APP_ENV != "prod"``) — returns
    ``allowed=True`` without touching DynamoDB so local development is
    not rate-limited.

    Returns ``{"allowed": bool, "llm_call_count": int, "limit": int}``.
    """
    if os.getenv("APP_ENV", "dev").lower() != "prod":
        return {"allowed": True, "llm_call_count": 0, "limit": MAX_LLM_CALLS_PER_MONTH}

    month = _current_month()
    try:
        resp = _usage_table().update_item(
            Key={"user_id": user_id, "month": month},
            UpdateExpression="SET llm_call_count = if_not_exists(llm_call_count, :zero) + :one",
            ExpressionAttributeValues={":zero": 0, ":one": 1},
            ReturnValues="ALL_NEW",
        )
        count = int(resp["Attributes"]["llm_call_count"])
        allowed = count <= MAX_LLM_CALLS_PER_MONTH
        if not allowed:
            logger.warning(
                "LLM call limit reached for %s: %d/%d",
                user_id, count, MAX_LLM_CALLS_PER_MONTH,
            )
        return {"allowed": allowed, "llm_call_count": count, "limit": MAX_LLM_CALLS_PER_MONTH}
    except ClientError:
        logger.exception("increment_llm_call failed for %s", user_id)
        # Fail open — don't block users on infra outages
        return {"allowed": True, "llm_call_count": -1, "limit": MAX_LLM_CALLS_PER_MONTH}


# ---------------------------------------------------------------------------
# LLM call audit log
# ---------------------------------------------------------------------------


def log_llm_call(
    user_id: str,
    *,
    session_id: str,
    model: str,
    prompt_chars: int,
    response_chars: int,
    latency_ms: int,
    tool_calls: list[str] | None = None,
) -> None:
    """Write one row to the llm-calls audit table."""
    now = datetime.now(timezone.utc)
    try:
        _calls_table().put_item(
            Item={
                "user_id": user_id,
                "timestamp": now.isoformat(),
                "session_id": session_id,
                "model": model,
                "prompt_chars": prompt_chars,
                "response_chars": response_chars,
                "latency_ms": latency_ms,
                "tool_calls": tool_calls or [],
                "month": now.strftime("%Y-%m"),
            }
        )
    except ClientError:
        logger.exception("log_llm_call failed for %s", user_id)


def is_configured() -> bool:
    """Return True if we can reach DynamoDB (tables may not exist yet)."""
    return bool(_TABLE_PREFIX)


def _session_logs_table():
    return _get_dynamodb().Table(f"{_TABLE_PREFIX}-session-logs")


# ---------------------------------------------------------------------------
# Session log archival
# ---------------------------------------------------------------------------

# 90 days in seconds
_SESSION_LOG_TTL_SECS = 90 * 86_400


def flush_session_logs(
    user_id: str,
    session_id: str,
    entries: list[dict],
) -> None:
    """Batch-write *entries* from the in-memory LogBuffer to DynamoDB.

    Each entry becomes one row keyed by ``session_id`` (hash) and a sortable
    ``log_id`` of the form ``"<epoch_ms_at_flush>#<zero_padded_index>"`` so
    that successive flushes within the same session remain ordered and unique.

    Call this right before ``st.rerun()`` so the log survives the Streamlit
    worker restart.  The function is a no-op if *entries* is empty or if the
    DynamoDB table is unreachable (errors are logged, never raised).
    """
    if not entries or not session_id:
        return

    now = datetime.now(timezone.utc)
    ttl = int(now.timestamp()) + _SESSION_LOG_TTL_SECS
    # Millisecond epoch as the time-ordered prefix for log_id
    base_ms = int(now.timestamp() * 1000)

    try:
        table = _session_logs_table()
        with table.batch_writer() as batch:
            for i, entry in enumerate(entries):
                batch.put_item(Item={
                    "session_id": session_id,
                    # Sortable, unique range key: epoch_ms#sequence
                    "log_id": f"{base_ms:016d}#{i:06d}",
                    "user_id": user_id or "anonymous",
                    "ts": entry.get("ts", ""),
                    "level": entry.get("level", "INFO"),
                    "logger": entry.get("logger", ""),
                    "message": entry.get("message", ""),
                    "ttl": ttl,
                })
    except ClientError:
        logger.exception("flush_session_logs failed for session %s", session_id)
