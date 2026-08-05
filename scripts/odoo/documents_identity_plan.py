"""Resolve a Pocket manifest to active Odoo Documents users."""

# Executed by ``odoo shell`` where ``env`` is injected by Odoo. Literal
# validation errors and the stdout marker are part of the operator contract.
# ruff: noqa: EM101, F821, I001, T201

import json
import os


raw_policy = os.environ.get("USL_PAPERLESS_IDENTITY_POLICY_JSON", "").strip()
if not raw_policy:
    raise RuntimeError("USL_PAPERLESS_IDENTITY_POLICY_JSON is required")
try:
    policy = json.loads(raw_policy)
except json.JSONDecodeError as error:
    raise RuntimeError("The Paperless identity policy is invalid JSON") from error
if not isinstance(policy, list) or not policy:
    raise RuntimeError("The Paperless identity policy is empty")

provider = env.ref("usl_pocketid.provider_pocketid").sudo()
if not provider.enabled or not provider.usl_oidc_issuer:
    raise RuntimeError("Pocket ID is not enabled in Odoo")

document_groups = (
    "usl_documents.group_documents_user",
    "usl_documents.group_documents_accountant",
    "usl_documents.group_documents_hr",
    "usl_documents.group_documents_manager",
)
plan = []
seen_users = set()
for specification in policy:
    if not isinstance(specification, dict) or set(specification) != {
        "subject",
        "username",
        "email",
        "display_name",
    }:
        raise RuntimeError("A Paperless identity policy entry is incomplete")
    subject = str(specification["subject"]).strip()
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
        raise RuntimeError(
            f"Pocket subject {subject!r} does not resolve to one Odoo identity",
        )
    user = identities.user_id
    if (
        not user.active
        or not user.usl_pocketid_access
        or user.id in seen_users
    ):
        raise RuntimeError(f"Odoo user {user.login!r} is not safely governed")
    if not any(user.has_group(xmlid) for xmlid in document_groups):
        raise RuntimeError(f"Odoo user {user.login!r} has no Documents role")
    seen_users.add(user.id)
    plan.append(
        {
            "subject": subject,
            "username": str(specification["username"]).strip(),
            "email": str(specification["email"]).strip(),
            "display_name": str(specification["display_name"]).strip(),
        },
    )

print(
    "USL_PAPERLESS_IDENTITY_PLAN="
    + json.dumps(plan, separators=(",", ":"), sort_keys=True),
)  # noqa: T201
