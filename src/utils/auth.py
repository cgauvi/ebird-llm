"""
auth.py — AWS Cognito authentication for the eBird Birding Assistant.

Provides sign-up, sign-in, and email-verification flows backed by a Cognito
User Pool.  All operations use the ``USER_PASSWORD_AUTH`` flow (no SRP) so
only ``boto3`` is required — no Cognito-specific SDK.

Environment variables
---------------------
COGNITO_USER_POOL_ID   – e.g. ``us-east-2_AbC123``
COGNITO_CLIENT_ID      – the App Client ID (public client, no secret)
AWS_REGION             – defaults to ``us-east-2``

Local development
-----------------
Set the above env-vars (or put them in ``.env``) and ensure you have valid
AWS credentials (``aws configure``).  The same code runs on ECS via the task
role.
"""

from __future__ import annotations

import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cognito client
# ---------------------------------------------------------------------------

_cognito_client = None


def _get_client():
    global _cognito_client
    if _cognito_client is None:
        _cognito_client = boto3.client(
            "cognito-idp",
            region_name=os.getenv("AWS_REGION", "us-east-2"),
        )
    return _cognito_client


def _client_id() -> str:
    return os.environ["COGNITO_CLIENT_ID"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sign_up(email: str, password: str) -> dict:
    """Register a new user with email as username.

    Returns ``{"success": True}`` or ``{"success": False, "error": "<msg>"}``.
    On success the user must confirm their email via ``confirm_sign_up``.
    """
    try:
        _get_client().sign_up(
            ClientId=_client_id(),
            Username=email,
            Password=password,
            UserAttributes=[{"Name": "email", "Value": email}],
        )
        return {"success": True}
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        msg = exc.response["Error"]["Message"]
        logger.warning("sign_up failed for %s: %s — %s", email, code, msg)
        return {"success": False, "error": msg}


def confirm_sign_up(email: str, code: str) -> dict:
    """Confirm email with the 6-digit verification code sent by Cognito."""
    try:
        _get_client().confirm_sign_up(
            ClientId=_client_id(),
            Username=email,
            ConfirmationCode=code,
        )
        return {"success": True}
    except ClientError as exc:
        msg = exc.response["Error"]["Message"]
        logger.warning("confirm_sign_up failed for %s: %s", email, msg)
        return {"success": False, "error": msg}


def sign_in(email: str, password: str) -> dict:
    """Authenticate and return tokens.

    Returns ``{"success": True, "email": …, "id_token": …, "access_token": …}``
    or ``{"success": False, "error": "<msg>"}``.
    """
    try:
        resp = _get_client().initiate_auth(
            ClientId=_client_id(),
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": email,
                "PASSWORD": password,
            },
        )
        auth_result = resp["AuthenticationResult"]
        return {
            "success": True,
            "email": email,
            "id_token": auth_result["IdToken"],
            "access_token": auth_result["AccessToken"],
        }
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        msg = exc.response["Error"]["Message"]
        logger.warning("sign_in failed for %s: %s — %s", email, code, msg)
        if code == "UserNotConfirmedException":
            return {"success": False, "error": "Email not verified. Please check your inbox."}
        return {"success": False, "error": msg}


def resend_confirmation_code(email: str) -> dict:
    """Re-send the email verification code."""
    try:
        _get_client().resend_confirmation_code(
            ClientId=_client_id(),
            Username=email,
        )
        return {"success": True}
    except ClientError as exc:
        msg = exc.response["Error"]["Message"]
        return {"success": False, "error": msg}


def is_configured() -> bool:
    """Return True if the required Cognito env-vars are set."""
    return bool(
        os.getenv("COGNITO_USER_POOL_ID") and os.getenv("COGNITO_CLIENT_ID")
    )
