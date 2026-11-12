"""Disposable browser fixtures; imported explicitly by QA, never by the addon.

Every identity, account and movement in this file is synthetic. FakeClient is
injected only by a task-owned test process; production has no fixture switch.
"""
from datetime import datetime, timedelta, timezone

from odoo import Command
from odoo.tests import new_test_user

from .test_enablebanking import movement, resource, session


class FakeClient:
    def aspsps(self):
        return {"aspsps": [{"name": "Fortuneo", "country": "FR", "psu_types": ["personal"], "maximum_consent_validity": 3600}]}

    def transactions(self, uid, params):
        return {"transactions": [movement("synthetic-entry-a"), movement("synthetic-entry-b")], "continuation_key": None}

    def balances(self, uid):
        return {"balances": [{"name": "Synthetic available balance", "balance_type": "CLAV", "balance_amount": {"amount": "90.00", "currency": "EUR"}, "reference_date": "2026-10-01"}]}


def seed(env):
    currency = env.ref("base.EUR")
    currency.active = True
    company = env["res.company"].with_context(install_demo=True).create({"name": "Banking QA", "currency_id": currency.id, "country_id": env.ref("base.fr").id})
    env["account.chart.template"].try_loading("generic_coa", company=company, install_demo=False)
    company.write({"currency_id": currency.id, "account_fiscal_country_id": env.ref("base.fr").id})
    user = new_test_user(env, name="Banking QA User", login="banking-qa", password="qa",
                         groups="account.group_account_user,account.group_account_manager",
                         company_id=company.id, company_ids=[Command.set(company.ids)])
    user.lang = "en_US"
    connection = env["usl.enablebanking.connection"].with_user(user).with_company(company).create({
        "name": "Fortuneo - Synthetic QA", "app_id": "qa-application", "company_id": company.id,
    })
    connection._apply_session(session([
        resource("qa-current", "qa-hash-current", name="Synthetic current account", product="Current account"),
        resource("qa-savings", "qa-hash-savings", name="Synthetic savings account", product="Savings account", cash_account_type="SVGS"),
        resource("qa-other", "qa-hash-other", name="Synthetic other account", product="Other supported product", cash_account_type="OTHR"),
    ]))
    connection.account_ids.write({"import_from": "2026-10-01"})
    journal = env["account.journal"].with_company(company).create({"name": "Synthetic current bank", "code": "QACR", "type": "bank", "currency_id": currency.id, "company_id": company.id})
    return {"user_id": user.id, "company_id": company.id, "connection_id": connection.id,
            "account_id": connection.account_ids.filtered(lambda a: a.account_type == "CACC").id, "journal_id": journal.id}
