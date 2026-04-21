# ---------------------------------------------------------------------------
# AWS Cognito User Pool — authentication for Streamlit users
# ---------------------------------------------------------------------------

resource "aws_cognito_user_pool" "main" {
  name = "${local.prefix}-users"

  # Sign-in with email address
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  # Email verification
  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
    email_subject        = "eBird Assistant — verify your email"
    email_message        = "Your verification code is {####}"
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true

    string_attribute_constraints {
      min_length = 1
      max_length = 256
    }
  }
}

# Public app client — no secret, uses USER_PASSWORD_AUTH flow
resource "aws_cognito_user_pool_client" "streamlit" {
  name         = "${local.prefix}-streamlit"
  user_pool_id = aws_cognito_user_pool.main.id

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  # No client secret (public client for Streamlit)
  generate_secret = false
}

# ---------------------------------------------------------------------------
# DynamoDB — usage tracking & LLM call audit log
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "usage" {
  name         = "${local.prefix}-usage"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "month"

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "month"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

resource "aws_dynamodb_table" "llm_calls" {
  name         = "${local.prefix}-llm-calls"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "timestamp"

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "S"
  }

  # GSI for querying all calls in a given month (analytics)
  attribute {
    name = "month"
    type = "S"
  }

  global_secondary_index {
    name            = "month-index"
    hash_key        = "month"
    range_key       = "timestamp"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

# ---------------------------------------------------------------------------
# DynamoDB — per-session log archive
#
# Every log entry written to the in-memory LogBuffer during a user turn is
# flushed here before the Streamlit rerun.  Entries are queryable by
# session_id (hash) + log_id (range) and, via a GSI, by user_id + log_id
# so you can retrieve the full history for a given user across sessions.
#
# TTL: 90 days (configurable by changing the ttl attribute on each item).
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "session_logs" {
  name         = "${local.prefix}-session-logs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"
  range_key    = "log_id"

  attribute {
    name = "session_id"
    type = "S"
  }

  attribute {
    name = "log_id"
    type = "S"
  }

  attribute {
    name = "user_id"
    type = "S"
  }

  # GSI: look up all log entries across sessions for a single user
  global_secondary_index {
    name            = "user-log-index"
    hash_key        = "user_id"
    range_key       = "log_id"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

# ---------------------------------------------------------------------------
# IAM — grant the ECS task role access to Cognito + DynamoDB
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "app_permissions" {
  # Cognito — sign-up / sign-in flows
  statement {
    sid    = "CognitoAuth"
    effect = "Allow"
    actions = [
      "cognito-idp:SignUp",
      "cognito-idp:ConfirmSignUp",
      "cognito-idp:ResendConfirmationCode",
      "cognito-idp:InitiateAuth",
    ]
    resources = [aws_cognito_user_pool.main.arn]
  }

  # DynamoDB — read/write usage, llm-calls, and session-logs tables
  statement {
    sid    = "DynamoDB"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:BatchWriteItem",
    ]
    resources = [
      aws_dynamodb_table.usage.arn,
      aws_dynamodb_table.llm_calls.arn,
      "${aws_dynamodb_table.llm_calls.arn}/index/*",
      aws_dynamodb_table.session_logs.arn,
      "${aws_dynamodb_table.session_logs.arn}/index/*",
    ]
  }
}

resource "aws_iam_role_policy" "app_permissions" {
  name   = "${local.prefix}-app-permissions"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.app_permissions.json
}
