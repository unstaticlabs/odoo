"""Apply environment-only Pocket ID provider and named-user policy."""

import json
import os

TRUTHY_VALUES = {"1", "true", "yes", "on"}
USERS_REQUIRED_ERROR = "USL_POCKET_ID_USERS_JSON is required."
USERS_JSON_ERROR = "USL_POCKET_ID_USERS_JSON is not valid JSON."


def _is_enabled(name):
    return os.getenv(name, "").strip().lower() in TRUTHY_VALUES


def configure(env):
    raw_configuration = os.getenv("USL_POCKET_ID_USERS_JSON", "").strip()
    break_glass_password = os.getenv(
        "USL_POCKET_ID_BREAK_GLASS_PASSWORD",
        "",
    )
    if not raw_configuration:
        raise RuntimeError(USERS_REQUIRED_ERROR)
    try:
        user_configuration = json.loads(raw_configuration)
    except json.JSONDecodeError as error:
        raise RuntimeError(USERS_JSON_ERROR) from error

    apply_changes = _is_enabled("USL_POCKET_ID_APPLY")
    try:
        env["auth.oauth.provider"]._usl_pocketid_apply_environment()
        summary = env["res.users"]._usl_pocketid_apply_user_configuration(
            user_configuration,
            break_glass_password=break_glass_password,
            strict=True,
        )
        if apply_changes:
            env.cr.commit()
        else:
            env.cr.rollback()
    except Exception:
        env.cr.rollback()
        raise
    summary["mode"] = "applied" if apply_changes else "dry_run"
    print(json.dumps(summary, indent=2, sort_keys=True))  # noqa: T201


configure(globals()["env"])
