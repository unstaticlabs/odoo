"""Prepare a disposable, network-free French e-invoicing QA database."""

import os

from odoo import Command

TRUTHY_VALUES = {"1", "true", "yes", "on"}
QA_COMPANY_NAME = "French E-Invoice QA"
QA_SIRET = "98398295000021"
MISSING_OPT_IN = "Set USL_EINVOICE_QA_BOOTSTRAP=1 explicitly."
LIVE_GUARD_ENABLED = "QA bootstrap refuses to run with a live guard enabled."
EXISTING_IDENTITY = (
    "QA bootstrap refuses to replace an existing company identity."
)
MISSING_CHART = "Install the French chart before QA bootstrap."
EXISTING_ACCOUNTING = (
    "QA bootstrap refuses to replace accounting on a company with posted entries."
)
NON_DEMO_CONNECTION = "QA bootstrap found a non-demo platform connection."


def _is_enabled(name):
    return os.getenv(name, "").strip().lower() in TRUTHY_VALUES


def _user(env, *, login, name, password, groups, company):
    user = env["res.users"].sudo().search([("login", "=", login)], limit=1)
    values = {
        "name": name,
        "login": login,
        "password": password,
        "email": f"{login}@example.invalid",
        "company_id": company.id,
        "company_ids": [Command.set(company.ids)],
        "group_ids": [Command.set(groups.ids)],
        "active": True,
    }
    if user:
        user.write(values)
    else:
        user = env["res.users"].with_context(
            no_reset_password=True,
        ).sudo().create(values)
    return user


def bootstrap(env):
    if not _is_enabled("USL_EINVOICE_QA_BOOTSTRAP"):
        raise RuntimeError(MISSING_OPT_IN)
    if (
        _is_enabled("USL_EINVOICE_LIVE_ENABLED")
        or _is_enabled("USL_EREPORTING_LIVE_ENABLED")
    ):
        raise RuntimeError(LIVE_GUARD_ENABLED)

    company = env.company.sudo()
    if (
        (company.vat or company.company_registry)
        and (
            company.name != QA_COMPANY_NAME
            or company.company_registry != QA_SIRET
        )
    ):
        raise RuntimeError(EXISTING_IDENTITY)

    france = env.ref("base.fr")
    eur = env.ref("base.EUR")
    eur.active = True
    company.write({
        "country_id": france.id,
        "account_fiscal_country_id": france.id,
        "currency_id": eur.id,
    })
    if company.chart_template != "fr_comp":
        posted_moves = env["account.move"].sudo().search_count([
            ("company_id", "=", company.id),
            ("state", "=", "posted"),
        ])
        if posted_moves:
            raise RuntimeError(EXISTING_ACCOUNTING)
        env["account.chart.template"].try_loading(
            "fr_comp",
            company=company,
            install_demo=False,
        )

    purchase_journal = env["account.journal"].sudo().search([
        ("company_id", "=", company.id),
        ("type", "=", "purchase"),
    ], limit=1)
    if not purchase_journal:
        raise RuntimeError(MISSING_CHART)

    company.write({
        "name": QA_COMPANY_NAME,
        "country_id": france.id,
        "account_fiscal_country_id": france.id,
        "currency_id": eur.id,
        "vat": "FR48983982950",
        "company_registry": QA_SIRET,
        "street": "1 rue de la Validation",
        "zip": "75001",
        "city": "Paris",
        "email": "qa-company@example.invalid",
        "phone": "+33142000000",
        "peppol_eas": "0225",
        "peppol_endpoint": QA_SIRET[:9],
        "peppol_purchase_journal_id": purchase_journal.id,
        "account_peppol_contact_email": "qa-manager@example.invalid",
        "account_peppol_phone_number": "+33600000000",
        "rebuild_einvoice_environment": "development",
        "rebuild_einvoice_provider": "odoo_pdp",
        "rebuild_einvoice_activation_approved": False,
        "l10n_fr_pdp_send_to_ppf": False,
        "l10n_fr_pdp_pilot_phase": False,
    })
    env["res.company"]._rebuild_apply_default_einvoice_provider()

    base_user = env.ref("base.group_user")
    manager = _user(
        env,
        login="qa.manager",
        name="QA Accounting Manager",
        password="admin",
        groups=base_user | env.ref("account.group_account_manager"),
        company=company,
    )
    _user(
        env,
        login="qa.reviewer",
        name="QA Read-Only Accountant",
        password="admin",
        groups=(
            base_user
            | env.ref("account.group_account_readonly")
            | env.ref(
                "rebuild_account_migration.group_rebuild_accountant_reviewer",
            )
        ),
        company=company,
    )

    demo_users = company.account_edi_proxy_client_ids.filtered(
        lambda user: user.proxy_type == "pdp" and user.edi_mode == "demo",
    )
    non_demo_users = company.account_edi_proxy_client_ids.filtered(
        lambda user: user.proxy_type == "pdp" and user.edi_mode != "demo",
    )
    if non_demo_users:
        raise RuntimeError(NON_DEMO_CONNECTION)
    if not demo_users:
        wizard = env["pdp.registration"].with_user(
            manager,
        ).with_company(company).with_context(
            rebuild_einvoice_safe_demo=True,
        ).create({
            "company_id": company.id,
        })
        wizard.button_trigger_authentication()
        wizard.button_register_pdp_participant()
        demo_users = company.account_edi_proxy_client_ids.filtered(
            lambda user: user.proxy_type == "pdp" and user.edi_mode == "demo",
        )

    if company.rebuild_einvoice_test_status != "passed":
        company.with_user(manager).action_rebuild_run_einvoice_acceptance_test()
    demo_users._peppol_get_new_documents()

    env.cr.commit()


bootstrap(globals()["env"])
