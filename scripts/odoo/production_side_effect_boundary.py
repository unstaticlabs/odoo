"""Prove staged or admitted production side-effect controls."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: F821, T201

import json
import os

from odoo.tools import config


mode = os.environ.get("USL_PRODUCTION_SIDE_EFFECT_MODE", "staged")
if mode not in {"staged", "admitted"}:
    raise RuntimeError("Unsupported production side-effect boundary mode.")
errors = []
live_einvoice = os.environ.get("USL_EINVOICE_LIVE_ENABLED") == "1"
live_ereporting = os.environ.get("USL_EREPORTING_LIVE_ENABLED") == "1"
try:
    cron_gates = json.loads(os.environ["USL_PRODUCTION_CRON_GATES_JSON"])
except (KeyError, json.JSONDecodeError):
    cron_gates = {}
    errors.append("production cron gates are missing or invalid")
if live_ereporting and not live_einvoice:
    errors.append("e-reporting is enabled before invoice exchange")
if mode == "staged" and (live_einvoice or live_ereporting):
    errors.append("regulatory live access is enabled during staging")
if mode == "admitted" and (
    cron_gates.get("pdp_reception") is not live_einvoice
    or cron_gates.get("pdp_ereporting") is not live_ereporting
):
    errors.append("regulatory live flags and production cron gates disagree")

parameters = env["ir.config_parameter"].sudo()  # noqa: F821
neutralized = parameters.get_bool("database.is_neutralized")
if mode == "staged" and not neutralized:
    errors.append("Odoo staging neutralization marker is absent")
if mode == "admitted" and neutralized:
    errors.append("Odoo production remains neutralized")

mail_servers = env["ir.mail_server"].sudo().search([])  # noqa: F821
if mail_servers:
    errors.append(f"database outgoing mail servers remain: {mail_servers.ids}")
expected_smtp = {
    "smtp_server": "smtp.resend.com",
    "smtp_port": 587,
    "smtp_ssl": True,
    "smtp_user": "resend",
    "email_from": "odoo@unstaticlabs.com",
    "from_filter": "unstaticlabs.com",
}
if any(config.get(name) != expected for name, expected in expected_smtp.items()):
    errors.append("host-level Resend settings differ from the admitted configuration")
if len(config.get("smtp_password") or "") < 24:
    errors.append("host-level Resend credential is absent or too short")

pending_mail = env["mail.mail"].sudo().search_count([  # noqa: F821
    ("state", "in", ["outgoing", "exception"]),
])
if pending_mail:
    errors.append(f"pre-cutoff Odoo mail remains pending: {pending_mail}")
active_fetchmail = env["fetchmail.server"].sudo().search([("active", "=", True)])  # noqa: F821
if active_fetchmail and not (
    mode == "admitted" and cron_gates.get("inbound_mail") is True
):
    errors.append(f"incoming mail servers remain active: {active_fetchmail.ids}")
if mode == "admitted" and cron_gates.get("inbound_mail") is True:
    if len(active_fetchmail) != 1 or active_fetchmail.state != "done":
        errors.append("the admitted incoming mail server is not confirmed")

if mode == "staged" and "payment.provider" in env.registry:  # noqa: F821
    enabled_payment = env["payment.provider"].sudo().search([("state", "!=", "disabled")])  # noqa: F821
    if enabled_payment:
        errors.append(f"payment providers remain enabled during staging: {enabled_payment.ids}")

ServerAction = env["ir.actions.server"].sudo()  # noqa: F821
if mode == "staged" and "webhook_url" in ServerAction._fields:
    unsafe_webhooks = ServerAction.search([
        ("state", "=", "webhook"),
        ("webhook_url", "!=", "neutralization - disable webhook"),
    ])
    if unsafe_webhooks:
        errors.append(f"server-action webhooks remain enabled: {unsafe_webhooks.ids}")

if errors:
    raise RuntimeError("Production side-effect boundary failed: " + "; ".join(errors))

print(json.dumps({
    "database_mail_server_count": 0,
    "database_neutralized": neutralized,
    "mode": mode,
    "pending_mail": 0,
    "inbound_mail_live": bool(active_fetchmail),
    "regulatory_live": {
        "invoice_exchange": live_einvoice,
        "ereporting": live_ereporting,
    },
    "smtp_transport": "resend-host-config",
    "status": "passed",
}, indent=2, sort_keys=True))
