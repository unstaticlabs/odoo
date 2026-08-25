"""Prepare a disposable, network-free Expense Batch browser QA database."""

import base64
import os

from odoo import Command, fields

TRUTHY_VALUES = {"1", "true", "yes", "on"}
MISSING_OPT_IN = "Set USL_EXPENSE_BATCH_QA_BOOTSTRAP=1 explicitly."
LIVE_GUARD_ENABLED = "QA bootstrap refuses to run with a live guard enabled."
NON_EMPTY_LEDGER = "QA bootstrap refuses a database that already has posted entries."
TARGET_DATABASE_REQUIRED = "Target QA bootstrap requires the odoo_dev database."
TARGET_ACCOUNT_REQUIRED = "Target QA bootstrap requires account 625600."
ACTIVE_PAYMENT_METHOD_REQUIRED = (
    "Expense Batch QA requires an active outbound bank payment method."
)
LATER_STAGE_QA_DATA = "Refusing to replace later-stage mixed-payer QA data."
QA_DATA_ELSEWHERE = "Mixed-payer QA expenses already belong elsewhere."


def _is_enabled(name):
    return os.getenv(name, "").strip().lower() in TRUTHY_VALUES


def _user(env, *, login, name, groups, company):
    user = env["res.users"].sudo().search([("login", "=", login)], limit=1)
    values = {
        "name": name,
        "login": login,
        "password": "admin",
        "email": f"{login}@example.invalid",
        "company_id": company.id,
        "company_ids": [Command.set(company.ids)],
        "group_ids": [Command.set(groups.ids)],
        "active": True,
    }
    if user:
        updates = {}
        for key, value in values.items():
            if key in ("password", "company_ids", "group_ids"):
                continue
            current = user[key].id if key == "company_id" else user[key]
            if current != value:
                updates[key] = value
        if set(user.company_ids.ids) != set(company.ids):
            updates["company_ids"] = values["company_ids"]
        if not set(groups.ids).issubset(user.group_ids.ids):
            updates["group_ids"] = [Command.set((user.group_ids | groups).ids)]
        if updates:
            user.write(updates)
    else:
        user = env["res.users"].with_context(
            no_reset_password=True,
        ).sudo().create(values)
    return user


def _analytic_account(env, *, name, plan, company):
    account = env["account.analytic.account"].sudo().search([
        ("name", "=", name),
        ("plan_id", "=", plan.id),
        ("company_id", "in", (False, company.id)),
    ], limit=1)
    return account or env["account.analytic.account"].sudo().create({
        "name": name,
        "plan_id": plan.id,
        "company_id": company.id,
    })


def _product(env, *, name, code, account):
    product = env["product.product"].sudo().search([
        ("default_code", "=", code),
    ], limit=1)
    values = {
        "name": name,
        "default_code": code,
        "can_be_expensed": True,
        "property_account_expense_id": account.id,
        "supplier_taxes_id": [Command.clear()],
        "active": True,
    }
    if product:
        updates = {}
        for key, value in values.items():
            if key == "supplier_taxes_id":
                continue
            current = (
                product[key].id
                if key == "property_account_expense_id"
                else product[key]
            )
            if current != value:
                updates[key] = value
        if product.supplier_taxes_id:
            updates["supplier_taxes_id"] = values["supplier_taxes_id"]
        if updates:
            product.write(updates)
    else:
        product = env["product.product"].sudo().create(values)
    return product


def _active_company_payment_method(env, company):
    payment_method = env["account.payment.method.line"].sudo().search([
        ("journal_id.company_id", "=", company.id),
        ("journal_id.type", "=", "bank"),
        ("payment_method_id.payment_type", "=", "outbound"),
        ("payment_account_id.active", "=", True),
    ], order="journal_id, sequence, id", limit=1)
    if not payment_method:
        raise RuntimeError(ACTIVE_PAYMENT_METHOD_REQUIRED)
    return payment_method


def _expense(
    env,
    *,
    employee,
    product,
    name,
    date,
    amount,
    payment_mode,
    payment_method_line=None,
    analytic_distribution=None,
    explicit=False,
    receipt=True,
):
    expense = env["hr.expense"].sudo().search([
        ("employee_id", "=", employee.id),
        ("name", "=", name),
        ("date", "=", date),
    ], limit=1)
    values = {
        "employee_id": employee.id,
        "company_id": employee.company_id.id,
        "product_id": product.id,
        "name": name,
        "date": date,
        "payment_mode": payment_mode,
        "payment_method_line_id": (
            payment_method_line.id if payment_method_line else False
        ),
        "total_amount_currency": amount,
        "analytic_distribution": analytic_distribution or False,
    }
    if analytic_distribution:
        values["analytic_context_source"] = (
            "explicit" if explicit else "inferred"
        )
    else:
        values["analytic_context_source"] = "product"
    if expense:
        if expense.state != "draft":
            message = (
                "Refusing to replace later-stage QA expense "
                f"{expense.display_name}."
            )
            raise RuntimeError(message)
        expense.write(values)
    else:
        expense = env["hr.expense"].sudo().create(values)
    if receipt and not expense.message_main_attachment_id:
        attachment = env["ir.attachment"].sudo().create({
            "name": f"{name}.pdf",
            "type": "binary",
            "datas": base64.b64encode(f"QA receipt: {name}".encode()),
            "res_model": "hr.expense",
            "res_id": expense.id,
        })
        expense.message_main_attachment_id = attachment
    return expense


def bootstrap(env):
    target_mode = _is_enabled("USL_EXPENSE_BATCH_TARGET_QA_BOOTSTRAP")
    if not (_is_enabled("USL_EXPENSE_BATCH_QA_BOOTSTRAP") or target_mode):
        raise RuntimeError(MISSING_OPT_IN)
    if (
        _is_enabled("USL_EINVOICE_LIVE_ENABLED")
        or _is_enabled("USL_EREPORTING_LIVE_ENABLED")
    ):
        raise RuntimeError(LIVE_GUARD_ENABLED)

    company = env.company.sudo()
    if target_mode and env.cr.dbname != "odoo_dev":
        raise RuntimeError(TARGET_DATABASE_REQUIRED)
    if not target_mode and env["account.move"].sudo().search_count([
        ("company_id", "=", company.id),
        ("state", "=", "posted"),
    ]):
        raise RuntimeError(NON_EMPTY_LEDGER)
    if not target_mode and company.chart_template != "fr_comp":
        env["account.chart.template"].try_loading(
            "fr_comp",
            company=company,
            install_demo=False,
        )

    mission_account = env["account.account"].sudo().search([
        ("code", "=", "625600"),
        ("company_ids", "in", company.id),
    ], limit=1)
    if not mission_account and target_mode:
        raise RuntimeError(TARGET_ACCOUNT_REQUIRED)
    if not mission_account:
        mission_account = env["account.account"].sudo().create({
            "code": "625600",
            "name": "Missions",
            "account_type": "expense",
            "company_ids": [Command.set(company.ids)],
        })

    project_plan = env["account.analytic.plan"].sudo().search([
        ("name", "=", "Projet"),
    ], limit=1) or env["account.analytic.plan"].sudo().create({"name": "Projet"})
    epic_plan = env["account.analytic.plan"].sudo().search([
        ("name", "=", "Epic"),
    ], limit=1) or env["account.analytic.plan"].sudo().create({"name": "Epic"})
    project = _analytic_account(
        env,
        name="SBFH prod",
        plan=project_plan,
        company=company,
    )
    epic = _analytic_account(
        env,
        name="Canada 2026",
        plan=epic_plan,
        company=company,
    )
    exception = _analytic_account(
        env,
        name="Executive exception",
        plan=epic_plan,
        company=company,
    )
    transport = _product(
        env,
        name="Transport & Accommodation",
        code="TRANS-QA",
        account=mission_account,
    )
    meals = _product(
        env,
        name="Foreign Meals",
        code="FOOD-QA",
        account=mission_account,
    )
    company_payment_method = _active_company_payment_method(env, company)

    base_user = env.ref("base.group_user")
    submitter = _user(
        env,
        login="qa.expense.submitter",
        name="QA Expense Submitter",
        groups=base_user | env.ref("hr_expense.group_hr_expense_user"),
        company=company,
    )
    _user(
        env,
        login="qa.expense.manager",
        name="QA Expense and Accounting Manager",
        groups=base_user | env.ref("account.group_account_manager"),
        company=company,
    )
    _user(
        env,
        login="qa.expense.readonly",
        name="QA Read-Only Accountant",
        groups=base_user | env.ref("account.group_account_readonly"),
        company=company,
    )
    employee = env["hr.employee"].sudo().search([
        ("user_id", "=", submitter.id),
        ("company_id", "=", company.id),
    ], limit=1) or env["hr.employee"].sudo().create({
        "name": submitter.name,
        "user_id": submitter.id,
        "company_id": company.id,
        "work_contact_id": submitter.partner_id.id,
    })

    distribution = {f"{project.id},{epic.id}": 100.0}
    batch = env["usl.expense.batch"].sudo().search([
        ("name", "=", "QA — Mixed payment Batch"),
        ("employee_id", "=", employee.id),
    ], limit=1)
    values = {
        "name": "QA — Mixed payment Batch",
        "purpose": "Synthetic mixed employee and company payment review",
        "context_type": "travel",
        "context_date_from": fields.Date.from_string("2026-07-01"),
        "context_date_to": fields.Date.from_string("2026-07-31"),
        "employee_id": employee.id,
        "company_id": company.id,
        "account_override_id": mission_account.id,
        "analytic_distribution": distribution,
    }
    if batch:
        updates = {}
        for key, value in values.items():
            current = batch[key]
            if key in ("employee_id", "company_id", "account_override_id"):
                current = current.id
            if current != value:
                updates[key] = value
        if updates:
            batch.write(updates)
    else:
        batch = env["usl.expense.batch"].sudo().create(values)

    expense_names = (
        "QA Toronto hotel",
        "QA Toronto taxi company card",
        "QA Toronto team meal exception",
        "QA Toronto missing receipt",
    )
    existing_expenses = env["hr.expense"].sudo().search([
        ("employee_id", "=", employee.id),
        ("name", "in", expense_names),
    ])
    if target_mode and len(existing_expenses) == len(expense_names):
        if any(existing_expenses.filtered(lambda expense: expense.state != "draft")):
            raise RuntimeError(LATER_STAGE_QA_DATA)
        if existing_expenses.filtered(lambda expense: expense.expense_batch_id != batch):
            raise RuntimeError(QA_DATA_ELSEWHERE)
        existing_expenses.filtered(
            lambda expense: expense.payment_mode == "company_account",
        ).write({"payment_method_line_id": company_payment_method.id})
        env.cr.commit()
        return

    expenses = env["hr.expense"]
    expenses |= _expense(
        env,
        employee=employee,
        product=transport,
        name="QA Toronto hotel",
        date=fields.Date.from_string("2026-07-10"),
        amount=480,
        payment_mode="own_account",
        analytic_distribution=distribution,
    )
    expenses |= _expense(
        env,
        employee=employee,
        product=transport,
        name="QA Toronto taxi company card",
        date=fields.Date.from_string("2026-07-11"),
        amount=45,
        payment_mode="company_account",
        payment_method_line=company_payment_method,
        analytic_distribution=distribution,
    )
    expenses |= _expense(
        env,
        employee=employee,
        product=meals,
        name="QA Toronto team meal exception",
        date=fields.Date.from_string("2026-07-12"),
        amount=92,
        payment_mode="own_account",
        analytic_distribution={f"{project.id},{exception.id}": 100.0},
        explicit=True,
    )
    expenses |= _expense(
        env,
        employee=employee,
        product=meals,
        name="QA Toronto missing receipt",
        date=fields.Date.from_string("2026-07-13"),
        amount=18,
        payment_mode="company_account",
        payment_method_line=company_payment_method,
        receipt=False,
    )
    if target_mode:
        if expenses.filtered(
            lambda expense: expense.expense_batch_id
            and expense.expense_batch_id != batch,
        ):
            raise RuntimeError(QA_DATA_ELSEWHERE)
        expenses.filtered(lambda expense: not expense.expense_batch_id).write({
            "expense_batch_id": batch.id,
        })
    else:
        expenses.filtered("expense_batch_id").write({"expense_batch_id": False})
    env.cr.commit()


bootstrap(globals()["env"])
