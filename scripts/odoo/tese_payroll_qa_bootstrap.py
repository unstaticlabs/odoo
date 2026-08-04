"""Seed production-shaped, synthetic Paie TESE journeys in ``odoo_dev``.

The fixture deliberately lives outside the product module.  It reuses the
target company's configured accounts and journals, but every business record
is synthetic and visibly prefixed with a QA generation.
"""

import base64
import os

from dateutil.relativedelta import relativedelta

from odoo import Command, fields

TRUTHY_VALUES = {"1", "true", "yes", "on"}
QA_PREFIX = "QA-TESE"
MISSING_OPT_IN = "Set USL_TESE_PAYROLL_QA_BOOTSTRAP=1 explicitly."
LIVE_GUARD_ENABLED = "QA bootstrap refuses to run with a live guard enabled."
UNSAFE_DATABASE = "QA bootstrap refuses this database. Use odoo_dev or a named *_qa database."
MISSING_PRODUCT = "Install and configure USL TESE Payroll before seeding QA scenarios."
MISSING_TEMPLATE = "No complete non-QA TESE profile is available as a fixture template."
MISSING_BANK = "A bank journal with a reconcilable suspense account is required."
INVALID_GENERATION = "USL_TESE_PAYROLL_QA_GENERATION must be between 01 and 99."
GENERATION_USED = (
    "This QA generation has been changed by a demo. Choose the next generation "
    "instead of rewriting posted accounting history."
)
QA_PDF = b"%PDF-1.4\n% Synthetic Paie TESE QA evidence only\n%%EOF\n"


def _is_enabled(name):
    return os.getenv(name, "").strip().lower() in TRUTHY_VALUES


def _generation():
    generation = os.getenv("USL_TESE_PAYROLL_QA_GENERATION", "01").strip()
    if not generation.isdigit() or not 1 <= int(generation) <= 99:
        raise RuntimeError(INVALID_GENERATION)
    return f"{int(generation):02d}"


def _check_safety(env):
    if not _is_enabled("USL_TESE_PAYROLL_QA_BOOTSTRAP"):
        raise RuntimeError(MISSING_OPT_IN)
    if (
        _is_enabled("USL_EINVOICE_LIVE_ENABLED")
        or _is_enabled("USL_EREPORTING_LIVE_ENABLED")
    ):
        raise RuntimeError(LIVE_GUARD_ENABLED)
    expected_database = os.getenv(
        "ODOO_TESE_PAYROLL_QA_DATABASE",
        "odoo_dev",
    )
    if (
        env.cr.dbname != expected_database
        or env.cr.dbname == "odoo_online_source_saas_19_2"
        or not (env.cr.dbname == "odoo_dev" or env.cr.dbname.endswith("_qa"))
    ):
        raise RuntimeError(UNSAFE_DATABASE)
    module = env["ir.module.module"].sudo().search(
        [("name", "=", "usl_tese_payroll")],
        limit=1,
    )
    if not module or module.state != "installed":
        raise RuntimeError(MISSING_PRODUCT)


def _template_profile(env, company):
    profiles = env["usl.tese.profile"].sudo().search(
        [
            ("company_id", "=", company.id),
            ("active", "=", True),
            ("name", "not ilike", "[QA TESE"),
        ],
        order="last_used_date desc, valid_from desc, id desc",
    )
    template = profiles.filtered(lambda profile: len(profile.component_line_ids) == 11)[:1]
    if not template:
        raise RuntimeError(MISSING_TEMPLATE)
    template._validate_components()
    return template


def _scaled_profile_values(template, scale):
    amounts = {
        line.code: round(line.amount * scale, 2)
        for line in template.component_line_ids
    }
    debit = sum(
        amounts[line.code]
        for line in template.component_line_ids
        if line.side == "debit"
    )
    credit = sum(
        amounts[line.code]
        for line in template.component_line_ids
        if line.side == "credit"
    )
    amounts["431000"] = round(amounts["431000"] + debit - credit, 2)

    gross = amounts["641100"]
    net_paid = amounts["421000"]
    income_tax = amounts["442100"]
    net_before_tax = round(net_paid + income_tax, 2)
    employer_total = sum(
        amounts[line.code]
        for line in template.component_line_ids
        if line.role == "employer_contribution"
    )
    return {
        "default_hours": template.default_hours,
        "gross_salary": gross,
        "employee_contribution_total": round(gross - net_before_tax, 2),
        "employer_contribution_total": round(employer_total, 2),
        "net_social": net_before_tax,
        "net_before_tax": net_before_tax,
        "income_tax_base": round(template.income_tax_base * scale, 2),
        "income_tax_rate": template.income_tax_rate,
        "income_tax_amount": income_tax,
        "net_paid": net_paid,
        "component_line_ids": [
            Command.create(
                {
                    "sequence": line.sequence,
                    "code": line.code,
                    "name": line.name,
                    "side": line.side,
                    "role": line.role,
                    "account_id": line.account_id.id,
                    "amount": amounts[line.code],
                },
            )
            for line in template.component_line_ids.sorted("sequence")
        ],
    }


def _ensure_employee_and_profile(
    env,
    *,
    company,
    template,
    generation,
    sequence,
    slug,
    label,
    valid_from,
    scale,
    mismatch=False,
):
    marker = f"[QA TESE {generation}] {sequence:02d} {label}"
    email = f"qa.tese.{generation}.{slug}@example.invalid"
    employee = env["hr.employee"].sudo().search(
        [("work_email", "=", email)],
        limit=1,
    )
    profile = env["usl.tese.profile"].sudo().with_context(
        active_test=False,
    ).search(
        [("name", "=", marker), ("company_id", "=", company.id)],
        limit=1,
    )
    values = _scaled_profile_values(template, scale)
    if not employee:
        employee = env["hr.employee"].sudo().create(
            {
                "name": marker,
                "company_id": company.id,
                "work_email": email,
            },
        )
        employee.version_id.sudo().write(
            {
                "date_version": valid_from,
                "contract_date_start": valid_from,
                "wage": values["gross_salary"],
                "hours_per_week": (
                    40.0
                    if mismatch
                    else values["default_hours"] * 12.0 / 52.0
                ),
            },
        )
    if not profile:
        profile = env["usl.tese.profile"].sudo().create(
            {
                "name": marker,
                "company_id": company.id,
                "employee_id": employee.id,
                "hr_version_id": employee.version_id.id,
                "collector_partner_id": (
                    template.collector_partner_id.id
                    or company.tese_collector_partner_id.id
                ),
                "valid_from": valid_from,
                "active": True,
                "review_status": "ok",
                **values,
            },
        )
    if (
        not profile.active
        or profile.employee_id != employee
        or profile.hr_version_id != employee.version_id
    ):
        raise RuntimeError(f"{GENERATION_USED} Profile: {marker}")
    profile._validate_components()
    return employee, profile


def _ensure_pdf(env, payslip):
    attachment = env["ir.attachment"].sudo().search(
        [
            ("res_model", "=", payslip._name),
            ("res_id", "=", payslip.id),
            ("name", "=", f"{payslip.tese_reference}.pdf"),
        ],
        limit=1,
    )
    if not attachment:
        attachment = env["ir.attachment"].sudo().create(
            {
                "name": f"{payslip.tese_reference}.pdf",
                "type": "binary",
                "mimetype": "application/pdf",
                "datas": base64.b64encode(QA_PDF),
                "res_model": payslip._name,
                "res_id": payslip.id,
            },
        )
    if not payslip.attachment_id:
        payslip.attachment_id = attachment
    return attachment


def _ensure_payslip(
    env,
    *,
    company,
    employee,
    profile,
    generation,
    slug,
    pay_period,
    target_state,
):
    reference = f"{QA_PREFIX}-{generation}-{slug.upper()}"
    payslip = env["usl.tese.payslip"].sudo().search(
        [("company_id", "=", company.id), ("tese_reference", "=", reference)],
        limit=1,
    )
    if not payslip:
        payslip = env["usl.tese.payslip"].sudo().create(
            {
                "company_id": company.id,
                "employee_id": employee.id,
                "profile_id": profile.id,
                "pay_period": pay_period,
                "tese_reference": reference,
                "document_note": "Synthetic feature-branch QA evidence.",
            },
        )
        payslip.action_prepare()
        payslip.action_create_draft_entry()
        if target_state != "to_post":
            _ensure_pdf(env, payslip)
            payslip.action_post()
    return payslip


def _bank_journal(env, company):
    journal = env["account.journal"].sudo().search(
        [
            ("company_id", "=", company.id),
            ("type", "=", "bank"),
            ("suspense_account_id", "!=", False),
            ("suspense_account_id.reconcile", "=", True),
        ],
        order="id",
        limit=1,
    )
    if not journal:
        raise RuntimeError(MISSING_BANK)
    return journal


def _ensure_bank_line(
    env,
    *,
    journal,
    partner,
    payment_date,
    amount,
    label,
):
    statement_line = env["account.bank.statement.line"].sudo().search(
        [
            ("journal_id", "=", journal.id),
            ("payment_ref", "=", label),
        ],
        limit=1,
    )
    if not statement_line:
        statement = env["account.bank.statement"].sudo().create(
            {
                "journal_id": journal.id,
                "date": payment_date,
                "name": label,
            },
        )
        statement_line = env["account.bank.statement.line"].sudo().create(
            {
                "name": label,
                "payment_ref": label,
                "journal_id": journal.id,
                "statement_id": statement.id,
                "amount": -amount,
                "date": payment_date,
                "partner_id": partner.id,
            },
        )
    return statement_line


def _ensure_candidates(env, journal, payslip, scenario, generation, *, rounding=0.0, duplicate=False):
    counts = ("A", "B") if duplicate else ("A",)
    for suffix in counts:
        _ensure_bank_line(
            env,
            journal=journal,
            partner=payslip.employee_partner_snapshot_id,
            payment_date=payslip.payment_date,
            amount=payslip.net_paid,
            label=f"{QA_PREFIX} {generation} {scenario} SALARY {suffix} {payslip.employee_snapshot_name}",
        )
        _ensure_bank_line(
            env,
            journal=journal,
            partner=payslip.collector_partner_id,
            payment_date=payslip.tese_payment_date,
            amount=payslip.tese_detailed_total + rounding,
            label=f"{QA_PREFIX} {generation} {scenario} URSSAF {suffix}",
        )
    payslip.action_refresh_candidates()


def _ensure_filter(env, *, name, model, domain):
    existing = env["ir.filters"].sudo().search(
        [("name", "=", name), ("model_id", "=", model)],
        limit=1,
    )
    values = {
        "name": name,
        "model_id": model,
        "domain": repr(domain),
        "context": "{}",
        "sort": "[]",
        "is_default": False,
        "user_ids": [Command.clear()],
        "active": True,
    }
    if existing:
        existing.write(values)
    else:
        env["ir.filters"].sudo().create(values)


def _assert_fixture(env, data, last_completed, generation):
    monthly = data["monthly"]
    monthly_payroll = env["usl.tese.payslip"].sudo().search(
        [
            ("company_id", "=", monthly[0].company_id.id),
            ("employee_id", "=", monthly[0].id),
        ],
    )
    checks = [
        (not monthly_payroll, "monthly creation employee already has payroll"),
        (
            env["usl.tese.payslip"]._suggest_pay_period(
                monthly[0],
                monthly[0].company_id,
            ) == last_completed,
            "monthly creation does not suggest the last completed month",
        ),
        (monthly[1].has_hr_mismatch, "monthly profile must show the TESE/HR warning"),
        (
            data["missing_pdf"].state == "to_post" and not data["missing_pdf"].attachment_id,
            "missing-PDF scenario was changed",
        ),
        (
            data["exact"].state == "to_reconcile"
            and data["exact"].salary_payment_candidate_count == 1
            and data["exact"].tese_payment_candidate_count == 1,
            "exact-match scenario was changed",
        ),
        (
            data["rounding"].state == "to_reconcile"
            and data["rounding"].salary_payment_candidate_count == 1
            and data["rounding"].tese_payment_candidate_count == 1
            and abs(data["rounding"].tese_payment_candidate_difference - 0.55) < 0.001,
            "rounding scenario was changed",
        ),
        (
            data["ambiguous"].state == "to_reconcile"
            and data["ambiguous"].salary_payment_candidate_count == 2
            and data["ambiguous"].tese_payment_candidate_count == 2,
            "ambiguous scenario was changed",
        ),
        (
            data["paid"].state == "paid"
            and data["paid"].payment_status == "paid"
            and data["paid"].currency_id.is_zero(data["paid"].salary_open_amount)
            and data["paid"].currency_id.is_zero(data["paid"].tese_open_amount),
            "paid scenario was changed",
        ),
    ]
    failures = [message for ok, message in checks if not ok]
    if failures:
        raise RuntimeError(
            f"{GENERATION_USED} Generation {generation}: {'; '.join(failures)}",
        )


def bootstrap(env):
    _check_safety(env)
    generation = _generation()
    company = env.company.sudo()
    template = _template_profile(env, company)
    if not company.tese_payroll_journal_id or not company.tese_collector_partner_id:
        raise RuntimeError(MISSING_PRODUCT)
    journal = _bank_journal(env, company)
    current_month = fields.Date.context_today(env.user).replace(day=1)
    last_completed = current_month - relativedelta(months=1)

    specs = (
        (1, "monthly", "Monthly creation", 1.00, True),
        (2, "missing", "Missing PDF", 1.10, False),
        (3, "exact", "Exact matching", 1.20, False),
        (4, "rounding", "URSSAF rounding", 1.35, False),
        (5, "ambiguous", "Ambiguous bank", 1.50, False),
        (6, "paid", "Paid overview", 1.65, False),
    )
    employees_and_profiles = {
        slug: _ensure_employee_and_profile(
            env,
            company=company,
            template=template,
            generation=generation,
            sequence=sequence,
            slug=slug,
            label=label,
            valid_from=last_completed,
            scale=scale,
            mismatch=mismatch,
        )
        for sequence, slug, label, scale, mismatch in specs
    }
    data = {"monthly": employees_and_profiles["monthly"]}
    for slug, target_state in (
        ("missing", "to_post"),
        ("exact", "to_reconcile"),
        ("rounding", "to_reconcile"),
        ("ambiguous", "to_reconcile"),
        ("paid", "paid"),
    ):
        employee, profile = employees_and_profiles[slug]
        data["missing_pdf" if slug == "missing" else slug] = _ensure_payslip(
            env,
            company=company,
            employee=employee,
            profile=profile,
            generation=generation,
            slug=slug,
            pay_period=last_completed,
            target_state=target_state,
        )

    _ensure_candidates(env, journal, data["exact"], "EXACT", generation)
    _ensure_candidates(
        env,
        journal,
        data["rounding"],
        "ROUNDING",
        generation,
        rounding=0.55,
    )
    _ensure_candidates(
        env,
        journal,
        data["ambiguous"],
        "AMBIGUOUS",
        generation,
        duplicate=True,
    )
    if data["paid"].state != "paid":
        _ensure_candidates(env, journal, data["paid"], "PAID", generation)
        data["paid"].action_reconcile_salary()
        data["paid"].action_reconcile_tese()

    _ensure_filter(
        env,
        name=f"QA TESE {generation} · Payroll scenarios",
        model="usl.tese.payslip",
        domain=[("tese_reference", "ilike", f"{QA_PREFIX}-{generation}-")],
    )
    _ensure_filter(
        env,
        name=f"QA TESE {generation} · Profiles",
        model="usl.tese.profile",
        domain=[("name", "ilike", f"[QA TESE {generation}]")],
    )
    env["usl.tese.diagnostic.issue"].sudo().action_run_diagnostics()
    _assert_fixture(env, data, last_completed, generation)
    env.cr.commit()
    print(  # noqa: T201 - visible confirmation is the script's CLI contract
        f"TESE_QA_READY generation={generation} month={last_completed}",
    )
    print(  # noqa: T201
        f"TESE_QA_MONTHLY_EMPLOYEE={employees_and_profiles['monthly'][0].name}",
    )
    print(  # noqa: T201
        f"TESE_QA_PAYROLL_FILTER=QA TESE {generation} · Payroll scenarios",
    )


bootstrap(globals()["env"])
