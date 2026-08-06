"""Fail closed unless the finalized database enforces Pocket ID SSO-only login."""

import json
import os

from odoo.tools import config

from odoo.addons.usl_pocketid.policy import (
    LOGIN_POLICY_SSO_ONLY,
    emergency_window_active,
    stored_login_policy,
)


def fail(message):
    raise RuntimeError(message)


def validate(env):
    for xmlid in (
        "usl_pocketid.sso_only_login",
        "usl_pocketid.portal_sso_invitation",
    ):
        if not env.ref(xmlid, raise_if_not_found=False):
            fail(
                "The installed Pocket ID module is incomplete; "
                f"missing {xmlid}. Upgrade usl_pocketid before release.",
            )
    if stored_login_policy(env) != LOGIN_POLICY_SSO_ONLY:
        fail("The database is not enforcing SSO-only login.")
    users, break_glass = env["res.users"]._usl_pocketid_validate_sso_only()
    if emergency_window_active() or os.getenv(
        "USL_POCKET_ID_BREAK_GLASS_ENABLED",
        "0",
    ).strip().lower() in {"1", "true", "yes", "on"}:
        fail("Emergency login must be sealed at release time.")
    if env["ir.config_parameter"].sudo().get_bool("auth_signup.reset_password"):
        fail("Password reset remains enabled.")
    if config["list_db"]:
        fail("The database manager remains available.")
    for guard in ("USL_EINVOICE_LIVE_ENABLED", "USL_EREPORTING_LIVE_ENABLED"):
        if os.getenv(guard, "0") != "0":
            fail(f"{guard} must remain disabled.")
    provider = env.ref("usl_pocketid.provider_pocketid")
    print(  # noqa: T201
        json.dumps(
            {
                "break_glass": break_glass.login,
                "governed_users": len(users) - 1,
                "login_policy": LOGIN_POLICY_SSO_ONLY,
                "provider": provider.name,
                "status": "pass",
            },
            sort_keys=True,
        ),
    )


validate(globals()["env"])
