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
    defer_paperless_sync = _is_enabled("USL_POCKET_ID_DEFER_PAPERLESS_SYNC")
    try:
        env["auth.oauth.provider"]._usl_pocketid_apply_environment()
        public_base_url = (
            os.getenv("USL_POCKET_ID_ODOO_BASE_URL", "").strip().rstrip("/")
        )
        if public_base_url:
            parameters = env["ir.config_parameter"].sudo()
            parameters.set_str("web.base.url", public_base_url)
            parameters.set_str("web.base.url.freeze", "True")
        users = env["res.users"].with_context(
            usl_documents_defer_user_access_sync=True,
        )
        if not apply_changes or defer_paperless_sync:
            # The dry run is rolled back, but Paperless permission updates are
            # external side effects and cannot be rolled back with PostgreSQL.
            # Keep those hooks disabled while still exercising all Odoo-side
            # validation and access-policy writes in the transaction.
            users = users.with_context(usl_documents_user_access_no_sync=True)
        summary = users._usl_pocketid_apply_user_configuration(
            user_configuration,
            break_glass_password=break_glass_password,
            strict=True,
        )
        summary["login_policy"] = users._usl_pocketid_apply_login_policy()
        if apply_changes and not defer_paperless_sync and "usl.document" in env:
            env["usl.document"].sudo().search(
                [("access_scope", "=", "linked_record")],
            )._recompute_linked_record_access(sync_permissions=True)
        if apply_changes:
            env.cr.commit()
        else:
            env.cr.rollback()
    except Exception:
        env.cr.rollback()
        raise
    summary["mode"] = "applied" if apply_changes else "dry_run"
    summary["paperless_sync"] = (
        "deferred" if defer_paperless_sync else "synchronized"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))  # noqa: T201


configure(globals()["env"])
