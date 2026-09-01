"""Apply and prove the versioned production cron and outbound policy."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: EM101, F821, T201

import json
import os
from ast import literal_eval

from odoo.tools import config


def expected_regulatory_gates(environment):
    einvoice_live = environment.get("USL_EINVOICE_LIVE_ENABLED") == "1"
    ereporting_live = environment.get("USL_EREPORTING_LIVE_ENABLED") == "1"
    if ereporting_live and not einvoice_live:
        raise RuntimeError("E-reporting cannot be enabled before invoice exchange.")
    return {
        "pdp_reception": einvoice_live,
        "pdp_ereporting": ereporting_live,
    }


def expected_mail_alias_domain():
    return {
        "name": "unstaticlabs.com",
        "bounce_alias": "bounce",
        "catchall_alias": "catchall",
        "default_from": "odoo",
    }


def expected_inbound_server():
    return {
        "server_type": "imap",
        "state": "done",
        "server": "imap.gmail.com",
        "port": 993,
        "is_ssl": True,
        "object_id": False,
    }


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

Cron = env["ir.cron"].sudo().with_context(active_test=False)  # noqa: F821
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

alias_domains = env["mail.alias.domain"].sudo().search([])  # noqa: F821
if len(alias_domains) != 1:
    raise RuntimeError(
        "Production requires exactly one mail alias domain; "
        f"found {len(alias_domains)}."
    )
alias_domain = alias_domains.ensure_one()
expected_alias_domain = expected_mail_alias_domain()
alias_domain_mismatch = {
    field_name: {
        "configured": alias_domain[field_name],
        "expected": expected_value,
    }
    for field_name, expected_value in expected_alias_domain.items()
    if alias_domain[field_name] != expected_value
}

regulatory_gates = expected_regulatory_gates(os.environ)
gate_mismatch = {
    name: {"expected": expected, "configured": gates.get(name)}
    for name, expected in regulatory_gates.items()
    if gates.get(name) != expected
}
if gate_mismatch:
    raise RuntimeError(
        "The PDP cron gates and regulatory live flags do not agree: "
        + json.dumps(gate_mismatch, sort_keys=True)
    )

pending_mail = env["mail.mail"].sudo().search_count([  # noqa: F821
    ("state", "in", ["outgoing", "exception"]),
])
if pending_mail:
    raise RuntimeError(f"Pre-cutoff Odoo mail remains pending: {pending_mail}")
mail_servers = env["ir.mail_server"].sudo().search([])  # noqa: F821
active_fetchmail = env["fetchmail.server"].sudo().search([("active", "=", True)])  # noqa: F821
inbound_mail = {"active_server_count": len(active_fetchmail), "status": "disabled"}
if gates["inbound_mail"]:
    if len(active_fetchmail) != 1:
        raise RuntimeError(
            "Admitted inbound mail requires exactly one active server; "
            f"found {len(active_fetchmail)}."
        )
    incoming = active_fetchmail.ensure_one()
    expected_incoming = expected_inbound_server()
    incoming_mismatch = {}
    for field_name, expected_value in expected_incoming.items():
        configured = incoming[field_name]
        if field_name == "object_id":
            configured = configured.id or False
        if configured != expected_value:
            incoming_mismatch[field_name] = {
                "configured": configured,
                "expected": expected_value,
            }
    if incoming_mismatch or not incoming.user or not incoming.password:
        raise RuntimeError(
            "The admitted Gmail IMAP server is incomplete or unsafe: "
            + json.dumps(incoming_mismatch, sort_keys=True)
        )
    if incoming.error_message:
        raise RuntimeError("The admitted Gmail IMAP server has an unresolved error.")

    required_aliases = {
        "expense@unstaticlabs.com": "hr.expense",
        "purchases@unstaticlabs.com": "account.move",
        "purchases-usl-media@unstaticlabs.com": "account.move",
    }
    aliases = env["mail.alias"].sudo().search([  # noqa: F821
        ("alias_full_name", "in", list(required_aliases)),
    ])
    aliases_by_email = {alias.alias_full_name: alias for alias in aliases}
    alias_errors = {}
    for address, model in required_aliases.items():
        alias = aliases_by_email.get(address)
        if not alias:
            alias_errors[address] = "missing"
        elif alias.alias_model_id.model != model:
            alias_errors[address] = f"routes to {alias.alias_model_id.model}"
    expense_alias = aliases_by_email.get("expense@unstaticlabs.com")
    if expense_alias and expense_alias.alias_contact not in {"employees", "partners"}:
        alias_errors[expense_alias.alias_full_name] = "accepts unauthenticated senders"

    journals = env["account.journal"].sudo().search([("alias_id", "!=", False)])  # noqa: F821
    for journal in journals:
        defaults = literal_eval(journal.alias_id.alias_defaults or "{}")
        expected_defaults = {
            "company_id": journal.company_id.id,
            "journal_id": journal.id,
            "move_type": "in_invoice" if journal.type == "purchase" else "out_invoice",
        }
        if (
            journal.type not in {"purchase", "sale"}
            or journal.alias_id.alias_model_id.model != "account.move"
            or journal.alias_id.alias_domain_id != alias_domain
            or defaults != expected_defaults
        ):
            alias_errors[journal.alias_id.alias_full_name] = "journal routing is inconsistent"
    if alias_errors:
        raise RuntimeError(
            "Production inbound aliases are incomplete or unsafe: "
            + json.dumps(alias_errors, sort_keys=True)
        )
    inbound_mail = {
        "active_server_count": 1,
        "required_aliases": sorted(required_aliases),
        "status": "admitted",
    }
elif active_fetchmail:
    raise RuntimeError(
        f"Incoming mail is gated off but active servers remain: {active_fetchmail.ids}"
    )

apply = os.environ.get("USL_PRODUCTION_ADMISSION_APPLY") == "1"
if apply:
    if alias_domain_mismatch:
        alias_domain.write(expected_alias_domain)
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
if assert_converged and (
    alias_domain_mismatch or to_disable or to_enable or mail_servers
):
    raise RuntimeError(
        "The applied production policy is not converged: "
        + json.dumps(
            {
                "mail_alias_domain": alias_domain_mismatch,
                "database_mail_server_ids": mail_servers.ids,
                "disable": to_disable,
                "enable": to_enable,
            },
            sort_keys=True,
        )
    )

print(json.dumps({
    "mail_alias_domain": expected_alias_domain,
    "mail_alias_domain_update": alias_domain_mismatch,
    "database_mail_server_count": len(mail_servers),
    "desired_active_xmlids": sorted(desired),
    "disabled_xmlids": sorted(set(rules) - desired),
    "gates": gates,
    "inbound_mail": inbound_mail,
    "mode": "applied" if apply else "dry_run",
    "pending_mail": pending_mail,
}, indent=2, sort_keys=True))
