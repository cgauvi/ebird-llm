"""
Tests for src.utils.auth (Cognito authentication) and
src.utils.usage_tracker (DynamoDB usage tracking / rate limiting).

All AWS calls are mocked — no real Cognito or DynamoDB resources needed.
"""

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestAuth:
    """Tests for src.utils.auth module."""

    @pytest.fixture(autouse=True)
    def _set_cognito_env(self, monkeypatch):
        monkeypatch.setenv("COGNITO_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-2_test")

    @patch("src.utils.auth._get_client")
    def test_sign_up_success(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.sign_up.return_value = {}

        from src.utils.auth import sign_up
        result = sign_up("alice@example.com", "P@ssw0rd!")
        assert result["success"] is True
        mock_client.sign_up.assert_called_once()

    @patch("src.utils.auth._get_client")
    def test_sign_up_duplicate_user(self, mock_get_client):
        from botocore.exceptions import ClientError
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.sign_up.side_effect = ClientError(
            {"Error": {"Code": "UsernameExistsException", "Message": "User already exists"}},
            "SignUp",
        )

        from src.utils.auth import sign_up
        result = sign_up("alice@example.com", "P@ssw0rd!")
        assert result["success"] is False
        assert "already exists" in result["error"]

    @patch("src.utils.auth._get_client")
    def test_sign_in_success(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.initiate_auth.return_value = {
            "AuthenticationResult": {
                "IdToken": "id-token-123",
                "AccessToken": "access-token-456",
            }
        }

        from src.utils.auth import sign_in
        result = sign_in("alice@example.com", "P@ssw0rd!")
        assert result["success"] is True
        assert result["email"] == "alice@example.com"
        assert result["id_token"] == "id-token-123"
        assert result["access_token"] == "access-token-456"

    @patch("src.utils.auth._get_client")
    def test_sign_in_unconfirmed_user(self, mock_get_client):
        from botocore.exceptions import ClientError
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.initiate_auth.side_effect = ClientError(
            {"Error": {"Code": "UserNotConfirmedException", "Message": "User not confirmed"}},
            "InitiateAuth",
        )

        from src.utils.auth import sign_in
        result = sign_in("alice@example.com", "P@ssw0rd!")
        assert result["success"] is False
        assert "not verified" in result["error"].lower()

    @patch("src.utils.auth._get_client")
    def test_confirm_sign_up_success(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.confirm_sign_up.return_value = {}

        from src.utils.auth import confirm_sign_up
        result = confirm_sign_up("alice@example.com", "123456")
        assert result["success"] is True

    def test_is_configured_false_by_default(self, monkeypatch):
        monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
        monkeypatch.delenv("COGNITO_CLIENT_ID", raising=False)

        from src.utils.auth import is_configured
        assert is_configured() is False

    def test_is_configured_true_when_set(self, monkeypatch):
        monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-2_test")
        monkeypatch.setenv("COGNITO_CLIENT_ID", "test-client-id")

        from src.utils.auth import is_configured
        assert is_configured() is True


# ---------------------------------------------------------------------------
# Usage tracker tests
# ---------------------------------------------------------------------------


class TestUsageTracker:
    """Tests for src.utils.usage_tracker module."""

    @patch("src.utils.usage_tracker._usage_table")
    def test_get_usage_returns_defaults_when_no_item(self, mock_table):
        mock_table.return_value.get_item.return_value = {}

        from src.utils.usage_tracker import get_usage
        result = get_usage("alice@example.com")
        assert result == {"session_count": 0, "prompt_count": 0}

    @patch("src.utils.usage_tracker._usage_table")
    def test_get_usage_returns_existing_counts(self, mock_table):
        mock_table.return_value.get_item.return_value = {
            "Item": {"session_count": 5, "prompt_count": 20}
        }

        from src.utils.usage_tracker import get_usage
        result = get_usage("alice@example.com")
        assert result == {"session_count": 5, "prompt_count": 20}

    @patch("src.utils.usage_tracker._usage_table")
    def test_increment_session_allowed(self, mock_table):
        mock_table.return_value.update_item.return_value = {
            "Attributes": {"session_count": 3, "prompt_count": 10}
        }

        from src.utils.usage_tracker import increment_session
        result = increment_session("alice@example.com")
        assert result["allowed"] is True
        assert result["session_count"] == 3

    @patch("src.utils.usage_tracker._usage_table")
    def test_increment_session_denied_at_limit(self, mock_table):
        mock_table.return_value.update_item.return_value = {
            "Attributes": {"session_count": 11, "prompt_count": 5}
        }

        from src.utils.usage_tracker import increment_session
        result = increment_session("alice@example.com")
        assert result["allowed"] is False
        assert result["session_count"] == 11

    @patch("src.utils.usage_tracker._usage_table")
    def test_increment_prompt_allowed(self, mock_table):
        mock_table.return_value.update_item.return_value = {
            "Attributes": {"prompt_count": 15, "session_count": 2}
        }

        from src.utils.usage_tracker import increment_prompt
        result = increment_prompt("alice@example.com")
        assert result["allowed"] is True
        assert result["prompt_count"] == 15

    @patch("src.utils.usage_tracker._usage_table")
    def test_increment_prompt_denied_at_limit(self, mock_table):
        mock_table.return_value.update_item.return_value = {
            "Attributes": {"prompt_count": 31, "session_count": 2}
        }

        from src.utils.usage_tracker import increment_prompt
        result = increment_prompt("alice@example.com")
        assert result["allowed"] is False

    @patch("src.utils.usage_tracker._calls_table")
    def test_log_llm_call_writes_item(self, mock_table):
        from src.utils.usage_tracker import log_llm_call
        log_llm_call(
            "alice@example.com",
            session_id="sess-1",
            model="qwen2.5-72b",
            prompt_chars=100,
            response_chars=500,
            latency_ms=2000,
            tool_calls=["get_recent_observations_by_region"],
        )
        mock_table.return_value.put_item.assert_called_once()
        item = mock_table.return_value.put_item.call_args[1]["Item"]
        assert item["user_id"] == "alice@example.com"
        assert item["model"] == "qwen2.5-72b"
        assert item["prompt_chars"] == 100
        assert item["tool_calls"] == ["get_recent_observations_by_region"]
