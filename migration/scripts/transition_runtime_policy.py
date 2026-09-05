"""Apply or validate the exact local transition job policy.

This file is executed through ``odoo shell``.  It remains migration-only and
must never be added to the delivered Odoo add-ons path.
"""

# ruff: noqa: F821, T201 - Odoo shell supplies ``env``.

from __future__ import annotations

import json
import os


APPROVED_CRON_XMLIDS = (
    "usl_documents.ir_cron_usl_documents_sync",
    "usl_documents.ir_cron_usl_documents_poll",
    "usl_documents.ir_cron_usl_documents_attachment_queue",
    "usl_documents.ir_cron_usl_documents_classification",
    "usl_documents_accounting.ir_cron_archive_bank_statement_evidence",
    "usl_tese_payroll.ir_cron_tese_reconcile_documents",
    "usl_sign.ir_cron_sign_operations",
    "usl_sign.ir_cron_sign_daily_event_heads",
)


def activation_plan(all_cron_ids, approved_cron_ids):
    """Return the exact desired active state and reject missing jobs."""

    all_ids = {int(value) for value in all_cron_ids}
    approved_ids = {int(value) for value in approved_cron_ids}
    missing = approved_ids - all_ids
    if missing:
        raise RuntimeError(f"approved transition cron IDs are absent: {sorted(missing)}")
    return {cron_id: cron_id in approved_ids for cron_id in sorted(all_ids)}


def validate_active_ids(active_cron_ids, approved_cron_ids):
    active_ids = {int(value) for value in active_cron_ids}
    approved_ids = {int(value) for value in approved_cron_ids}
    if active_ids != approved_ids:
        raise RuntimeError(
            "transition cron policy differs: "
            f"active={sorted(active_ids)} approved={sorted(approved_ids)}",
        )


def cron_model(odoo_env):
    """Return the complete cron registry, including neutralized jobs."""

    return odoo_env["ir.cron"].sudo().with_context(active_test=False)


def apply_policy(odoo_env, *, mode):
    if os.environ.get("USL_MIGRATION_PURPOSE") != "transition":
        raise RuntimeError("transition job policy requires transition purpose")
    if os.environ.get("USL_EINVOICE_LIVE_ENABLED") != "0" or os.environ.get(
        "USL_EREPORTING_LIVE_ENABLED",
    ) != "0":
        raise RuntimeError("regulatory live flags must remain disabled")
    if mode not in {"apply", "validate"}:
        raise RuntimeError(f"unsupported transition policy mode: {mode}")

    Cron = cron_model(odoo_env)
    all_crons = Cron.search([])
    approved = Cron.browse()
    missing_xmlids = []
    for xmlid in APPROVED_CRON_XMLIDS:
        cron = odoo_env.ref(xmlid, raise_if_not_found=False)
        if not cron or cron._name != "ir.cron":
            missing_xmlids.append(xmlid)
        else:
            approved |= cron.sudo()
    if missing_xmlids:
        raise RuntimeError(
            "approved transition jobs are missing: " + ", ".join(missing_xmlids),
        )

    plan = activation_plan(all_crons.ids, approved.ids)
    if mode == "apply":
        disable = all_crons.filtered(lambda cron: cron.active and not plan[cron.id])
        enable = approved.filtered(lambda cron: not cron.active)
        if disable:
            disable.write({"active": False})
        if enable:
            enable.write({"active": True})

        companies = odoo_env["res.company"].sudo().search([])
        if "sign_opentimestamps_enabled" in companies._fields:
            companies.filtered("sign_opentimestamps_enabled").write(
                {"sign_opentimestamps_enabled": False},
            )
        odoo_env.cr.commit()

    active = Cron.search([("active", "=", True)])
    validate_active_ids(active.ids, approved.ids)

    parameters = odoo_env["ir.config_parameter"].sudo()
    if not parameters.get_bool("database.is_neutralized"):
        raise RuntimeError("transition database is missing the neutralization marker")

    mail_servers = odoo_env["ir.mail_server"].sudo().search([("active", "=", True)])
    unsafe_mail = mail_servers.filtered(lambda server: server.smtp_host != "invalid")
    if not mail_servers or unsafe_mail:
        raise RuntimeError("outgoing email is not confined to the invalid SMTP sink")

    if "fetchmail.server" in odoo_env.registry:
        incoming = odoo_env["fetchmail.server"].sudo().search([("active", "=", True)])
        if incoming:
            raise RuntimeError(f"incoming email servers remain active: {incoming.ids}")

    if "payment.provider" in odoo_env.registry:
        providers = odoo_env["payment.provider"].sudo().search(
            [("state", "!=", "disabled")],
        )
        if providers:
            raise RuntimeError(f"payment providers remain enabled: {providers.ids}")

    opentimestamps = odoo_env.ref(
        "usl_sign.ir_cron_sign_opentimestamps",
        raise_if_not_found=False,
    )
    if opentimestamps and opentimestamps.active:
        raise RuntimeError("OpenTimestamps remains scheduled")

    return {
        "active_cron_ids": sorted(active.ids),
        "approved_cron_xmlids": list(APPROVED_CRON_XMLIDS),
        "database_neutralized": True,
        "mode": mode,
        "outbound_email": "invalid-sink",
        "status": "passed",
    }


if "env" in globals():
    print(
        "USL_TRANSITION_RUNTIME_POLICY="
        + json.dumps(
            apply_policy(
                env,
                mode=os.environ.get("USL_TRANSITION_POLICY_MODE", "validate"),
            ),
            sort_keys=True,
        ),
    )
