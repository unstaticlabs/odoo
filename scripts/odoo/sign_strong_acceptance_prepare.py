# ruff: noqa: F821, T201 -- Odoo shell injects env; stdout is wrapper input.
"""Prepare a disposable Pocket-backed Strong enrolment for browser acceptance."""

import json
import os

authenticator = os.environ.get("USL_SIGN_ACCEPTANCE_AUTHENTICATOR", "virtual")
if authenticator not in {"virtual", "real_platform"}:
    msg = "Strong acceptance authenticator must be virtual or real_platform"
    raise RuntimeError(msg)

partner = env["res.partner"].search(
    [("email", "=", "roger@unstaticlabs.com")],
    limit=1,
)
if not partner:
    msg = "The isolated Roger QA partner is missing"
    raise RuntimeError(msg)
partner = partner.commercial_partner_id
reviewer = env["res.users"].search([("login", "=", "valentin")], limit=1)
if not reviewer or not reviewer.has_group("usl_sign.group_sign_identity_reviewer"):
    msg = "The isolated Valentin identity reviewer is missing"
    raise RuntimeError(msg)

enrollments = env["usl.sign.enrollment"].with_user(reviewer)
current = enrollments.search(
    [
        ("partner_id", "=", partner.id),
        ("company_id", "=", env.company.id),
        ("state", "!=", "revoked"),
    ],
)
if current:
    current.with_user(reviewer).action_revoke(
        "Superseded by a new isolated Strong acceptance run",
    )

enrollment = enrollments.create(
    {
        "partner_id": partner.id,
        "company_id": env.company.id,
        "relationship_basis": "recurring_partner",
        "relationship_reference": "Isolated Pocket ID Strong acceptance",
        "policy_version": "2026.1",
        "review_note": (
            "Synthetic browser acceptance using "
            + (
                "a real platform authenticator"
                if authenticator == "real_platform"
                else "a virtual platform authenticator"
            )
            + "; not a production identity review."
        ),
    },
)
invitation = enrollment.action_copy_invitation()
env.cr.commit()
payload = {
    "enrollment_id": enrollment.id,
    "invitation_url": invitation["params"]["url"],
    "partner_id": partner.id,
    "authenticator": authenticator,
    "run_id": os.environ.get("USL_SIGN_ACCEPTANCE_RUN_ID", ""),
}
print("USL_SIGN_STRONG_ACCEPTANCE=" + json.dumps(payload, sort_keys=True))
