# ruff: noqa: F821, T201

import hashlib
import json
from decimal import Decimal

from odoo.exceptions import AccessError

from odoo.addons.usl_hr_restore.models.restore import (
    HrSourceReader,
    source_binary,
    source_options,
)


def normalized(value):
    if value is False or value is None or value == {} or value == []:
        return None
    if isinstance(value, (Decimal, float)):
        value = Decimal(str(value)).normalize()
        return "0" if not value else format(value, "f")
    return value


def normalized_field(field_name, value):
    if field_name in {
        "children", "color", "distance_home_work", "hourly_cost",
        "no_of_recruitment", "wage",
    } and not value:
        return 0
    return normalized(value)


def digest(rows):
    return hashlib.sha256(
        json.dumps(
            rows,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode(),
    ).hexdigest()


source = HrSourceReader(source_options()).read()
run = env["usl.hr.restore.run"].sudo().search([], order="id desc", limit=1)
assert run and run.status == "passed"
assert run.statistics_json["source"] == source["counts"]
assert run.statistics_json["target"] == source["counts"]


def traced(model, rows):
    records = env[model].sudo().with_context(active_test=False).search(
        [
            ("rebuild_source_model", "=", model),
            ("rebuild_source_id", "in", [row["id"] for row in rows] or [0]),
            ("rebuild_source_snapshot", "=", run.source_snapshot),
        ],
    )
    result = {record.rebuild_source_id: record for record in records}
    assert len(result) == len(rows), (model, len(result), len(rows))
    assert len(records) == len(result), (model, "duplicate source identity")
    return result


datasets = {
    "calendars": "resource.calendar",
    "attendances": "resource.calendar.attendance",
    "employee_types": "hr.employee.type",
    "departments": "hr.department",
    "departure_reasons": "hr.departure.reason",
    "jobs": "hr.job",
    "payroll_structure_types": "hr.payroll.structure.type",
    "work_locations": "hr.work.location",
    "skill_types": "hr.skill.type",
    "skill_levels": "hr.skill.level",
    "skills": "hr.skill",
    "resume_line_types": "hr.resume.line.type",
    "resources": "resource.resource",
    "employees": "hr.employee",
    "versions": "hr.version",
}
mapped = {key: traced(model, source[key]) for key, model in datasets.items()}
assert env["resource.calendar"].sudo().with_context(active_test=False).search_count([]) == len(source["calendars"])
assert env["resource.calendar.attendance"].sudo().search_count([]) == len(source["attendances"])
companies = {
    record.rebuild_source_id: record
    for record in env["res.company"].sudo().with_context(active_test=False).search(
        [("rebuild_source_model", "=", "res.company")],
    )
}
partners = {
    record.rebuild_source_id: record
    for record in env["res.partner"].sudo().with_context(active_test=False).search(
        [("rebuild_source_model", "=", "res.partner")],
    )
}
users = {
    record.rebuild_source_id: record
    for record in env["res.users"].sudo().with_context(active_test=False).search(
        [("rebuild_source_model", "=", "res.users")],
    )
}
banks = {
    record.rebuild_source_id: record
    for record in env["res.partner.bank"].sudo().with_context(active_test=False).search(
        [("rebuild_source_model", "=", "res.partner.bank")],
    )
}
xmlids = {}
for row in source["xmlids"]:
    xmlids.setdefault((row["model"], row["res_id"]), []).append(row["xmlid"])


def reference_source_id(model, target):
    if not target:
        return None
    for (source_model, source_id), candidates in xmlids.items():
        if source_model != model:
            continue
        if any(env.ref(xmlid, raise_if_not_found=False) == target for xmlid in candidates):
            return source_id
    raise AssertionError((model, target.id, "unmapped native reference"))


def source_localized(value):
    if isinstance(value, dict):
        english = value.get("en_US") or value.get("fr_FR") or next(iter(value.values()), "")
        return {"en_US": english, "fr_FR": value.get("fr_FR") or english}
    return {"en_US": value or "", "fr_FR": value or ""}


def target_localized(record, field_name):
    return {
        language: record.with_context(lang=language)[field_name] or ""
        for language in ("en_US", "fr_FR")
    }


source_rows = []
target_rows = []
for row in source["calendars"]:
    record = mapped["calendars"][row["id"]]
    source_rows.append(
        (row["id"], row["company_id"], row["name"], normalized(row["hours_per_day"]),
         row["active"], normalized(row["two_weeks_calendar"]), normalized(row["full_time_required_hours"]),
         normalized(row["hours_per_week"])),
    )
    target_rows.append(
        (row["id"], record.company_id.rebuild_source_id or None, record.name,
         normalized(record.hours_per_day), record.active, normalized(record.two_weeks_calendar),
         normalized(record.full_time_required_hours), normalized(record.hours_per_week)),
    )
for row in source["attendances"]:
    record = mapped["attendances"][row["id"]]
    source_rows.append(
        ("attendance", row["id"], row["calendar_id"], row["sequence"], row["dayofweek"],
         row["day_period"], row["week_type"], normalized(row["hour_from"]),
         normalized(row["hour_to"]), normalized(row["duration_hours"]), row["duration_based"]),
    )
    target_rows.append(
        ("attendance", row["id"], record.calendar_id.rebuild_source_id, record.sequence,
         record.dayofweek, record.day_period, record.week_type or None,
         normalized(record.hour_from), normalized(record.hour_to),
         normalized(record.duration_hours), record.duration_based),
    )

localized_catalogs = {
    "employee_types": (
        "name", ("code", "sequence"), (("country_id", "res.country"),),
    ),
    "departure_reasons": ("name", ("sequence", "active"), (("country_id", "res.country"),)),
    "skill_types": ("name", ("sequence", "color", "levels_count", "active", "is_certification"), ()),
    "skills": ("name", ("sequence",), (("skill_type_id", "hr.skill.type"),)),
    "resume_line_types": ("name", ("sequence", "resume_line_type_properties_definition", "is_course"), ()),
    "departments": ("name", ("color", "note", "active"), ()),
    "jobs": ("name", ("sequence", "no_of_recruitment", "description", "requirements", "active"), ()),
}
for dataset, (name_field, scalar_fields, reference_fields) in localized_catalogs.items():
    for row in source[dataset]:
        record = mapped[dataset][row["id"]]
        source_item = {
            "dataset": dataset,
            "id": row["id"],
            "name": source_localized(row[name_field]),
            **{field: normalized_field(field, row[field]) for field in scalar_fields},
        }
        target_item = {
            "dataset": dataset,
            "id": row["id"],
            "name": target_localized(record, name_field),
            **{field: normalized_field(field, record[field]) for field in scalar_fields},
        }
        for field_name, model in reference_fields:
            source_item[field_name] = row[field_name]
            if model.startswith("hr."):
                target_item[field_name] = record[field_name].rebuild_source_id or None
            else:
                target_item[field_name] = reference_source_id(model, record[field_name])
        source_rows.append(source_item)
        target_rows.append(target_item)

for row in source["departments"]:
    record = mapped["departments"][row["id"]]
    source_rows.append(
        ("department_rel", row["id"], row["company_id"], row["parent_id"],
         row["master_department_id"], row["manager_id"]),
    )
    target_rows.append(
        ("department_rel", row["id"], record.company_id.rebuild_source_id or None,
         record.parent_id.rebuild_source_id or None,
         record.master_department_id.rebuild_source_id or None,
         record.manager_id.rebuild_source_id or None),
    )
for row in source["jobs"]:
    record = mapped["jobs"][row["id"]]
    source_rows.append(
        ("job_rel", row["id"], row["company_id"], row["department_id"],
         row["employee_type_id"], row["recruiter_id"]),
    )
    target_rows.append(
        ("job_rel", row["id"], record.company_id.rebuild_source_id or None,
         record.department_id.rebuild_source_id or None,
         record.employee_type_id.rebuild_source_id or None,
         record.recruiter_id.rebuild_source_id or None),
    )

for row in source["payroll_structure_types"]:
    record = mapped["payroll_structure_types"][row["id"]]
    source_rows.append(("payroll", row["id"], row["name"], row["country_id"], row["default_resource_calendar_id"]))
    target_rows.append(
        ("payroll", row["id"], record.name,
         reference_source_id("res.country", record.country_id),
         record.default_resource_calendar_id.rebuild_source_id or None),
    )
for row in source["work_locations"]:
    record = mapped["work_locations"][row["id"]]
    source_rows.append(
        ("work_location", row["id"], row["name"], row["company_id"],
         row["address_id"], row["location_type"], row["location_number"], row["active"]),
    )
    target_rows.append(
        ("work_location", row["id"], record.name,
         record.company_id.rebuild_source_id,
         record.address_id.rebuild_source_id or None, record.location_type,
         record.location_number or None, record.active),
    )
for row in source["skill_levels"]:
    record = mapped["skill_levels"][row["id"]]
    source_rows.append(
        ("skill_level", row["id"], row["name"], row["skill_type_id"],
         row["level_progress"], normalized(row["default_level"])),
    )
    target_rows.append(
        ("skill_level", row["id"], record.name,
         record.skill_type_id.rebuild_source_id, record.level_progress,
         normalized(record.default_level)),
    )

employee_rows_by_resource = {
    row["resource_id"]: row for row in source["employees"]
}
version_rows_by_id = {row["id"]: row for row in source["versions"]}
for row in source["resources"]:
    record = mapped["resources"][row["id"]]
    employee_row = employee_rows_by_resource.get(row["id"])
    expected_target_tz = (
        version_rows_by_id[employee_row["current_version_id"]]["tz"]
        if employee_row else row["tz"]
    )
    source_rows.append(
        ("resource", row["id"], row["company_id"], row["user_id"], row["calendar_id"],
         row["name"], row["resource_type"], expected_target_tz, row["active"],
         normalized(row["time_efficiency"]), row["color"],
         normalized(row["hours_per_week"]), normalized(row["hours_per_day"])),
    )
    target_rows.append(
        ("resource", row["id"], record.company_id.rebuild_source_id or None,
         record.user_id.rebuild_source_id or None, record.calendar_id.rebuild_source_id or None,
         record.name, record.resource_type, record.tz, record.active,
         normalized(record.time_efficiency), record.color,
         normalized(record.hours_per_week), normalized(record.hours_per_day)),
    )

employee_scalar_fields = (
    "name", "work_phone", "mobile_phone", "work_email", "legal_name",
    "private_phone", "private_email", "lang", "place_of_birth", "permit_no",
    "visa_no", "certificate", "study_field", "emergency_contact",
    "emergency_phone", "barcode", "pin", "birthday",
    "visa_expire", "work_permit_expiration_date", "employee_properties", "active",
    "birthday_public_display", "work_permit_scheduled_activity", "hourly_cost", "color",
    "id_card_name", "driving_license_name", "first_contract_date",
)
day_fields = (
    "monday_location_id", "tuesday_location_id", "wednesday_location_id",
    "thursday_location_id", "friday_location_id", "saturday_location_id",
    "sunday_location_id",
)
source_banks_by_employee = {}
for row in source["employee_bank_accounts"]:
    source_banks_by_employee.setdefault(row["employee_id"], []).append(row["bank_account_id"])
for row in source["employees"]:
    record = mapped["employees"][row["id"]]
    source_salary = row["salary_distribution"] or {}
    reverse_banks = {record.id: source_id for source_id, record in banks.items()}
    target_salary = {
        str(reverse_banks[int(target_id)]): value
        for target_id, value in (record.salary_distribution or {}).items()
    }
    source_item = {
        "dataset": "employee", "id": row["id"],
        **{field: normalized_field(field, row[field]) for field in employee_scalar_fields},
        "resource": row["resource_id"], "company": row["company_id"],
        "current_version": row["current_version_id"], "user": row["user_id"],
        "work_contact": row["work_contact_id"], "country_of_birth": row["country_of_birth"],
        "parent": row["parent_id"], "coach": row["coach_id"],
        "contract_template": row["contract_template_id"],
        "expense_manager": row["expense_manager_id"],
        "banks": sorted(source_banks_by_employee.get(row["id"], [])),
        "salary_distribution": source_salary,
        **{field: row[field] for field in day_fields},
    }
    target_item = {
        "dataset": "employee", "id": row["id"],
        **{field: normalized_field(field, record[field]) for field in employee_scalar_fields},
        "resource": record.resource_id.rebuild_source_id,
        "company": record.company_id.rebuild_source_id,
        "current_version": record.current_version_id.rebuild_source_id,
        "user": record.user_id.rebuild_source_id or None,
        "work_contact": record.work_contact_id.rebuild_source_id or None,
        "country_of_birth": reference_source_id("res.country", record.country_of_birth),
        "parent": record.parent_id.rebuild_source_id or None,
        "coach": record.coach_id.rebuild_source_id or None,
        "contract_template": record.contract_template_id.rebuild_source_id or None,
        "expense_manager": record.expense_manager_id.rebuild_source_id or None,
        "banks": sorted(record.bank_account_ids.mapped("rebuild_source_id")),
        "salary_distribution": target_salary,
        **{field: record[field].rebuild_source_id or None for field in day_fields},
    }
    source_rows.append(source_item)
    target_rows.append(target_item)

version_scalar_fields = (
    "name", "identification_id", "passport_id", "sex", "private_street",
    "private_street2", "private_city", "private_zip", "distance_home_work",
    "km_home_work", "children", "distance_home_work_unit", "marital",
    "spouse_complete_name", "job_title", "date_version",
    "passport_expiration_date", "spouse_birthdate", "contract_date_start",
    "contract_date_end", "trial_date_end", "additional_note", "wage", "active",
    "is_custom_job_title", "is_flexible", "is_fully_flexible",
    "last_modified_date", "tz", "hours_per_week", "hours_per_day", "fixed_term",
)
employee_rows = {row["id"]: row for row in source["employees"]}
resource_rows = {row["id"]: row for row in source["resources"]}
for row in source["versions"]:
    record = mapped["versions"][row["id"]]
    source_item = {
        "dataset": "version", "id": row["id"],
        **{field: normalized_field(field, row[field]) for field in version_scalar_fields},
        "company": row["company_id"], "employee": row["employee_id"],
        "last_modified_uid": row["last_modified_uid"], "country": row["country_id"],
        "private_state": row["private_state_id"], "private_country": row["private_country_id"],
        "department": row["department_id"], "job": row["job_id"],
        "address": row["address_id"], "work_location": row["work_location_id"],
        "calendar": row["resource_calendar_id"], "contract_template": row["contract_template_id"],
        "structure_type": row["structure_type_id"],
        "employee_type_id": row["employee_type_id"],
        "hr_responsible": row["hr_responsible_id"],
    }
    source_item["tz"] = row["tz"] or (
        resource_rows[employee_rows[row["employee_id"]]["resource_id"]]["tz"]
        if row["employee_id"] else None
    ) or (
        companies[row["company_id"]].partner_id.tz
        if row["company_id"] else env.user.tz
    ) or "UTC"
    target_item = {
        "dataset": "version", "id": row["id"],
        **{field: normalized_field(field, record[field]) for field in version_scalar_fields},
        "company": record.company_id.rebuild_source_id or None,
        "employee": record.employee_id.rebuild_source_id or None,
        "last_modified_uid": record.last_modified_uid.rebuild_source_id,
        "country": reference_source_id("res.country", record.country_id),
        "private_state": reference_source_id("res.country.state", record.private_state_id),
        "private_country": reference_source_id("res.country", record.private_country_id),
        "department": record.department_id.rebuild_source_id or None,
        "job": record.job_id.rebuild_source_id or None,
        "address": record.address_id.rebuild_source_id or None,
        "work_location": record.work_location_id.rebuild_source_id or None,
        "calendar": record.resource_calendar_id.rebuild_source_id or None,
        "contract_template": record.contract_template_id.rebuild_source_id or None,
        "structure_type": record.structure_type_id.rebuild_source_id or None,
        "employee_type_id": record.employee_type_id.rebuild_source_id or None,
        "hr_responsible": record.hr_responsible_id.rebuild_source_id,
    }
    source_rows.append(source_item)
    target_rows.append(target_item)

for row in source["company_calendars"]:
    source_rows.append(("company_calendar", row["id"], row["resource_calendar_id"]))
    target_rows.append(("company_calendar", row["id"], companies[row["id"]].resource_calendar_id.rebuild_source_id))

for row in source["images"]:
    source_content = source_binary(row)
    target_content = bytes(mapped["employees"][row["res_id"]].image_1920)
    assert target_content == source_content
    source_rows.append(("image", row["id"], row["res_id"], row["checksum"], row["file_size"]))
    target_rows.append(
        (
            "image",
            row["id"],
            row["res_id"],
            hashlib.sha1(target_content, usedforsecurity=False).hexdigest(),
            len(target_content),
        ),
    )

if source_rows != target_rows:
    for index, (source_row, target_row) in enumerate(zip(source_rows, target_rows)):
        if source_row == target_row:
            continue
        if isinstance(source_row, dict) and isinstance(target_row, dict):
            differing = sorted(
                key for key in set(source_row) | set(target_row)
                if source_row.get(key) != target_row.get(key)
            )
            identity = (source_row.get("dataset"), source_row.get("id"))
        else:
            differing = [
                position for position, values in enumerate(zip(source_row, target_row))
                if values[0] != values[1]
            ]
            identity = source_row[:2]
        raise AssertionError(
            f"HR parity differs at row {index}, identity {identity}, fields {differing}",
        )
    raise AssertionError(
        f"HR parity row counts differ: {len(source_rows)} != {len(target_rows)}",
    )

private_fields_blocked = False
candidate = env["res.users"].sudo().search(
    [
        ("share", "=", False),
        ("group_ids", "not in", env.ref("hr.group_hr_user").id),
        ("id", "not in", [env.ref("base.user_root").id, env.ref("base.user_admin").id]),
    ],
    limit=1,
)
if candidate:
    try:
        mapped["employees"][source["employees"][0]["id"]].with_user(candidate).read(["private_email"])
    except AccessError:
        private_fields_blocked = True
    assert private_fields_blocked, "Private employee fields are exposed to a non-HR user"

evidence = {
    "source_counts": source["counts"],
    "target_counts": run.statistics_json["target"],
    "material_rows": len(source_rows),
    "material_digest": digest(source_rows),
    "target_digest": digest(target_rows),
    "private_fields_blocked": private_fields_blocked if candidate else "no-candidate",
    "delegated": run.statistics_json["delegated"],
}
print(json.dumps(evidence, indent=2, sort_keys=True, default=str))
