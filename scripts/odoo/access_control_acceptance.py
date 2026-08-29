# ruff: noqa: EM101, F821, I001, T201

"""Read-only named-user and capability evidence for Distribution access QA."""

import json

from odoo.exceptions import ValidationError


Users = env["res.users"].sudo().with_context(active_test=False)  # noqa: F821
expected = {
    "admin": {
        "role": "usl_access_control.group_distribution_administrator",
        "companies": "all",
        "pocketid": False,
        "irreversible": True,
    },
    "valentin": {
        "role": "usl_access_control.group_distribution_administrator",
        "companies": "all",
        "pocketid": True,
        "irreversible": True,
    },
    "roger@unstaticlabs.com": {
        "role": "usl_access_control.group_technical_administrator",
        "companies": "all",
        "pocketid": True,
        "irreversible": False,
    },
    "prosper": {
        "role": "usl_access_control.group_accounting_reviewer",
        "companies": {"Unstatic Labs", "USL MEDIA"},
        "pocketid": True,
        "irreversible": False,
    },
}
all_companies = set(env["res.company"].sudo().search([]).mapped("name"))  # noqa: F821
evidence = []
prosper_user = Users.browse()
for login, policy in expected.items():
    users = Users.search([("login", "=", login)])
    if len(users) != 1:
        raise ValidationError(f"Expected exactly one user for {login}; got {len(users)}.")
    user = users
    company_names = set(user.company_ids.mapped("name"))
    expected_companies = all_companies if policy["companies"] == "all" else policy["companies"]
    if company_names != expected_companies:
        raise ValidationError(
            f"Unexpected companies for {login}: {sorted(company_names)}; "
            f"expected {sorted(expected_companies)}.",
        )
    if not user.has_group(policy["role"]):
        raise ValidationError(f"Missing Distribution role for {login}: {policy['role']}.")
    if user.usl_pocketid_access != policy["pocketid"]:
        raise ValidationError(f"Unexpected Pocket ID state for {login}.")
    if user.usl_has_irreversible_actions != policy["irreversible"]:
        raise ValidationError(f"Unexpected irreversible capability for {login}.")
    multi_company_ui = user.has_group("base.group_multi_company")
    if len(user.company_ids) > 1 and not multi_company_ui:
        raise ValidationError(f"Missing native multi-company UI access for {login}.")
    identities = user.usl_oidc_identity_ids.filtered("active")
    if policy["pocketid"] and len(identities) != 1:
        raise ValidationError(f"Expected one active immutable identity for {login}.")
    evidence.append(
        {
            "login": login,
            "odoo_user_id": user.id,
            "active": user.active,
            "companies": sorted(company_names),
            "distribution_role": policy["role"],
            "pocketid_subject": identities.subject if identities else None,
            "ai_agent": user.usl_is_ai_agent,
            "irreversible_actions": user.usl_has_irreversible_actions,
            "multi_company_ui": multi_company_ui,
        },
    )
    if login == "prosper":
        prosper_user = user

prosper_context = {"allowed_company_ids": prosper_user.company_ids.ids}
prosper_declarations = env["rebuild.account.declaration"].with_user(  # noqa: F821
    prosper_user,
).with_context(**prosper_context)
prosper_decisions = env["rebuild.account.assurance.decision"].with_user(  # noqa: F821
    prosper_user,
).with_context(**prosper_context)
capabilities = {
    "declaration_read": prosper_declarations.has_access("read"),
    "declaration_write": prosper_declarations.has_access("write"),
    "decision_read": prosper_decisions.has_access("read"),
    "decision_create": prosper_decisions.has_access("create"),
    "decision_write": prosper_decisions.has_access("write"),
    "decision_delete": prosper_decisions.has_access("unlink"),
}
expected_capabilities = {
    "declaration_read": True,
    "declaration_write": False,
    "decision_read": True,
    "decision_create": True,
    "decision_write": True,
    "decision_delete": False,
}
if capabilities != expected_capabilities:
    raise ValidationError(
        f"Unexpected Prosper declaration capabilities: {capabilities}; "
        f"expected {expected_capabilities}."
    )

conflicts = Users.search([]).filtered(
    lambda user: user.usl_is_ai_agent and user.usl_has_irreversible_actions,
)
if conflicts:
    raise ValidationError(
        "AI Agent and Irreversible Actions conflict: "
        + ", ".join(conflicts.mapped("login")),
    )

historical = Users.search([("login", "=", "roger@xaic.cat")])
if historical and (historical.active or historical.usl_pocketid_access):
    raise ValidationError("The historical Roger identity is not inactive and sealed.")

print(json.dumps({  # noqa: T201
    "named_users": evidence,
    "prosper_declaration_capabilities": capabilities,
}, indent=2, sort_keys=True))
