# ruff: noqa: F821, T201

import json

from odoo.tools import html2plaintext

from odoo.addons.usl_tese_payroll.models.constants import TESE_COMPONENTS

EXPECTED = {
    "employees": 2,
    "versions": 3,
    "profiles": 4,
    "payslips": 9,
    "payroll_moves": 9,
    "payroll_pdfs": 9,
    "employee_pdfs": 14,
    "messages": 30,
    "tracking_values": 57,
    "followers": 3,
    "paid": 5,
    "to_reconcile": 4,
}


def fail(message):
    raise RuntimeError(message)


def mapped(source_model, source_id, target_model):
    mapping = env["usl.tese.restore.mapping"].sudo().search([
        ("source_model", "=", source_model),
        ("source_id", "=", source_id),
        ("target_model", "=", target_model),
    ], limit=1)
    return (
        env[target_model].sudo().browse(mapping.target_id).exists()
        if mapping
        else env[target_model]
    )


def same_number(left, right):
    return abs(float(left or 0) - float(right or 0)) <= 0.000001


parity_errors = []


def parity(condition, source_model, source_id, field_name):
    if not condition and len(parity_errors) < 30:
        parity_errors.append({
            "source_model": source_model,
            "source_id": source_id,
            "field": field_name,
        })


run = env["usl.tese.restore.run"].sudo().search([], limit=1)
if not run:
    fail("No TESE restoration run exists.")
if run.status != "passed":
    fail(
        f"The latest TESE restoration status is {run.status!r}, not 'passed'.",
    )
blocking = run.issue_ids.filtered(lambda issue: issue.severity == "error")
if blocking:
    fail(
        f"The latest TESE restoration has {len(blocking)} blocking issue(s).",
    )
statistics = run.statistics_json or {}
differences = {
    key: {"expected": expected, "actual": statistics.get(key)}
    for key, expected in EXPECTED.items()
    if statistics.get(key) != expected
}
if differences:
    fail(f"TESE source/target parity failed: {differences}.")

payslips = env["usl.tese.payslip"].sudo().search([
    ("id", "in", env["usl.tese.restore.mapping"].sudo().search([
        ("source_model", "=", "x_tese_payslip"),
        ("target_model", "=", "usl.tese.payslip"),
    ]).mapped("target_id")),
])
if len(payslips) != EXPECTED["payslips"]:
    fail("The mapped TESE payroll record count is incorrect.")
if len(payslips.mapped("move_id")) != len(payslips):
    fail("Every TESE payroll record must have one accounting entry.")
if any(move.state != "posted" for move in payslips.mapped("move_id")):
    fail("Every restored TESE accounting entry must remain posted.")
if len(payslips.mapped("attachment_id")) != len(payslips):
    fail("Every restored TESE payroll record must link one PDF.")
if any(len(payslip.component_line_ids) != 11 for payslip in payslips):
    fail("Every restored TESE snapshot must contain 11 components.")
if any(not payslip.currency_id.is_zero(payslip.balance_difference) for payslip in payslips):
    fail("Every restored TESE payroll entry must remain balanced.")
attachment_mappings = env["usl.tese.restore.mapping"].sudo().search([
    ("source_model", "=", "ir.attachment"),
    ("target_model", "=", "ir.attachment"),
])
attachments = env["ir.attachment"].sudo().browse(
    attachment_mappings.mapped("target_id"),
).exists()
if len(attachment_mappings) != EXPECTED["employee_pdfs"]:
    fail("The restored employee-folder PDF mapping count is incorrect.")
if len(attachments) != len(attachment_mappings):
    fail("One or more restored employee-folder PDFs no longer exist.")
checksum_by_target = {
    mapping.target_id: mapping.source_checksum
    for mapping in attachment_mappings
}
checksum_mismatches = attachments.filtered(
    lambda attachment: (
        attachment.checksum != checksum_by_target[attachment.id]
        or attachment.mimetype != "application/pdf"
    ),
)
if checksum_mismatches:
    fail(
        "Restored employee-folder PDF checksum or MIME parity failed: "
        f"{checksum_mismatches.ids}.",
    )

payload = run._load_source_payload()
profile_mappings = env["usl.tese.restore.mapping"].sudo().search([
    ("source_model", "=", "x_tese_payroll_profile"),
    ("target_model", "=", "usl.tese.profile"),
])
all_profiles = env["usl.tese.profile"].sudo().with_context(
    active_test=False,
).browse(profile_mappings.mapped("target_id")).exists()
if len(all_profiles.filtered("active")) != 1:
    fail("Exactly one restored TESE profile must remain active.")
if len(all_profiles.filtered(lambda profile: not profile.active)) != 3:
    fail("Exactly three restored TESE profiles must remain archived.")

for row in payload["employees"]:
    employee = mapped("hr.employee", row["id"], "hr.employee")
    parity(bool(employee), "hr.employee", row["id"], "mapping")
    if not employee:
        continue
    for source_field, target_field in (
        ("name", "name"),
        ("work_email", "work_email"),
        ("work_phone", "work_phone"),
        ("mobile_phone", "mobile_phone"),
        ("legal_name", "legal_name"),
        ("active", "active"),
    ):
        expected = (
            run._text(row.get(source_field))
            if source_field in {"name", "legal_name"}
            else row.get(source_field)
        )
        actual = getattr(employee, target_field)
        parity(
            (actual or False) == (expected or False),
            "hr.employee",
            row["id"],
            target_field,
        )
    expected_current_version = mapped(
        "hr.version",
        row.get("current_version_id"),
        "hr.version",
    )
    parity(
        employee.current_version_id == expected_current_version,
        "hr.employee",
        row["id"],
        "current_version_id",
    )

for row in payload["versions"]:
    version = mapped("hr.version", row["id"], "hr.version")
    parity(bool(version), "hr.version", row["id"], "mapping")
    if not version:
        continue
    for source_field, target_field in (
        ("date_version", "date_version"),
        ("contract_date_start", "contract_date_start"),
        ("contract_date_end", "contract_date_end"),
        ("trial_date_end", "trial_date_end"),
        ("job_title", "job_title"),
        ("employee_type", "employee_type"),
        ("active", "active"),
    ):
        parity(
            (getattr(version, target_field) or False)
            == (row.get(source_field) or False),
            "hr.version",
            row["id"],
            target_field,
        )
    for source_field, target_field in (
        ("wage", "wage"),
        ("hours_per_week", "hours_per_week"),
        ("hours_per_day", "hours_per_day"),
    ):
        parity(
            same_number(
                getattr(version, target_field),
                row.get(source_field),
            ),
            "hr.version",
            row["id"],
            target_field,
        )
    expected_employee = mapped(
        "hr.employee",
        row.get("employee_id"),
        "hr.employee",
    )
    parity(
        version.employee_id == expected_employee,
        "hr.version",
        row["id"],
        "employee_id",
    )

profile_fields = (
    ("x_valid_from", "valid_from"),
    ("x_valid_to", "valid_to"),
    ("x_review_status", "review_status"),
    ("x_review_message", "review_message"),
    ("x_last_used_date", "last_used_date"),
    ("x_active", "active"),
)
profile_numbers = (
    ("x_default_hours", "default_hours"),
    ("x_gross_salary", "gross_salary"),
    ("x_employee_contrib_total", "employee_contribution_total"),
    ("x_employer_contrib_total", "employer_contribution_total"),
    ("x_net_social", "net_social"),
    ("x_net_before_tax", "net_before_tax"),
    ("x_income_tax_base", "income_tax_base"),
    ("x_income_tax_rate", "income_tax_rate"),
    ("x_income_tax_amount", "income_tax_amount"),
    ("x_net_paid", "net_paid"),
)
for row in payload["profiles"]:
    profile = mapped(
        "x_tese_payroll_profile",
        row["id"],
        "usl.tese.profile",
    )
    parity(bool(profile), "x_tese_payroll_profile", row["id"], "mapping")
    if not profile:
        continue
    parity(
        profile.name == run._text(row.get("x_name")),
        "x_tese_payroll_profile",
        row["id"],
        "name",
    )
    for source_field, target_field in profile_fields:
        expected = row.get(source_field)
        if source_field == "x_review_status" and not expected:
            expected = "to_review"
        parity(
            (getattr(profile, target_field) or False)
            == (expected or False),
            "x_tese_payroll_profile",
            row["id"],
            target_field,
        )
    for source_field, target_field in profile_numbers:
        parity(
            same_number(
                getattr(profile, target_field),
                row.get(source_field),
            ),
            "x_tese_payroll_profile",
            row["id"],
            target_field,
        )
    expected_employee = mapped(
        "hr.employee",
        row.get("x_employee_id"),
        "hr.employee",
    )
    expected_version = mapped(
        "hr.version",
        row.get("x_hr_version_id"),
        "hr.version",
    )
    parity(
        profile.employee_id == expected_employee,
        "x_tese_payroll_profile",
        row["id"],
        "employee_id",
    )
    parity(
        profile.hr_version_id == expected_version,
        "x_tese_payroll_profile",
        row["id"],
        "hr_version_id",
    )
    lines_by_code = {
        line.code: line for line in profile.component_line_ids
    }
    for component in TESE_COMPONENTS:
        code = component["code"]
        line = lines_by_code.get(code)
        parity(
            bool(line)
            and line.account_id.code == code
            and same_number(line.amount, row.get(f"x_amount_{code}")),
            "x_tese_payroll_profile",
            row["id"],
            f"component_{code}",
        )

payslip_fields = (
    ("x_period_start", "period_start"),
    ("x_period_end", "period_end"),
    ("x_payment_date", "payment_date"),
    ("x_payslip_date", "payslip_date"),
    ("x_tese_payment_date", "tese_payment_date"),
    ("x_tese_reference", "tese_reference"),
    ("x_move_ref", "move_ref"),
)
payslip_numbers = (
    ("x_hours", "hours"),
    ("x_gross_salary", "gross_salary"),
    ("x_employee_contrib_total", "employee_contribution_total"),
    ("x_employer_contrib_total", "employer_contribution_total"),
    ("x_net_social", "net_social"),
    ("x_net_before_tax", "net_before_tax"),
    ("x_income_tax_base", "income_tax_base"),
    ("x_income_tax_rate", "income_tax_rate"),
    ("x_income_tax_amount", "income_tax_amount"),
    ("x_net_paid", "net_paid"),
    ("x_total_debit", "total_debit"),
    ("x_total_credit", "total_credit"),
    ("x_balance_diff", "balance_difference"),
    ("x_tese_bank_amount", "tese_bank_amount"),
    ("x_tese_bank_diff", "tese_bank_difference"),
)
for row in payload["payslips"]:
    payslip = mapped("x_tese_payslip", row["id"], "usl.tese.payslip")
    parity(bool(payslip), "x_tese_payslip", row["id"], "mapping")
    if not payslip:
        continue
    parity(
        payslip.name == run._text(row.get("x_name")),
        "x_tese_payslip",
        row["id"],
        "name",
    )
    parity(
        payslip.pay_period == row.get("x_period_start"),
        "x_tese_payslip",
        row["id"],
        "pay_period",
    )
    for source_field, target_field in payslip_fields:
        parity(
            (getattr(payslip, target_field) or False)
            == (row.get(source_field) or False),
            "x_tese_payslip",
            row["id"],
            target_field,
        )
    for source_field, target_field in payslip_numbers:
        parity(
            same_number(
                getattr(payslip, target_field),
                row.get(source_field),
            ),
            "x_tese_payslip",
            row["id"],
            target_field,
        )
    for source_field, source_model, target_model, target_field in (
        ("x_employee_id", "hr.employee", "hr.employee", "employee_id"),
        (
            "x_profile_id",
            "x_tese_payroll_profile",
            "usl.tese.profile",
            "profile_id",
        ),
        ("x_hr_version_id", "hr.version", "hr.version", "hr_version_id"),
    ):
        expected_record = mapped(
            source_model,
            row.get(source_field),
            target_model,
        )
        parity(
            getattr(payslip, target_field) == expected_record,
            "x_tese_payslip",
            row["id"],
            target_field,
        )
    parity(
        payslip.move_id.rebuild_source_id == row.get("x_move_id"),
        "x_tese_payslip",
        row["id"],
        "move_id",
    )
    source_attachment = mapped(
        "ir.attachment",
        row.get("source_attachment_id"),
        "ir.attachment",
    )
    parity(
        payslip.attachment_id == source_attachment,
        "x_tese_payslip",
        row["id"],
        "attachment_id",
    )
    lines_by_code = {
        line.code: line for line in payslip.component_line_ids
    }
    for component in TESE_COMPONENTS:
        code = component["code"]
        line = lines_by_code.get(code)
        parity(
            bool(line)
            and line.account_id.code == code
            and same_number(line.amount, row.get(f"x_amount_{code}")),
            "x_tese_payslip",
            row["id"],
            f"component_{code}",
        )

for row in payload["messages"]:
    message = mapped("mail.message", row["id"], "mail.message")
    parity(bool(message), "mail.message", row["id"], "mapping")
    if not message:
        continue
    for source_field, target_field in (
        ("model", "model"),
        ("subject", "subject"),
        ("body", "body"),
        ("message_type", "message_type"),
        ("email_from", "email_from"),
        ("date", "date"),
    ):
        expected = (
            run._text(row.get(source_field))
            if source_field in {"subject", "body"}
            else row.get(source_field)
        )
        actual = getattr(message, target_field)
        if source_field == "body":
            expected = html2plaintext(expected).strip()
            actual = html2plaintext(actual).strip()
        parity(
            (str(actual) if actual else False)
            == (str(expected) if expected else False),
            "mail.message",
            row["id"],
            target_field,
        )

for row in payload["tracking"]:
    tracking_value = mapped(
        "mail.tracking.value",
        row["id"],
        "mail.tracking.value",
    )
    parity(
        bool(tracking_value),
        "mail.tracking.value",
        row["id"],
        "mapping",
    )
    if not tracking_value:
        continue
    for field_name in (
        "old_value_integer",
        "new_value_integer",
        "old_value_char",
        "new_value_char",
        "old_value_text",
        "new_value_text",
        "old_value_datetime",
        "new_value_datetime",
        "old_value_float",
        "new_value_float",
    ):
        source_value = row.get(field_name)
        target_value = getattr(tracking_value, field_name)
        condition = (
            same_number(target_value, source_value)
            if field_name.endswith(("integer", "float"))
            else (target_value or False) == (source_value or False)
        )
        parity(
            condition,
            "mail.tracking.value",
            row["id"],
            field_name,
        )

if parity_errors:
    fail(f"TESE field-level source/target parity failed: {parity_errors}.")
if len(payslips.filtered(lambda item: item.state == "paid")) != 5:
    fail("Residual-derived paid status parity failed.")
if len(payslips.filtered(lambda item: item.state == "to_reconcile")) != 4:
    fail("Residual-derived open status parity failed.")

print(json.dumps({
    "run_id": run.id,
    "status": "passed",
    "statistics": statistics,
}, indent=2, sort_keys=True))
