"""Apply and prove the versioned production cron and outbound policy."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: EM101, F821, T201

import json
import os

from odoo.tools import config


def load_object(name):
    try:
        value = json.loads(os.environ[name])
    except (KeyError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{name} is missing or invalid.") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} must be a JSON object.")
    return value


policy = load_object("USL_PRODUCTION_CRON_POLICY_JSON")
gates = load_object("USL_PRODUCTION_CRON_GATES_JSON")
if policy.get("schema") != "usl-production-cron-policy-v1":
    raise RuntimeError("The production cron policy schema is unsupported.")
policy_gates = policy.get("gates")
rules = policy.get("crons")
if (
    not isinstance(policy_gates, list)
    or not isinstance(rules, dict)
    or set(gates) != set(policy_gates)
    or any(type(value) is not bool for value in gates.values())
    or gates.get("always") is not True
):
    raise RuntimeError("The production cron gates are incomplete or unsafe.")
for xmlid, rule in rules.items():
    if (
        not isinstance(xmlid, str)
        or "." not in xmlid
        or not isinstance(rule, dict)
        or set(rule) != {"gate", "reason"}
        or rule["gate"] not in {*policy_gates, None}
        or not isinstance(rule["reason"], str)
        or not rule["reason"].strip()
    ):
        raise RuntimeError(f"Invalid production cron policy entry: {xmlid!r}")

Cron = env["ir.cron"].sudo()  # noqa: F821
crons = Cron.search([])
xmlid_rows = env["ir.model.data"].sudo().search([  # noqa: F821
    ("model", "=", "ir.cron"),
    ("res_id", "in", crons.ids),
])
xmlids_by_id = {}
for row in xmlid_rows:
    xmlids_by_id.setdefault(row.res_id, []).append(f"{row.module}.{row.name}")
ambiguous = {record_id: values for record_id, values in xmlids_by_id.items() if len(values) != 1}
missing_ids = sorted(set(crons.ids) - set(xmlids_by_id))
if ambiguous or missing_ids:
    raise RuntimeError(
        "Every installed cron must have exactly one XML ID: "
        + json.dumps({"ambiguous": ambiguous, "missing_ids": missing_ids}, sort_keys=True)
    )
installed = {values[0]: crons.browse(record_id) for record_id, values in xmlids_by_id.items()}
unknown = sorted(set(installed) - set(rules))
missing = sorted(set(rules) - set(installed))
if unknown or missing:
    raise RuntimeError(
        "The installed cron inventory differs from the versioned production policy: "
        + json.dumps({"unknown": unknown, "missing": missing}, sort_keys=True)
    )

desired = {
    xmlid
    for xmlid, rule in rules.items()
    if rule["gate"] is not None and gates[rule["gate"]]
}
currently_active = {xmlid for xmlid, cron in installed.items() if cron.active}
to_enable = sorted(desired - currently_active)
to_disable = sorted(currently_active - desired)

expected_smtp = {
    "smtp_server": "smtp.resend.com",
    "smtp_port": 587,
    "smtp_ssl": True,
    "smtp_user": "resend",
    "email_from": "odoo@unstaticlabs.com",
    "from_filter": "unstaticlabs.com",
}
if gates["smtp"]:
    bad_smtp = {
        name: config.get(name)
        for name, expected in expected_smtp.items()
        if config.get(name) != expected
    }
    if bad_smtp or len(config.get("smtp_password") or "") < 24:
        raise RuntimeError("The host-level Resend transport is not configured safely.")

live_pdp = (
    os.environ.get("USL_EINVOICE_LIVE_ENABLED") == "1"
    and os.environ.get("USL_EREPORTING_LIVE_ENABLED") == "1"
)
if gates["pdp"] != live_pdp:
    raise RuntimeError("The PDP cron gate and regulatory live flags do not agree.")

pending_mail = env["mail.mail"].sudo().search_count([  # noqa: F821
    ("state", "in", ["outgoing", "exception"]),
])
if pending_mail:
    raise RuntimeError(f"Pre-cutoff Odoo mail remains pending: {pending_mail}")
mail_servers = env["ir.mail_server"].sudo().search([])  # noqa: F821
if "fetchmail.server" in env.registry:  # noqa: F821
    active_fetchmail = env["fetchmail.server"].sudo().search_count([("active", "=", True)])  # noqa: F821
    if active_fetchmail:
        raise RuntimeError(f"Incoming mail servers remain active: {active_fetchmail}")

apply = os.environ.get("USL_PRODUCTION_ADMISSION_APPLY") == "1"
if apply:
    if mail_servers:
        mail_servers.unlink()
    if to_disable:
        Cron.browse([installed[xmlid].id for xmlid in to_disable]).write({"active": False})
    if to_enable:
        Cron.browse([installed[xmlid].id for xmlid in to_enable]).write({"active": True})
    env.cr.commit()  # noqa: F821
else:
    env.cr.rollback()  # noqa: F821

assert_converged = os.environ.get("USL_PRODUCTION_ADMISSION_ASSERT_CONVERGED") == "1"
if assert_converged and (to_disable or to_enable or mail_servers):
    raise RuntimeError(
        "The applied production policy is not converged: "
        + json.dumps(
            {
                "database_mail_server_ids": mail_servers.ids,
                "disable": to_disable,
                "enable": to_enable,
            },
            sort_keys=True,
        )
    )

print(json.dumps({
    "database_mail_server_count": len(mail_servers),
    "desired_active_xmlids": sorted(desired),
    "disabled_xmlids": sorted(set(rules) - desired),
    "gates": gates,
    "mode": "applied" if apply else "dry_run",
    "pending_mail": pending_mail,
}, indent=2, sort_keys=True))
