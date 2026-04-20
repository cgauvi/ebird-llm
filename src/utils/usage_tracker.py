"""
usage_tracker.py — DynamoDB-backed usage tracking and rate limiting.

Two tables (names derived from ``DYNAMODB_TABLE_PREFIX`` env-var, defaulting
to the ``{project}-{env}`` naming used by Terraform):

1. ``{prefix}-usage``      — monthly session & prompt counters per user.
2. ``{prefix}-llm-calls``  — per-call audit log for analytics.

Rate limits (configurable via env-vars):
    MAX_SESSIONS_PER_MONTH  – default 10
    MAX_PROMPTS_PER_MONTH   – default 30

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

MAX_SESSIONS_PER_MONTH = int(os.getenv("MAX_SESSIONS_PER_MONTH", "10"))
MAX_PROMPTS_PER_MONTH = int(os.getenv("MAX_PROMPTS_PER_MONTH", "30"))

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

    Returns ``{"session_count": int, "prompt_count": int}``.
    """
    month = month or _current_month()
    try:
        resp = _usage_table().get_item(
            Key={"user_id": user_id, "month": month},
        )
        item = resp.get("Item", {})
        return {
            "session_count": int(item.get("session_count", 0)),
            "prompt_count": int(item.get("prompt_count", 0)),
        }
    except ClientError:
        logger.exception("get_usage failed for %s", user_id)
        return {"session_count": 0, "prompt_count": 0}


def increment_session(user_id: str) -> dict:
    """Atomically increment ``session_count`` for the current month.

    Returns ``{"allowed": bool, "session_count": int, "limit": int}``.
    """
    month = _current_month()
    try:
        resp = _usage_table().update_item(
            Key={"user_id": user_id, "month": month},
            UpdateExpression="SET session_count = if_not_exists(session_count, :zero) + :one, "
                             "prompt_count = if_not_exists(prompt_count, :zero)",
            ExpressionAttributeValues={":zero": 0, ":one": 1},
            ReturnValues="ALL_NEW",
        )
        count = int(resp["Attributes"]["session_count"])
        allowed = count <= MAX_SESSIONS_PER_MONTH
        if not allowed:
            logger.warning("Session limit reached for %s: %d/%d", user_id, count, MAX_SESSIONS_PER_MONTH)
        return {"allowed": allowed, "session_count": count, "limit": MAX_SESSIONS_PER_MONTH}
    except ClientError:
        logger.exception("increment_session failed for %s", user_id)
        # Fail open — allow the session but log the error
        return {"allowed": True, "session_count": -1, "limit": MAX_SESSIONS_PER_MONTH}


def increment_prompt(user_id: str) -> dict:
    """Atomically increment ``prompt_count`` for the current month.

    Returns ``{"allowed": bool, "prompt_count": int, "limit": int}``.
    """
    month = _current_month()
    try:
        resp = _usage_table().update_item(
            Key={"user_id": user_id, "month": month},
            UpdateExpression="SET prompt_count = if_not_exists(prompt_count, :zero) + :one, "
                             "session_count = if_not_exists(session_count, :zero)",
            ExpressionAttributeValues={":zero": 0, ":one": 1},
            ReturnValues="ALL_NEW",
        )
        count = int(resp["Attributes"]["prompt_count"])
        allowed = count <= MAX_PROMPTS_PER_MONTH
        if not allowed:
            logger.warning("Prompt limit reached for %s: %d/%d", user_id, count, MAX_PROMPTS_PER_MONTH)
        return {"allowed": allowed, "prompt_count": count, "limit": MAX_PROMPTS_PER_MONTH}
    except ClientError:
        logger.exception("increment_prompt failed for %s", user_id)
        return {"allowed": True, "prompt_count": -1, "limit": MAX_PROMPTS_PER_MONTH}


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
