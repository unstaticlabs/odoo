"""Shared login-policy helpers for the Pocket ID authentication boundary."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

LOGIN_POLICY_PARAMETER = "usl_pocketid.login_policy"
LOGIN_POLICY_STANDARD = "standard"
LOGIN_POLICY_SSO_ONLY = "sso_only"
SUPPORTED_LOGIN_POLICIES = {LOGIN_POLICY_STANDARD, LOGIN_POLICY_SSO_ONLY}

REAUTH_SESSION_KEY = "usl_pocketid_reauth"
EMERGENCY_SESSION_KEY = "usl_pocketid_emergency"
ID_TOKEN_SESSION_KEY = "usl_pocketid_id_token"
END_SESSION_URL_SESSION_KEY = "usl_pocketid_end_session_url"

_TRUTHY_VALUES = {"1", "true", "yes", "on"}
_PROCESS_STARTED_AT = datetime.now(UTC)
_MAX_EMERGENCY_WINDOW = timedelta(hours=1)


def desired_login_policy() -> str:
    return os.getenv(
        "USL_POCKET_ID_LOGIN_POLICY",
        LOGIN_POLICY_STANDARD,
    ).strip().lower()


def stored_login_policy(env) -> str:
    return (
        env["ir.config_parameter"]
        .sudo()
        .get_str(LOGIN_POLICY_PARAMETER, LOGIN_POLICY_STANDARD)
    )


def is_sso_only(env) -> bool:
    return stored_login_policy(env) == LOGIN_POLICY_SSO_ONLY


def emergency_window_active() -> bool:
    if (
        os.getenv("USL_POCKET_ID_BREAK_GLASS_ENABLED", "")
        .strip()
        .lower()
        not in _TRUTHY_VALUES
    ):
        return False
    raw_expiry = os.getenv("USL_POCKET_ID_BREAK_GLASS_EXPIRES_AT", "").strip()
    if not raw_expiry:
        return False
    try:
        expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
    except ValueError:
        return False
    if expiry.tzinfo is None:
        return False
    expiry = expiry.astimezone(UTC)
    now = datetime.now(UTC)
    return now < expiry <= _PROCESS_STARTED_AT + _MAX_EMERGENCY_WINDOW
