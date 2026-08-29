"""Prove the admitted database still has fail-closed outbound controls."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: F821, T201

import json
import os

errors = []
if os.environ.get("USL_EINVOICE_LIVE_ENABLED", "") != "0":
    errors.append("electronic invoicing live access is enabled")
if os.environ.get("USL_EREPORTING_LIVE_ENABLED", "") != "0":
    errors.append("e-reporting live access is enabled")

parameters = env["ir.config_parameter"].sudo()  # noqa: F821
if not parameters.get_bool("database.is_neutralized"):
    errors.append("Odoo standard neutralization marker is absent")

active_mail_servers = env["ir.mail_server"].sudo().search([("active", "=", True)])  # noqa: F821
unsafe_mail_servers = active_mail_servers.filtered(
    lambda server: server.smtp_host != "invalid",
)
if not active_mail_servers or unsafe_mail_servers:
    errors.append(
        "outgoing mail is not confined to the neutralization server: "
        f"{unsafe_mail_servers.ids}",
    )

if "fetchmail.server" in env.registry:  # noqa: F821
    active_fetchmail = env["fetchmail.server"].sudo().search([("active", "=", True)])  # noqa: F821
    if active_fetchmail:
        errors.append(f"incoming mail servers remain active: {active_fetchmail.ids}")

if "payment.provider" in env.registry:  # noqa: F821
    enabled_payment = env["payment.provider"].sudo().search([("state", "!=", "disabled")])  # noqa: F821
    if enabled_payment:
        errors.append(f"payment providers remain enabled: {enabled_payment.ids}")

ServerAction = env["ir.actions.server"].sudo()  # noqa: F821
if "webhook_url" in ServerAction._fields:
    unsafe_webhooks = ServerAction.search([
        ("state", "=", "webhook"),
        ("webhook_url", "!=", "neutralization - disable webhook"),
    ])
    if unsafe_webhooks:
        errors.append(f"server-action webhooks remain enabled: {unsafe_webhooks.ids}")

if errors:
    raise RuntimeError("Production side-effect boundary failed: " + "; ".join(errors))

print(json.dumps({
    "active_neutralized_mail_server_ids": active_mail_servers.ids,
    "database_neutralized": True,
    "outbound_integrations": "disabled",
    "regulatory_live_guards": "disabled",
    "status": "passed",
}, indent=2, sort_keys=True))
