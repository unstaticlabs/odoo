import os

import psycopg2
import psycopg2.extras

from odoo import Command, fields, models


RESTORE_REVISION = 1


class HrSourceReader:
    def __init__(self, options):
        self.options = options

    def _connect(self):
        connection = psycopg2.connect(
            host=self.options["host"],
            port=self.options["port"],
            user=self.options["user"],
            password=self.options["password"],
            dbname=self.options["database"],
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        connection.set_session(readonly=True, autocommit=False)
        return connection

    @staticmethod
    def _rows(cursor, query):
        cursor.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    def read(self):
        queries = {
            "calendars": """
                SELECT id, company_id, name, hours_per_day, active,
                       two_weeks_calendar, full_time_required_hours,
                       hours_per_week, create_date, write_date
                  FROM resource_calendar ORDER BY id
            """,
            "attendances": """
                SELECT id, calendar_id, sequence, dayofweek, day_period,
                       week_type, hour_from, hour_to, duration_hours,
                       duration_based, create_date, write_date
                  FROM resource_calendar_attendance ORDER BY id
            """,
            "contract_types": """
                SELECT id, country_id, code, name, create_date, write_date
                  FROM hr_contract_type ORDER BY id
            """,
            "departments": """
                SELECT id, company_id, parent_id, manager_id, color,
                       master_department_id, name, note, active,
                       create_date, write_date
                  FROM hr_department ORDER BY id
            """,
            "departure_reasons": """
                SELECT id, sequence, country_id, name, active,
                       create_date, write_date
                  FROM hr_departure_reason ORDER BY id
            """,
            "jobs": """
                SELECT id, sequence, no_of_recruitment, recruiter_id,
                       department_id, company_id, contract_type_id, name,
                       description, requirements, active, create_date, write_date
                  FROM hr_job ORDER BY id
            """,
            "payroll_structure_types": """
                SELECT id, default_resource_calendar_id, country_id, name,
                       create_date, write_date
                  FROM hr_payroll_structure_type ORDER BY id
            """,
            "work_locations": """
                SELECT id, company_id, address_id, name, location_type,
                       location_number, active, create_date, write_date
                  FROM hr_work_location ORDER BY id
            """,
            "skill_types": """
                SELECT id, sequence, color, levels_count, name, active,
                       is_certification, create_date, write_date
                  FROM hr_skill_type ORDER BY id
            """,
            "skill_levels": """
                SELECT id, skill_type_id, level_progress, name, default_level,
                       create_date, write_date
                  FROM hr_skill_level ORDER BY id
            """,
            "skills": """
                SELECT id, sequence, skill_type_id, name, create_date, write_date
                  FROM hr_skill ORDER BY id
            """,
            "resume_line_types": """
                SELECT id, sequence, name, resume_line_type_properties_definition,
                       is_course, create_date, write_date
                  FROM hr_resume_line_type ORDER BY id
            """,
            "resources": """
                SELECT id, company_id, user_id, calendar_id, name,
                       resource_type, tz, active, time_efficiency, color,
                       hours_per_week, hours_per_day, create_date, write_date
                  FROM resource_resource ORDER BY id
            """,
            "employees": """
                SELECT id, resource_id, company_id, message_main_attachment_id,
                       current_version_id, user_id, work_contact_id,
                       country_of_birth, parent_id, coach_id, color, name,
                       work_phone, mobile_phone, work_email, legal_name,
                       private_phone, private_email, lang, place_of_birth,
                       permit_no, visa_no, certificate, study_field, study_school,
                       emergency_contact, emergency_phone, barcode, pin,
                       private_car_plate, birthday, visa_expire,
                       work_permit_expiration_date, salary_distribution,
                       employee_properties, active, birthday_public_display,
                       work_permit_scheduled_activity, contract_template_id,
                       hourly_cost, monday_location_id, tuesday_location_id,
                       wednesday_location_id, thursday_location_id,
                       friday_location_id, saturday_location_id,
                       sunday_location_id, expense_manager_id,
                       hr_employee_folder_id, hr_employee_contract_folder_id,
                       x_tese_is_linked, x_tese_payslip_count,
                       create_date, write_date
                  FROM hr_employee ORDER BY id
            """,
            "versions": """
                SELECT id, company_id, employee_id, last_modified_uid,
                       country_id, private_state_id, private_country_id,
                       distance_home_work, km_home_work, children, department_id,
                       job_id, address_id, work_location_id, resource_calendar_id,
                       contract_template_id, structure_type_id, contract_type_id,
                       hr_responsible_id, name, identification_id, passport_id,
                       sex, private_street, private_street2, private_city,
                       private_zip, distance_home_work_unit, marital,
                       spouse_complete_name, employee_type, job_title,
                       date_version, passport_expiration_date, spouse_birthdate,
                       contract_date_start, contract_date_end, trial_date_end,
                       additional_note, wage, active, is_custom_job_title,
                       is_flexible, is_fully_flexible, last_modified_date,
                       departure_id, tz, hours_per_week, hours_per_day,
                       x_tese_is_linked, create_date, write_date
                  FROM hr_version ORDER BY id
            """,
            "employee_bank_accounts": """
                SELECT employee_id, bank_account_id
                  FROM employee_bank_account_rel
                 ORDER BY employee_id, bank_account_id
            """,
            "bank_references": """
                SELECT DISTINCT bank.id, bank.partner_id, bank.company_id,
                       bank.account_number
                  FROM res_partner_bank bank
                  JOIN employee_bank_account_rel relation
                    ON relation.bank_account_id = bank.id
                 ORDER BY bank.id
            """,
            "company_calendars": """
                SELECT id, resource_calendar_id FROM res_company ORDER BY id
            """,
            "xmlids": """
                SELECT model, res_id, module || '.' || name AS xmlid
                  FROM ir_model_data
                 WHERE model IN (
                    'res.country', 'res.country.state', 'resource.calendar',
                    'hr.contract.type', 'hr.departure.reason',
                    'hr.payroll.structure.type', 'hr.resume.line.type',
                    'hr.skill', 'hr.skill.level', 'hr.skill.type'
                 )
                 ORDER BY model, res_id, module, name
            """,
        }
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            if cursor.fetchone()["transaction_read_only"] != "on":
                raise RuntimeError("HR source connection is not read-only")
            result = {
                name: self._rows(cursor, query)
                for name, query in queries.items()
            }
        result["counts"] = {
            key: len(value)
            for key, value in result.items()
            if key not in {"counts", "xmlids", "company_calendars", "bank_references"}
        }
        return result


class UslHrRestoreRun(models.Model):
    _name = "usl.hr.restore.run"
    _description = "USL HR Restoration Run"
    _order = "started_at desc, id desc"

    status = fields.Selection(
        [("running", "Running"), ("passed", "Passed"), ("failed", "Failed")],
        required=True,
        default="running",
    )
    source_database = fields.Char(required=True)
    source_snapshot = fields.Char(required=True)
    started_at = fields.Datetime(required=True, default=fields.Datetime.now)
    finished_at = fields.Datetime()
    statistics_json = fields.Json(readonly=True)

    @staticmethod
    def _text(value):
        if isinstance(value, dict):
            return value.get("en_US") or value.get("fr_FR") or next(iter(value.values()), "")
        return value or ""

    def _trace_values(self, model, source_id):
        return {
            "rebuild_source_database": self.source_database,
            "rebuild_source_model": model,
            "rebuild_source_id": source_id,
            "rebuild_source_snapshot": self.source_snapshot,
            "rebuild_import_status": "imported",
            "rebuild_import_note": (
                f"Restored by HR run {self.id}, revision {RESTORE_REVISION} "
                f"from {self.source_database}."
            ),
        }

    def _traced(self, model, source_id):
        return self.env[model].sudo().with_context(active_test=False).search(
            [
                ("rebuild_source_model", "=", model),
                ("rebuild_source_id", "=", source_id),
            ],
            limit=1,
        )

    def _upsert(self, model, row, values, natural_domain=None, xmlid=None):
        record = self._traced(model, row["id"])
        if not record and xmlid:
            candidate = self.env.ref(xmlid, raise_if_not_found=False)
            if candidate and candidate._name == model:
                record = candidate
        if not record and natural_domain:
            candidates = self.env[model].sudo().with_context(active_test=False).search(
                natural_domain,
                limit=2,
            )
            if len(candidates) == 1:
                record = candidates
        values = {**values, **self._trace_values(model, row["id"])}
        context = {
            "active_test": False,
            "lang": "en_US",
            "tracking_disable": True,
            "mail_create_nolog": True,
        }
        if record:
            record.sudo().with_context(**context).write(values)
        else:
            record = self.env[model].sudo().with_context(**context).create(values)
        return record

    @staticmethod
    def _xmlid_index(rows):
        result = {}
        for row in rows:
            result.setdefault((row["model"], row["res_id"]), []).append(row["xmlid"])
        return result

    def _source_xmlid(self, xmlids, model, source_id):
        for xmlid in xmlids.get((model, source_id), []):
            target = self.env.ref(xmlid, raise_if_not_found=False)
            if target and target._name == model:
                return xmlid
        return None

    def _reference(self, xmlids, model, source_id):
        if not source_id:
            return self.env[model]
        xmlid = self._source_xmlid(xmlids, model, source_id)
        if not xmlid:
            raise RuntimeError(f"No target {model} reference for source id {source_id}")
        return self.env.ref(xmlid)

    def _mapping(self, model, source_ids):
        records = self.env[model].sudo().with_context(active_test=False).search(
            [
                ("rebuild_source_model", "=", model),
                ("rebuild_source_id", "in", sorted(set(source_ids)) or [0]),
            ],
        )
        result = {record.rebuild_source_id: record for record in records}
        missing = sorted(set(source_ids) - set(result))
        if missing:
            raise RuntimeError(f"Missing {model} source mappings: {missing}")
        return result

    def _stamp_dates(self, model, mapping, rows):
        table = self.env[model]._table
        for row in rows:
            self.env.cr.execute(
                f"UPDATE {table} SET create_date=COALESCE(%s, create_date), "
                "write_date=COALESCE(%s, write_date) WHERE id=%s",
                (row["create_date"], row["write_date"], mapping[row["id"]].id),
            )

    def _write_french(self, record, row, field_names):
        values = {}
        for field_name in field_names:
            source_value = row.get(field_name)
            if isinstance(source_value, dict) and source_value.get("fr_FR"):
                values[field_name] = source_value["fr_FR"]
        if values:
            record.with_context(lang="fr_FR", tracking_disable=True).write(values)

    def restore(self, source):
        self.ensure_one()
        xmlids = self._xmlid_index(source["xmlids"])
        companies = self._mapping(
            "res.company",
            [row["id"] for row in source["company_calendars"]],
        )
        user_ids = {
            row[key]
            for dataset in ("resources", "employees", "versions", "jobs")
            for row in source[dataset]
            for key in (
                ("user_id",) if dataset == "resources" else
                ("user_id", "expense_manager_id") if dataset == "employees" else
                ("last_modified_uid", "hr_responsible_id") if dataset == "versions" else
                ("recruiter_id",)
            )
            if row.get(key)
        }
        users = self._mapping("res.users", user_ids)
        partner_ids = {
            row[key]
            for dataset in ("employees", "versions", "work_locations")
            for row in source[dataset]
            for key in (
                ("work_contact_id",) if dataset == "employees" else
                ("address_id",) if dataset == "versions" else
                ("address_id",)
            )
            if row.get(key)
        }
        partners = self._mapping("res.partner", partner_ids)
        banks = {}
        for row in source["bank_references"]:
            bank = self._traced("res.partner.bank", row["id"])
            if not bank:
                partner = partners[row["partner_id"]]
                company = companies.get(row["company_id"])
                candidates = self.env["res.partner.bank"].sudo().with_context(active_test=False).search(
                    [
                        ("partner_id", "=", partner.id),
                        ("company_id", "=", company.id if company else False),
                        ("account_number", "=", row["account_number"]),
                    ],
                    limit=2,
                )
                if len(candidates) != 1:
                    raise RuntimeError(
                        f"Cannot resolve source bank account {row['id']} uniquely",
                    )
                bank = candidates
            bank.write(self._trace_values("res.partner.bank", row["id"]))
            banks[row["id"]] = bank

        calendars = {}
        for row in source["calendars"]:
            company = companies.get(row["company_id"])
            calendars[row["id"]] = self._upsert(
                "resource.calendar",
                row,
                {
                    "name": row["name"],
                    "company_id": company.id if company else False,
                    "active": row["active"],
                    "two_weeks_calendar": row["two_weeks_calendar"],
                    "full_time_required_hours": row["full_time_required_hours"],
                },
                [
                    ("name", "=", row["name"]),
                    ("company_id", "=", company.id if company else False),
                ],
                self._source_xmlid(xmlids, "resource.calendar", row["id"]),
            )

        attendances = {}
        seen_attendances = {calendar.id: self.env["resource.calendar.attendance"] for calendar in calendars.values()}
        for row in source["attendances"]:
            calendar = calendars[row["calendar_id"]]
            natural_domain = [
                ("calendar_id", "=", calendar.id),
                ("dayofweek", "=", row["dayofweek"]),
                ("week_type", "=", row["week_type"] or False),
                ("hour_from", "=", row["hour_from"]),
                ("hour_to", "=", row["hour_to"]),
            ]
            attendance = self._upsert(
                "resource.calendar.attendance",
                row,
                {
                    "calendar_id": calendar.id,
                    "sequence": row["sequence"],
                    "dayofweek": row["dayofweek"],
                    "week_type": row["week_type"] or False,
                    "hour_from": row["hour_from"],
                    "hour_to": row["hour_to"],
                    "duration_hours": row["duration_hours"],
                },
                natural_domain,
            )
            attendances[row["id"]] = attendance
            seen_attendances[calendar.id] |= attendance
        for calendar in calendars.values():
            stale = calendar.attendance_ids - seen_attendances[calendar.id]
            stale.unlink()

        contract_types = {}
        for row in source["contract_types"]:
            name = self._text(row["name"])
            country = self._reference(xmlids, "res.country", row["country_id"])
            contract_types[row["id"]] = self._upsert(
                "hr.contract.type", row,
                {"name": name, "code": row["code"], "country_id": country.id},
                [("name", "=", name), ("country_id", "=", country.id)],
                self._source_xmlid(xmlids, "hr.contract.type", row["id"]),
            )
            self._write_french(contract_types[row["id"]], row, ("name",))

        departure_reasons = {}
        for row in source["departure_reasons"]:
            name = self._text(row["name"])
            country = self._reference(xmlids, "res.country", row["country_id"])
            departure_reasons[row["id"]] = self._upsert(
                "hr.departure.reason", row,
                {
                    "name": name,
                    "sequence": row["sequence"],
                    "country_id": country.id,
                    "active": row["active"],
                },
                [("name", "=", name), ("country_id", "=", country.id)],
                self._source_xmlid(xmlids, "hr.departure.reason", row["id"]),
            )
            self._write_french(departure_reasons[row["id"]], row, ("name",))

        payroll_types = {}
        for row in source["payroll_structure_types"]:
            country = self._reference(xmlids, "res.country", row["country_id"])
            payroll_types[row["id"]] = self._upsert(
                "hr.payroll.structure.type", row,
                {
                    "name": row["name"],
                    "country_id": country.id,
                    "default_resource_calendar_id": calendars[row["default_resource_calendar_id"]].id,
                },
                [("name", "=", row["name"]), ("country_id", "=", country.id)],
                self._source_xmlid(xmlids, "hr.payroll.structure.type", row["id"]),
            )

        work_locations = {}
        for row in source["work_locations"]:
            company = companies[row["company_id"]]
            address = partners.get(row["address_id"])
            work_locations[row["id"]] = self._upsert(
                "hr.work.location", row,
                {
                    "name": row["name"],
                    "company_id": company.id,
                    "address_id": address.id if address else False,
                    "location_type": row["location_type"],
                    "location_number": row["location_number"],
                    "active": row["active"],
                },
                [("name", "=", row["name"]), ("company_id", "=", company.id)],
            )

        skill_types = {}
        for row in source["skill_types"]:
            name = self._text(row["name"])
            skill_types[row["id"]] = self._upsert(
                "hr.skill.type", row,
                {
                    "name": name,
                    "sequence": row["sequence"],
                    "color": row["color"],
                    "active": row["active"],
                    "is_certification": row["is_certification"],
                },
                [("name", "=", name)],
                self._source_xmlid(xmlids, "hr.skill.type", row["id"]),
            )
            self._write_french(skill_types[row["id"]], row, ("name",))

        skill_levels = {}
        for row in source["skill_levels"]:
            skill_type = skill_types[row["skill_type_id"]]
            skill_levels[row["id"]] = self._upsert(
                "hr.skill.level", row,
                {
                    "name": row["name"],
                    "skill_type_id": skill_type.id,
                    "level_progress": row["level_progress"],
                    "default_level": row["default_level"],
                },
                [("name", "=", row["name"]), ("skill_type_id", "=", skill_type.id)],
                self._source_xmlid(xmlids, "hr.skill.level", row["id"]),
            )

        skills = {}
        for row in source["skills"]:
            name = self._text(row["name"])
            skill_type = skill_types[row["skill_type_id"]]
            skills[row["id"]] = self._upsert(
                "hr.skill", row,
                {"name": name, "sequence": row["sequence"], "skill_type_id": skill_type.id},
                [("name", "=", name), ("skill_type_id", "=", skill_type.id)],
                self._source_xmlid(xmlids, "hr.skill", row["id"]),
            )
            self._write_french(skills[row["id"]], row, ("name",))

        resume_types = {}
        for row in source["resume_line_types"]:
            name = self._text(row["name"])
            resume_types[row["id"]] = self._upsert(
                "hr.resume.line.type", row,
                {
                    "name": name,
                    "sequence": row["sequence"],
                    "resume_line_type_properties_definition": row["resume_line_type_properties_definition"],
                    "is_course": row["is_course"],
                },
                [("name", "=", name)],
                self._source_xmlid(xmlids, "hr.resume.line.type", row["id"]),
            )
            self._write_french(resume_types[row["id"]], row, ("name",))

        departments = {}
        for row in source["departments"]:
            name = self._text(row["name"])
            company = companies.get(row["company_id"])
            departments[row["id"]] = self._upsert(
                "hr.department", row,
                {
                    "name": name,
                    "company_id": company.id if company else False,
                    "color": row["color"],
                    "note": row["note"],
                    "active": row["active"],
                },
                [("name", "=", name), ("company_id", "=", company.id if company else False)],
            )
            self._write_french(departments[row["id"]], row, ("name",))
        for row in source["departments"]:
            departments[row["id"]].write(
                {
                    "parent_id": departments.get(row["parent_id"]).id if row["parent_id"] else False,
                    "master_department_id": (
                        departments[row["master_department_id"]].id
                        if row["master_department_id"] else False
                    ),
                },
            )

        jobs = {}
        for row in source["jobs"]:
            name = self._text(row["name"])
            company = companies.get(row["company_id"])
            jobs[row["id"]] = self._upsert(
                "hr.job", row,
                {
                    "name": name,
                    "sequence": row["sequence"],
                    "no_of_recruitment": row["no_of_recruitment"],
                    "recruiter_id": users.get(row["recruiter_id"]).id if row["recruiter_id"] else False,
                    "department_id": departments.get(row["department_id"]).id if row["department_id"] else False,
                    "company_id": company.id if company else False,
                    "contract_type_id": (
                        contract_types[row["contract_type_id"]].id
                        if row["contract_type_id"] else False
                    ),
                    "description": row["description"],
                    "requirements": row["requirements"],
                    "active": row["active"],
                },
                [("name", "=", name), ("company_id", "=", company.id if company else False)],
            )
            self._write_french(jobs[row["id"]], row, ("name",))

        versions = {}
        employees = {}
        resources = {}
        version_rows_by_employee = {
            row["employee_id"]: row for row in source["versions"] if row["employee_id"]
        }
        resource_rows = {row["id"]: row for row in source["resources"]}
        employee_rows = {row["id"]: row for row in source["employees"]}
        for row in source["employees"]:
            company = companies[row["company_id"]]
            user = users.get(row["user_id"])
            work_contact = partners.get(row["work_contact_id"])
            employee = self._traced("hr.employee", row["id"])
            if not employee:
                employee = self.env["hr.employee"].sudo().with_context(
                    tracking_disable=True, mail_create_nolog=True,
                ).create(
                    {
                        "name": row["name"],
                        "company_id": company.id,
                        "user_id": user.id if user else False,
                        "work_contact_id": work_contact.id if work_contact else False,
                        **self._trace_values("hr.employee", row["id"]),
                    },
                )
            else:
                employee.write(self._trace_values("hr.employee", row["id"]))
            employees[row["id"]] = employee
            resource_row = resource_rows[row["resource_id"]]
            resource = employee.resource_id
            resource.write(
                {
                    "company_id": company.id,
                    "user_id": users.get(resource_row["user_id"]).id if resource_row["user_id"] else False,
                    "name": resource_row["name"],
                    "resource_type": resource_row["resource_type"],
                    "tz": resource_row["tz"],
                    "active": resource_row["active"],
                    "time_efficiency": resource_row["time_efficiency"],
                    "color": resource_row["color"],
                    **self._trace_values("resource.resource", resource_row["id"]),
                },
            )
            resources[resource_row["id"]] = resource
            version_row = version_rows_by_employee[row["id"]]
            version = self._traced("hr.version", version_row["id"])
            if not version:
                version = employee.current_version_id or employee.version_ids[:1]
            version.write(self._trace_values("hr.version", version_row["id"]))
            versions[version_row["id"]] = version

        for row in source["versions"]:
            if row["id"] in versions:
                continue
            versions[row["id"]] = self._upsert(
                "hr.version",
                row,
                {
                    "name": row["name"],
                    "company_id": companies.get(row["company_id"]).id if row["company_id"] else False,
                    "employee_id": False,
                    "date_version": row["date_version"],
                    "last_modified_uid": users[row["last_modified_uid"]].id,
                    "last_modified_date": row["last_modified_date"],
                    "hr_responsible_id": users[row["hr_responsible_id"]].id,
                    "active": row["active"],
                },
                [
                    ("employee_id", "=", False),
                    ("company_id", "=", companies.get(row["company_id"]).id if row["company_id"] else False),
                    ("name", "=", row["name"]),
                    ("date_version", "=", row["date_version"]),
                ],
            )

        for row in source["versions"]:
            version = versions[row["id"]]
            version.write(
                {
                    "company_id": companies.get(row["company_id"]).id if row["company_id"] else False,
                    "employee_id": employees.get(row["employee_id"]).id if row["employee_id"] else False,
                    "last_modified_uid": users[row["last_modified_uid"]].id,
                    "last_modified_date": row["last_modified_date"],
                    "country_id": self._reference(xmlids, "res.country", row["country_id"]).id,
                    "private_state_id": self._reference(xmlids, "res.country.state", row["private_state_id"]).id,
                    "private_country_id": self._reference(xmlids, "res.country", row["private_country_id"]).id,
                    "distance_home_work": row["distance_home_work"],
                    "children": row["children"],
                    "department_id": departments.get(row["department_id"]).id if row["department_id"] else False,
                    "job_id": jobs.get(row["job_id"]).id if row["job_id"] else False,
                    "address_id": partners.get(row["address_id"]).id if row["address_id"] else False,
                    "work_location_id": (
                        work_locations[row["work_location_id"]].id
                        if row["work_location_id"] else False
                    ),
                    "resource_calendar_id": (
                        calendars[row["resource_calendar_id"]].id
                        if row["resource_calendar_id"] else False
                    ),
                    "contract_template_id": (
                        versions[row["contract_template_id"]].id
                        if row["contract_template_id"] else False
                    ),
                    "structure_type_id": (
                        payroll_types[row["structure_type_id"]].id
                        if row["structure_type_id"] else False
                    ),
                    "contract_type_id": (
                        contract_types[row["contract_type_id"]].id
                        if row["contract_type_id"] else False
                    ),
                    "hr_responsible_id": users[row["hr_responsible_id"]].id,
                    "name": row["name"],
                    "identification_id": row["identification_id"],
                    "passport_id": row["passport_id"],
                    "sex": row["sex"],
                    "private_street": row["private_street"],
                    "private_street2": row["private_street2"],
                    "private_city": row["private_city"],
                    "private_zip": row["private_zip"],
                    "distance_home_work_unit": row["distance_home_work_unit"],
                    "marital": row["marital"],
                    "spouse_complete_name": row["spouse_complete_name"],
                    "employee_type": row["employee_type"],
                    "job_title": row["job_title"],
                    "date_version": row["date_version"],
                    "passport_expiration_date": row["passport_expiration_date"],
                    "spouse_birthdate": row["spouse_birthdate"],
                    "contract_date_start": row["contract_date_start"],
                    "contract_date_end": row["contract_date_end"],
                    "trial_date_end": row["trial_date_end"],
                    "additional_note": row["additional_note"],
                    "wage": row["wage"],
                    "tz": row["tz"] or (
                        resource_rows[employee_rows[row["employee_id"]]["resource_id"]]["tz"]
                        if row["employee_id"] else None
                    ) or (
                        companies[row["company_id"]].partner_id.tz
                        if row["company_id"] else self.env.user.tz
                    ) or "UTC",
                    "active": row["active"],
                    "is_custom_job_title": row["is_custom_job_title"],
                    "hours_per_week": row["hours_per_week"],
                    "hours_per_day": row["hours_per_day"],
                },
            )

        employee_banks = {}
        for row in source["employee_bank_accounts"]:
            employee_banks.setdefault(row["employee_id"], []).append(banks[row["bank_account_id"]])
        day_fields = (
            "monday_location_id", "tuesday_location_id", "wednesday_location_id",
            "thursday_location_id", "friday_location_id", "saturday_location_id",
            "sunday_location_id",
        )
        for row in source["employees"]:
            employee = employees[row["id"]]
            salary_distribution = {
                str(banks[int(source_bank_id)].id): value
                for source_bank_id, value in (row["salary_distribution"] or {}).items()
            }
            employee.write(
                {
                    "company_id": companies[row["company_id"]].id,
                    "user_id": users.get(row["user_id"]).id if row["user_id"] else False,
                    "work_contact_id": partners.get(row["work_contact_id"]).id if row["work_contact_id"] else False,
                    "country_of_birth": self._reference(xmlids, "res.country", row["country_of_birth"]).id,
                    "parent_id": employees.get(row["parent_id"]).id if row["parent_id"] else False,
                    "coach_id": employees.get(row["coach_id"]).id if row["coach_id"] else False,
                    "color": row["color"],
                    "name": row["name"],
                    "work_phone": row["work_phone"],
                    "mobile_phone": row["mobile_phone"],
                    "work_email": row["work_email"],
                    "legal_name": row["legal_name"],
                    "private_phone": row["private_phone"],
                    "private_email": row["private_email"],
                    "lang": row["lang"],
                    "place_of_birth": row["place_of_birth"],
                    "permit_no": row["permit_no"],
                    "visa_no": row["visa_no"],
                    "certificate": row["certificate"],
                    "study_field": row["study_field"],
                    "study_school": row["study_school"],
                    "emergency_contact": row["emergency_contact"],
                    "emergency_phone": row["emergency_phone"],
                    "barcode": row["barcode"],
                    "pin": row["pin"],
                    "private_car_plate": row["private_car_plate"],
                    "birthday": row["birthday"],
                    "visa_expire": row["visa_expire"],
                    "work_permit_expiration_date": row["work_permit_expiration_date"],
                    "bank_account_ids": [Command.set([bank.id for bank in employee_banks.get(row["id"], [])])],
                    "salary_distribution": salary_distribution,
                    "employee_properties": row["employee_properties"],
                    "active": row["active"],
                    "birthday_public_display": row["birthday_public_display"],
                    "work_permit_scheduled_activity": row["work_permit_scheduled_activity"],
                    "contract_template_id": (
                        versions[row["contract_template_id"]].id
                        if row["contract_template_id"] else False
                    ),
                    "hourly_cost": row["hourly_cost"],
                    "expense_manager_id": (
                        users[row["expense_manager_id"]].id
                        if row["expense_manager_id"] else False
                    ),
                    **{
                        field_name: work_locations.get(row[field_name]).id if row[field_name] else False
                        for field_name in day_fields
                    },
                },
            )

        for row in source["departments"]:
            departments[row["id"]].write(
                {"manager_id": employees.get(row["manager_id"]).id if row["manager_id"] else False},
            )
        for row in source["company_calendars"]:
            companies[row["id"]].write(
                {"resource_calendar_id": calendars[row["resource_calendar_id"]].id},
            )
        # Creating a company in an earlier stage creates a target-default
        # calendar. Once the source calendar is restored, that generated row is
        # not source truth and must not remain as a duplicate schedule.
        mapped_calendar_ids = {calendar.id for calendar in calendars.values()}
        stale_calendars = self.env["resource.calendar"].sudo().with_context(
            active_test=False,
        ).search(
            [
                ("company_id", "in", [company.id for company in companies.values()]),
                ("id", "not in", sorted(mapped_calendar_ids)),
            ],
        )
        for calendar in stale_calendars:
            references = {
                "company": self.env["res.company"].sudo().search_count(
                    [("resource_calendar_id", "=", calendar.id)], limit=1,
                ),
                "resource": self.env["resource.resource"].sudo().search_count(
                    [("calendar_id", "=", calendar.id)], limit=1,
                ),
                "version": self.env["hr.version"].sudo().search_count(
                    [("resource_calendar_id", "=", calendar.id)], limit=1,
                ),
                "payroll_type": self.env["hr.payroll.structure.type"].sudo().search_count(
                    [("default_resource_calendar_id", "=", calendar.id)], limit=1,
                ),
                "leave": self.env["resource.calendar.leaves"].sudo().search_count(
                    [("calendar_id", "=", calendar.id)], limit=1,
                ),
            }
            if any(references.values()):
                raise RuntimeError(
                    f"Generated calendar {calendar.id} is still referenced: {references}",
                )
            calendar.unlink()
        for employee in employees.values():
            employee._compute_current_version_id()
        # Employee writes route inherited fields through the active version and
        # stamp the runtime editor. Restore authoritative historical attribution
        # only after every employee/resource inverse has settled.
        for row in source["versions"]:
            versions[row["id"]].write(
                {
                    "last_modified_uid": users[row["last_modified_uid"]].id,
                    "last_modified_date": row["last_modified_date"],
                    "tz": row["tz"] or (
                        resource_rows[employee_rows[row["employee_id"]]["resource_id"]]["tz"]
                        if row["employee_id"] else None
                    ) or (
                        companies[row["company_id"]].partner_id.tz
                        if row["company_id"] else self.env.user.tz
                    ) or "UTC",
                },
            )

        mappings = {
            "resource.calendar": (calendars, source["calendars"]),
            "resource.calendar.attendance": (attendances, source["attendances"]),
            "hr.contract.type": (contract_types, source["contract_types"]),
            "hr.department": (departments, source["departments"]),
            "hr.departure.reason": (departure_reasons, source["departure_reasons"]),
            "hr.job": (jobs, source["jobs"]),
            "hr.payroll.structure.type": (payroll_types, source["payroll_structure_types"]),
            "hr.work.location": (work_locations, source["work_locations"]),
            "hr.skill.type": (skill_types, source["skill_types"]),
            "hr.skill.level": (skill_levels, source["skill_levels"]),
            "hr.skill": (skills, source["skills"]),
            "hr.resume.line.type": (resume_types, source["resume_line_types"]),
            "resource.resource": (resources, source["resources"]),
            "hr.employee": (employees, source["employees"]),
            "hr.version": (versions, source["versions"]),
        }
        for model, (mapping, rows) in mappings.items():
            self._stamp_dates(model, mapping, rows)

        counts = {
            "calendars": len(calendars),
            "attendances": len(attendances),
            "contract_types": len(contract_types),
            "departments": len(departments),
            "departure_reasons": len(departure_reasons),
            "jobs": len(jobs),
            "payroll_structure_types": len(payroll_types),
            "work_locations": len(work_locations),
            "skill_types": len(skill_types),
            "skill_levels": len(skill_levels),
            "skills": len(skills),
            "resume_line_types": len(resume_types),
            "resources": len(resources),
            "employees": len(employees),
            "versions": len(versions),
            "employee_bank_accounts": sum(len(value) for value in employee_banks.values()),
        }
        if counts != source["counts"]:
            raise RuntimeError(f"HR source/target counts differ: {source['counts']} != {counts}")
        delegated = {
            "attachment_links": sum(bool(row["message_main_attachment_id"]) for row in source["employees"]),
            "documents_folders": sum(
                bool(row["hr_employee_folder_id"]) + bool(row["hr_employee_contract_folder_id"])
                for row in source["employees"]
            ),
            "studio_fields": sum(
                bool(row["x_tese_is_linked"]) + bool(row["x_tese_payslip_count"])
                for row in source["employees"]
            ) + sum(bool(row["x_tese_is_linked"]) for row in source["versions"]),
            "resource_timezones_recomputed": sum(
                resource_rows[employee_rows[row["employee_id"]]["resource_id"]]["tz"]
                != row["tz"]
                for row in source["versions"]
                if row["employee_id"]
            ),
        }
        self.write(
            {
                "status": "passed",
                "finished_at": fields.Datetime.now(),
                "statistics_json": {
                    "source": source["counts"],
                    "target": counts,
                    "delegated": delegated,
                },
            },
        )
        return counts


def source_options():
    return {
        "host": os.getenv("HR_SOURCE_DB_HOST", "accounting-source-db"),
        "port": int(os.getenv("HR_SOURCE_DB_PORT", "5432")),
        "user": os.getenv("HR_SOURCE_DB_USER", "odoo"),
        "password": os.getenv("HR_SOURCE_DB_PASSWORD", "odoo"),
        "database": os.getenv("HR_SOURCE_DATABASE", "odoo_online_source_saas_19_2"),
    }
