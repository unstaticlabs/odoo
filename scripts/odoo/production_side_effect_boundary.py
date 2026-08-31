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
if live_einvoice != live_ereporting:
    errors.append("e-invoicing and e-reporting live guards disagree")
if mode == "staged" and (live_einvoice or live_ereporting):
    errors.append("regulatory live access is enabled during staging")

parameters = env["ir.config_parameter"].sudo()  # noqa: F821
neutralized = parameters.get_bool("database.is_neutralized")
if mode == "staged" and not neutralized:
    errors.append("Odoo staging neutralization marker is absent")

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
if "fetchmail.server" in env.registry:  # noqa: F821
    active_fetchmail = env["fetchmail.server"].sudo().search([("active", "=", True)])  # noqa: F821
    if active_fetchmail:
        errors.append(f"incoming mail servers remain active: {active_fetchmail.ids}")

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
    "regulatory_live": live_einvoice,
    "smtp_transport": "resend-host-config",
    "status": "passed",
}, indent=2, sort_keys=True))
