"""Verify Paperless users, upsert Odoo mappings, and synchronize permissions."""

# Executed by ``odoo shell`` where ``env`` is injected by Odoo. Literal
# validation errors and the stdout summary are part of the operator contract.
# ruff: noqa: EM101, F821, I001, T201

import json
import os

from odoo import fields


raw_identities = os.environ.get("USL_PAPERLESS_IDENTITIES_JSON", "").strip()
if not raw_identities:
    raise RuntimeError("USL_PAPERLESS_IDENTITIES_JSON is required")
try:
    specifications = json.loads(raw_identities)
except json.JSONDecodeError as error:
    raise RuntimeError("The Paperless identity result is invalid JSON") from error
if not isinstance(specifications, list) or not specifications:
    raise RuntimeError("The Paperless identity result is empty")

provider = env.ref("usl_pocketid.provider_pocketid").sudo()
Mapping = env["usl.paperless.user.mapping"].sudo().with_context(
    usl_documents_mapping_no_sync=True,
)
client = env["usl.document"]._paperless()
force_permission_sync = os.environ.get(
    "USL_PAPERLESS_FORCE_PERMISSION_SYNC",
    "0",
).strip() == "1"
desired_user_ids = set()
managed_usernames = set()
mappings_changed = False
results = []
for specification in specifications:
    if not isinstance(specification, dict) or set(specification) != {
        "subject",
        "username",
        "paperless_user_id",
    }:
        raise RuntimeError("A Paperless identity result entry is incomplete")
    subject = str(specification["subject"]).strip()
    username = str(specification["username"]).strip()
    paperless_user_id = int(specification["paperless_user_id"])
    identities = env["usl.oidc.identity"].sudo().search(
        [
            ("provider_id", "=", provider.id),
            ("issuer", "=", provider.usl_oidc_issuer),
            ("subject", "=", subject),
            ("active", "=", True),
        ],
        limit=2,
    )
    if len(identities) != 1:
        raise RuntimeError(f"Pocket subject {subject!r} is ambiguous in Odoo")
    user = identities.user_id
    if not user.active or not user.usl_pocketid_access:
        raise RuntimeError(f"Odoo user {user.login!r} is not active in Pocket ID")
    remote = client.get_user(paperless_user_id)
    if remote.get("username") != username:
        raise RuntimeError(
            f"Paperless user {paperless_user_id} does not match {username!r}",
        )
    if remote.get("is_active") is not True:
        raise RuntimeError(
            f"Paperless user {paperless_user_id} ({username!r}) is inactive",
        )
    conflicting = env["usl.paperless.user.mapping"].sudo().search(
        [
            ("paperless_user_id", "=", paperless_user_id),
            ("user_id", "!=", user.id),
        ],
        limit=1,
    )
    if conflicting:
        raise RuntimeError(f"Paperless user {paperless_user_id} is already mapped")
    mapping = env["usl.paperless.user.mapping"].sudo().search(
        [("user_id", "=", user.id)],
        limit=1,
    )
    values = {
        "paperless_user_id": paperless_user_id,
        "paperless_username": username,
        "oidc_identity_id": identities.id,
        "qa_local_identity": False,
        "sync_state": "synchronized",
        "last_verified_at": fields.Datetime.now(),
        "last_error": False,
        "active": True,
    }
    if mapping:
        mappings_changed = mappings_changed or any(
            (
                mapping[field_name].id
                if mapping._fields[field_name].type == "many2one"
                else mapping[field_name]
            )
            != expected
            for field_name, expected in {
                "paperless_user_id": paperless_user_id,
                "paperless_username": username,
                "oidc_identity_id": identities.id,
                "qa_local_identity": False,
                "sync_state": "synchronized",
                "active": True,
            }.items()
        )
        mapping.with_context(usl_documents_mapping_no_sync=True).write(values)
    else:
        mapping = Mapping.create({"user_id": user.id, **values})
        mappings_changed = True
    if not mapping._identity_is_safe():
        raise RuntimeError(f"Odoo mapping for {user.login!r} is not identity-safe")
    desired_user_ids.add(user.id)
    managed_usernames.add(username)
    results.append(
        {
            "login": user.login,
            "paperless_user_id": paperless_user_id,
            "username": username,
        },
    )

stale = env["usl.paperless.user.mapping"].sudo().search(
    [("active", "=", True)],
).filtered(
    lambda mapping: (
        mapping.oidc_identity_id.provider_id == provider
        and mapping.paperless_username in managed_usernames
        and mapping.user_id.id not in desired_user_ids
    ),
)
if stale:
    mappings_changed = True
    stale.with_context(usl_documents_mapping_no_sync=True).write(
        {
            "active": False,
            "sync_state": "failed",
            "last_verified_at": False,
            "last_error": "Identity is no longer present in the governed Pocket policy.",
        },
    )

live_documents = env["usl.document"].sudo().search(
    [("availability_state", "in", ("available", "permission_error"))],
)
documents = (
    live_documents
    if force_permission_sync or mappings_changed
    else live_documents.filtered(
        lambda item: item.permission_sync_state != "synchronized",
    )
)
if documents:
    documents.with_user(env.ref("base.user_root")).action_sync_permissions()
failures = live_documents.filtered(
    lambda item: item.permission_sync_state != "synchronized",
)
if failures:
    raise RuntimeError(
        f"Paperless permission synchronization failed for {len(failures)} documents",
    )
env.cr.commit()
print(
    json.dumps(
        {
            "documents_synchronized": len(documents),
            "live_documents": len(live_documents),
            "identities": results,
            "stale_mappings_disabled": len(stale),
        },
        indent=2,
        sort_keys=True,
    ),
)  # noqa: T201
