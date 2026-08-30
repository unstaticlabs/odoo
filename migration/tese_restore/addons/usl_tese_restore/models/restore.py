import hashlib
import json
import os
from pathlib import Path

import psycopg2
import psycopg2.extras

from odoo import Command, api, fields, models
from odoo.tools import BinaryBytes

from odoo.addons.usl_tese_payroll.models.constants import (
    TESE_COMPONENTS,
    TESE_INTERNAL_WRITE_TOKEN,
)

RESTORE_REVISION = 1
SOURCE_PROFILE_MODEL = "x_tese_payroll_profile"
SOURCE_PAYSLIP_MODEL = "x_tese_payslip"


class UslTeseRestoreRun(models.Model):
    _name = "usl.tese.restore.run"
    _description = "USL TESE Restoration Run"
    _order = "started_at desc, id desc"

    name = fields.Char(required=True, default="TESE payroll restoration")
    status = fields.Selection(
        [
            ("running", "Running"),
            ("passed", "Passed"),
            ("partial", "Partial"),
            ("failed", "Failed"),
        ],
        required=True,
        default="running",
        index=True,
    )
    source_database = fields.Char(required=True, index=True)
    source_snapshot = fields.Char(required=True, index=True)
    target_database = fields.Char(required=True, index=True)
    started_at = fields.Datetime(required=True, default=fields.Datetime.now)
    finished_at = fields.Datetime()
    employee_count = fields.Integer(readonly=True)
    version_count = fields.Integer(readonly=True)
    profile_count = fields.Integer(readonly=True)
    payslip_count = fields.Integer(readonly=True)
    payroll_pdf_count = fields.Integer(readonly=True)
    employee_pdf_count = fields.Integer(readonly=True)
    message_count = fields.Integer(readonly=True)
    tracking_count = fields.Integer(readonly=True)
    follower_count = fields.Integer(readonly=True)
    paid_count = fields.Integer(readonly=True)
    to_reconcile_count = fields.Integer(readonly=True)
    issue_count = fields.Integer(readonly=True)
    statistics_json = fields.Json(readonly=True)
    issue_ids = fields.One2many(
        "usl.tese.restore.issue",
        "run_id",
        readonly=True,
    )

    def _issue(self, severity, source_model, source_id, description):
        self.ensure_one()
        return self.env["usl.tese.restore.issue"].sudo().create({
            "run_id": self.id,
            "severity": severity,
            "source_model": source_model,
            "source_id": source_id or 0,
            "description": description,
        })

    @staticmethod
    def _text(value):
        if isinstance(value, dict):
            return (
                value.get("fr_FR")
                or value.get("en_US")
                or next(iter(value.values()), "")
            )
        return value or ""

    @staticmethod
    def _checksum(values):
        return hashlib.sha256(
            json.dumps(
                values,
                default=str,
                ensure_ascii=False,
                sort_keys=True,
            ).encode(),
        ).hexdigest()

    def _mapping(self, source_model, source_id, target_model=None):
        domain = [
            ("source_database", "=", self.source_database),
            ("source_model", "=", source_model),
            ("source_id", "=", source_id),
        ]
        if target_model:
            domain.append(("target_model", "=", target_model))
        return self.env["usl.tese.restore.mapping"].sudo().search(
            domain,
            limit=1,
        )

    def _mapped_record(self, source_model, source_id, target_model):
        mapping = self._mapping(source_model, source_id, target_model)
        if not mapping:
            return self.env[target_model]
        return self.env[target_model].sudo().browse(
            mapping.target_id,
        ).exists()

    def _bind(self, source_model, source_id, record, checksum=None):
        values = {
            "source_database": self.source_database,
            "source_model": source_model,
            "source_id": source_id,
            "target_model": record._name,
            "target_id": record.id,
            "source_checksum": checksum,
            "last_run_id": self.id,
        }
        mapping = self._mapping(source_model, source_id, record._name)
        if mapping:
            mapping.write(values)
        else:
            mapping = self.env["usl.tese.restore.mapping"].sudo().create(
                values,
            )
        return mapping

    def _traced(self, target_model, source_model, source_id):
        Model = self.env[target_model].sudo().with_context(active_test=False)
        if "rebuild_source_id" not in Model._fields:
            return Model
        domain = [
            ("rebuild_source_id", "=", source_id),
            ("rebuild_source_model", "=", source_model),
        ]
        return Model.search(domain, limit=1)

    def _traced_move(self, source_id):
        Move = self.env["account.move"].sudo()
        if "rebuild_source_id" not in Move._fields:
            return Move
        return Move.search([
            ("rebuild_source_id", "=", source_id),
            ("rebuild_source_model", "=like", "account.move%"),
        ], limit=1)

    def _target_company(self, source_id):
        company = self._traced("res.company", "res.company", source_id)
        if not company and source_id == 1:
            companies = self.env["res.company"].sudo().search([])
            if len(companies) == 1:
                company = companies
        if not company:
            self._issue(
                "error",
                "res.company",
                source_id,
                "No uniquely mapped target company is available.",
            )
        return company

    def _target_partner(self, source_id, partners, company):
        if not source_id:
            return self.env["res.partner"]
        partner = self._traced("res.partner", "res.partner", source_id)
        if partner:
            return partner
        partner = self._mapped_record(
            "res.partner",
            source_id,
            "res.partner",
        )
        if partner:
            return partner
        row = partners.get(source_id)
        if not row:
            self._issue(
                "error",
                "res.partner",
                source_id,
                "The source partner row is missing.",
            )
            return partner
        email = row.get("email")
        exact = self.env["res.partner"].sudo().search([
            ("email", "=", email),
        ], limit=2) if email else self.env["res.partner"]
        if len(exact) == 1:
            partner = exact
        else:
            partner = self.env["res.partner"].sudo().with_context(
                tracking_disable=True,
                mail_create_nolog=True,
                mail_create_nosubscribe=True,
            ).create({
                "name": self._text(row.get("name")),
                "email": email,
                "phone": row.get("phone"),
                "company_id": company.id,
            })
        self._bind("res.partner", source_id, partner, self._checksum(row))
        return partner

    def _target_user(self, source_id, users):
        if not source_id:
            return self.env["res.users"]
        row = users.get(source_id)
        if not row:
            return self.env["res.users"]
        return self.env["res.users"].sudo().with_context(
            active_test=False,
        ).search([("login", "=", row["login"])], limit=1)

    def _target_country(self, source_id, countries):
        row = countries.get(source_id)
        return (
            self.env["res.country"].sudo().search(
                [("code", "=", row["code"])],
                limit=1,
            )
            if row and row.get("code")
            else self.env["res.country"]
        )

    def _target_account(self, source_id, code, company):
        account = self._traced(
            "account.account",
            "account.account",
            source_id,
        )
        if account:
            return account
        accounts = self.env["account.account"].sudo().search([
            ("code", "=", code),
            ("company_ids", "in", company.id),
        ])
        if len(accounts) == 1:
            return accounts
        self._issue(
            "error",
            "account.account",
            source_id,
            (
                f"No unique target account is available for source "
                f"{source_id} / code {code}."
            ),
        )
        return self.env["account.account"]

    @staticmethod
    def _connection():
        connection = psycopg2.connect(
            host=os.environ.get("TESE_SOURCE_DB_HOST", "accounting-source-db"),
            port=int(os.environ.get("TESE_SOURCE_DB_PORT", "5432")),
            user=os.environ.get("TESE_SOURCE_DB_USER", "odoo"),
            password=os.environ.get("TESE_SOURCE_DB_PASSWORD", "odoo"),
            dbname=os.environ.get(
                "TESE_SOURCE_DATABASE",
                "odoo_online_source_saas_19_3",
            ),
        )
        connection.set_session(readonly=True, autocommit=False)
        return connection

    @staticmethod
    def _fetch(cursor, query, parameters=None):
        cursor.execute(query, parameters or ())
        return [dict(row) for row in cursor.fetchall()]

    @api.model
    def _load_source_payload(self):
        with self._connection() as connection, connection.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor,
        ) as cursor:
            employees = self._fetch(cursor, """
                SELECT *
                  FROM hr_employee
                 ORDER BY id
            """)
            versions = self._fetch(cursor, """
                SELECT *
                  FROM hr_version
                 ORDER BY id
            """)
            employee_types = self._fetch(cursor, """
                SELECT id, country_id, code, name, sequence
                  FROM hr_employee_type
                 ORDER BY id
            """)
            profiles = self._fetch(cursor, """
                SELECT *
                  FROM x_tese_payroll_profile
                 ORDER BY id
            """)
            payslips = self._fetch(cursor, """
                SELECT payslip.*,
                       document.attachment_id AS source_attachment_id,
                       payroll_move.state AS source_move_state
                  FROM x_tese_payslip payslip
             LEFT JOIN documents_document document
                    ON document.id = payslip.x_document_id
             LEFT JOIN account_move payroll_move
                    ON payroll_move.id = payslip.x_move_id
                 ORDER BY payslip.x_pay_year, payslip.x_pay_month, payslip.id
            """)
            partners = self._fetch(cursor, """
                SELECT id, name, email, phone
                  FROM res_partner
                 WHERE id IN (
                    SELECT work_contact_id FROM hr_employee
                    UNION
                    SELECT x_employee_partner_id
                      FROM x_tese_payroll_profile
                    UNION
                    SELECT x_tese_collector_partner_id
                      FROM x_tese_payroll_profile
                    UNION
                    SELECT address_id
                      FROM hr_version
                    UNION
                    SELECT author_id
                      FROM mail_message
                     WHERE model IN ('hr.employee', 'hr.version')
                 )
            """)
            users = self._fetch(cursor, """
                SELECT id, login, partner_id
                  FROM res_users
                 WHERE id IN (
                    SELECT user_id FROM hr_employee
                    UNION SELECT create_uid FROM hr_employee
                    UNION SELECT write_uid FROM hr_employee
                    UNION SELECT create_uid FROM hr_version
                    UNION SELECT write_uid FROM hr_version
                 )
            """)
            countries = self._fetch(cursor, """
                SELECT id, code
                  FROM res_country
                 WHERE id IN (
                    SELECT country_of_birth FROM hr_employee
                    UNION SELECT country_id FROM hr_version
                    UNION SELECT private_country_id FROM hr_version
                 )
            """)
            employee_documents = self._fetch(cursor, """
                SELECT attachment.*,
                       document.id AS source_document_id,
                       employee.id AS source_employee_id
                  FROM hr_employee employee
                  JOIN documents_document document
                    ON document.folder_id = employee.hr_employee_folder_id
                  JOIN ir_attachment attachment
                    ON attachment.id = document.attachment_id
                 WHERE attachment.mimetype = 'application/pdf'
                 ORDER BY employee.id, attachment.id
            """)
            employee_images = self._fetch(cursor, """
                SELECT attachment.*, employee.id AS source_employee_id
                  FROM hr_employee employee
                  JOIN ir_attachment attachment
                    ON attachment.res_model = 'hr.employee'
                   AND attachment.res_id = employee.id
                   AND attachment.res_field = 'image_1920'
                 ORDER BY employee.id, attachment.id
            """)
            messages = self._fetch(cursor, """
                SELECT message.*
                  FROM mail_message message
                 WHERE (
                       message.model = 'hr.employee'
                   AND message.res_id IN (SELECT id FROM hr_employee)
                 ) OR (
                       message.model = 'hr.version'
                   AND message.res_id IN (SELECT id FROM hr_version)
                 )
                 ORDER BY message.id
            """)
            tracking = self._fetch(cursor, """
                SELECT tracking.*, field.model AS source_field_model,
                       field.name AS source_field_name
                  FROM mail_tracking_value tracking
                  JOIN mail_message message
                    ON message.id = tracking.mail_message_id
             LEFT JOIN ir_model_fields field
                    ON field.id = tracking.field_id
                 WHERE (
                       message.model = 'hr.employee'
                   AND message.res_id IN (SELECT id FROM hr_employee)
                 ) OR (
                       message.model = 'hr.version'
                   AND message.res_id IN (SELECT id FROM hr_version)
                 )
                 ORDER BY tracking.id
            """)
            followers = self._fetch(cursor, """
                SELECT follower.*
                  FROM mail_followers follower
                 WHERE (
                       follower.res_model = 'hr.employee'
                   AND follower.res_id IN (SELECT id FROM hr_employee)
                 ) OR (
                       follower.res_model = 'hr.version'
                   AND follower.res_id IN (SELECT id FROM hr_version)
                 )
                 ORDER BY follower.id
            """)
            follower_subtypes = self._fetch(cursor, """
                SELECT relation.mail_followers_id AS follower_id,
                       data.module || '.' || data.name AS subtype_xmlid
                  FROM mail_followers_mail_message_subtype_rel relation
                  JOIN ir_model_data data
                    ON data.model = 'mail.message.subtype'
                   AND data.res_id = relation.mail_message_subtype_id
                 WHERE relation.mail_followers_id IN (
                    SELECT id
                      FROM mail_followers
                     WHERE res_model IN ('hr.employee', 'hr.version')
                 )
                 ORDER BY relation.mail_followers_id, subtype_xmlid
            """)
            subtype_xmlids = self._fetch(cursor, """
                SELECT data.res_id,
                       data.module || '.' || data.name AS xmlid
                  FROM ir_model_data data
                 WHERE data.model = 'mail.message.subtype'
            """)
        return {
            "employees": employees,
            "versions": versions,
            "employee_types": employee_types,
            "profiles": profiles,
            "payslips": payslips,
            "partners": partners,
            "users": users,
            "countries": countries,
            "employee_documents": employee_documents,
            "employee_images": employee_images,
            "messages": messages,
            "tracking": tracking,
            "followers": followers,
            "follower_subtypes": follower_subtypes,
            "subtype_xmlids": subtype_xmlids,
        }

    def _attachment_binary(self, row):
        if row.get("db_datas"):
            value = row["db_datas"]
            return bytes(value)
        store_name = row.get("store_fname")
        if not store_name:
            return False
        source_root = Path(os.environ.get(
            "TESE_SOURCE_FILESTORE",
            "/mnt/accounting-source/filestore",
        ))
        path = source_root / store_name
        if not path.is_file():
            self._issue(
                "error",
                "ir.attachment",
                row["id"],
                f"Source filestore object is missing: {store_name}.",
            )
            return False
        return path.read_bytes()

    def _restore_employees(self, payload):
        partners = {row["id"]: row for row in payload["partners"]}
        users = {row["id"]: row for row in payload["users"]}
        countries = {row["id"]: row for row in payload["countries"]}
        versions = {row["id"]: row for row in payload["versions"]}
        employee_types = {}
        for row in payload["employee_types"]:
            name = self._text(row.get("name"))
            employee_types[row["id"]] = (
                self.env["hr.employee.type"]
                .sudo()
                .with_context(active_test=False)
                .search(
                    [
                        "|",
                        ("code", "=", row.get("code")),
                        ("name", "=", name),
                    ],
                    limit=1,
                )
            )
        employees = {}
        version_records = {}
        Employee = self.env["hr.employee"].sudo().with_context(
            tracking_disable=True,
            mail_create_nolog=True,
            mail_create_nosubscribe=True,
        )
        for row in payload["employees"]:
            company = self._target_company(row["company_id"])
            if not company:
                continue
            partner = self._target_partner(
                row.get("work_contact_id"),
                partners,
                company,
            )
            user = self._target_user(row.get("user_id"), users)
            employee = self._mapped_record(
                "hr.employee",
                row["id"],
                "hr.employee",
            )
            if not employee:
                employee = self._traced(
                    "hr.employee",
                    "hr.employee",
                    row["id"],
                )
            values = {
                "name": self._text(row.get("name")),
                "company_id": company.id,
                "user_id": user.id,
                "work_contact_id": partner.id,
                "work_email": row.get("work_email"),
                "work_phone": row.get("work_phone"),
                "mobile_phone": row.get("mobile_phone"),
                "legal_name": row.get("legal_name"),
                "active": row.get("active", True),
            }
            if employee:
                employee.write(values)
            else:
                employee = Employee.create(values)
            self._bind(
                "hr.employee",
                row["id"],
                employee,
                self._checksum(row),
            )
            employees[row["id"]] = employee

            source_version = versions.get(row.get("current_version_id"))
            if source_version:
                target_version = employee.version_id
                self._bind(
                    "hr.version",
                    source_version["id"],
                    target_version,
                )
                version_records[source_version["id"]] = target_version

        Version = self.env["hr.version"].sudo().with_context(
            tracking_disable=True,
            mail_create_nolog=True,
            mail_create_nosubscribe=True,
        )
        for row in payload["versions"]:
            version = version_records.get(row["id"]) or self._mapped_record(
                "hr.version",
                row["id"],
                "hr.version",
            )
            employee = employees.get(row.get("employee_id"))
            company = self._target_company(row["company_id"])
            country = self._target_country(row.get("country_id"), countries)
            private_country = self._target_country(
                row.get("private_country_id"),
                countries,
            )
            address = self._target_partner(
                row.get("address_id"),
                partners,
                company,
            )
            responsible = self._target_user(row.get("hr_responsible_id"), users)
            values = {
                "company_id": company.id,
                "employee_id": employee.id if employee else False,
                "name": row.get("name"),
                "date_version": row["date_version"],
                "contract_date_start": row.get("contract_date_start"),
                "contract_date_end": row.get("contract_date_end"),
                "trial_date_end": row.get("trial_date_end"),
                "wage": row.get("wage") or 0,
                "hours_per_week": row.get("hours_per_week") or 0,
                "hours_per_day": row.get("hours_per_day") or 0,
                "job_title": row.get("job_title"),
                "employee_type_id": employee_types.get(
                    row.get("employee_type_id"),
                    self.env["hr.employee.type"],
                ).id,
                "country_id": country.id,
                "private_country_id": private_country.id,
                "address_id": address.id,
                "hr_responsible_id": responsible.id or self.env.user.id,
                "identification_id": row.get("identification_id"),
                "sex": row.get("sex"),
                "private_street": row.get("private_street"),
                "private_street2": row.get("private_street2"),
                "private_city": row.get("private_city"),
                "private_zip": row.get("private_zip"),
                "marital": row.get("marital") or "single",
                "spouse_complete_name": row.get("spouse_complete_name"),
                "spouse_birthdate": row.get("spouse_birthdate"),
                "children": row.get("children") or 0,
                "additional_note": row.get("additional_note"),
                "tz": row.get("tz") or "Europe/Paris",
                "active": row.get("active", True),
            }
            if version:
                version.write(values)
            else:
                version = Version.create(values)
            self._bind(
                "hr.version",
                row["id"],
                version,
                self._checksum(row),
            )
            version_records[row["id"]] = version

        for row in payload["employees"]:
            employee = employees.get(row["id"])
            if not employee:
                continue
            country = self._target_country(
                row.get("country_of_birth"),
                countries,
            )
            employee.write({
                "country_of_birth": country.id,
                "birthday": row.get("birthday"),
                "place_of_birth": row.get("place_of_birth"),
                "certificate": row.get("certificate"),
                "study_field": row.get("study_field"),
                "emergency_contact": row.get("emergency_contact"),
                "emergency_phone": row.get("emergency_phone"),
                "private_phone": row.get("private_phone"),
                "private_email": row.get("private_email"),
            })
        return employees, version_records

    def _restore_images(self, payload, employees):
        count = 0
        for row in payload["employee_images"]:
            employee = employees.get(row["source_employee_id"])
            binary = self._attachment_binary(row)
            if employee and binary:
                employee.with_context(tracking_disable=True).write({
                    "image_1920": BinaryBytes(binary),
                })
                count += 1
        return count

    def _restore_employee_documents(self, payload, employees):
        attachments = {}
        for row in payload["employee_documents"]:
            employee = employees.get(row["source_employee_id"])
            if not employee:
                continue
            attachment = self._traced(
                "ir.attachment",
                "ir.attachment",
                row["id"],
            )
            if not attachment:
                attachment = self._mapped_record(
                    "ir.attachment",
                    row["id"],
                    "ir.attachment",
                )
            values = {
                "name": self._text(row.get("name")),
                "type": row.get("type") or "binary",
                "mimetype": row.get("mimetype"),
            }
            source_res_model = row.get("res_model")
            if source_res_model == "account.move":
                target_move = self._traced_move(row.get("res_id"))
                if not target_move:
                    self._issue(
                        "error",
                        "account.move",
                        row.get("res_id"),
                        (
                            f"Payroll PDF {row['id']} cannot be linked because "
                            "its accounting entry is missing."
                        ),
                    )
                    continue
                values.update({
                    "res_model": "account.move",
                    "res_id": target_move.id,
                })
            else:
                values.update({
                    "res_model": "hr.employee",
                    "res_id": employee.id,
                })
            binary = self._attachment_binary(row)
            if not attachment:
                if not binary:
                    continue
                values["raw"] = binary
                attachment = (
                    self.env["ir.attachment"]
                    .sudo()
                    .with_context(usl_documents_skip_attachment_queue=True)
                    .create(values)
                )
            else:
                attachment.sudo().with_context(
                    usl_documents_skip_attachment_queue=True,
                ).write(values)
            if row.get("create_date"):
                self.env.cr.execute(
                    """
                    UPDATE ir_attachment
                       SET create_date = %s,
                           write_date = COALESCE(%s, %s)
                     WHERE id = %s
                    """,
                    [
                        row["create_date"],
                        row.get("write_date"),
                        row["create_date"],
                        attachment.id,
                    ],
                )
                attachment.invalidate_recordset(["create_date", "write_date"])
            self._bind(
                "ir.attachment",
                row["id"],
                attachment,
                row.get("checksum"),
            )
            attachments[row["id"]] = attachment
        return attachments

    def _profile_component_values(self, row, company):
        commands = []
        accounts = {}
        for sequence, component in enumerate(TESE_COMPONENTS, start=1):
            code = component["code"]
            account = self._target_account(
                row.get(f"x_account_{code}_id"),
                code,
                company,
            )
            if not account:
                continue
            accounts[code] = account
            commands.append(Command.create({
                **component,
                "sequence": sequence * 10,
                "account_id": account.id,
                "amount": row.get(f"x_amount_{code}") or 0,
            }))
        return commands, accounts

    def _restore_profiles(
        self,
        payload,
        employees,
        versions,
        partners,
    ):
        profiles = {}
        for row in payload["profiles"]:
            company = self._target_company(row["x_company_id"])
            employee = employees.get(row.get("x_employee_id"))
            if not company or not employee:
                self._issue(
                    "error",
                    SOURCE_PROFILE_MODEL,
                    row["id"],
                    "The profile company or employee could not be mapped.",
                )
                continue
            collector = self._target_partner(
                row.get("x_tese_collector_partner_id"),
                partners,
                company,
            )
            version = versions.get(row.get("x_hr_version_id"))
            commands, _accounts = self._profile_component_values(row, company)
            if len(commands) != len(TESE_COMPONENTS):
                continue
            values = {
                "name": self._text(row.get("x_name")),
                "active": row.get("x_active", True),
                "company_id": company.id,
                "employee_id": employee.id,
                "hr_version_id": version.id if version else False,
                "collector_partner_id": collector.id,
                "valid_from": row.get("x_valid_from"),
                "valid_to": row.get("x_valid_to"),
                "default_hours": row.get("x_default_hours") or 0,
                "gross_salary": row.get("x_gross_salary") or 0,
                "employee_contribution_total": (
                    row.get("x_employee_contrib_total") or 0
                ),
                "employer_contribution_total": (
                    row.get("x_employer_contrib_total") or 0
                ),
                "net_social": row.get("x_net_social") or 0,
                "net_before_tax": row.get("x_net_before_tax") or 0,
                "income_tax_base": row.get("x_income_tax_base") or 0,
                "income_tax_rate": row.get("x_income_tax_rate") or 0,
                "income_tax_amount": row.get("x_income_tax_amount") or 0,
                "net_paid": row.get("x_net_paid") or 0,
                "review_status": (
                    row.get("x_review_status")
                    if row.get("x_review_status") in {
                        "to_review",
                        "ok",
                        "warning",
                        "archived",
                    }
                    else "to_review"
                ),
                "review_message": row.get("x_review_message"),
                "last_used_date": row.get("x_last_used_date"),
            }
            checksum = self._checksum(row)
            profile = self._mapped_record(
                SOURCE_PROFILE_MODEL,
                row["id"],
                "usl.tese.profile",
            )
            mapping = self._mapping(
                SOURCE_PROFILE_MODEL,
                row["id"],
                "usl.tese.profile",
            )
            if profile and mapping.source_checksum == checksum:
                profiles[row["id"]] = profile
                continue
            Profile = self.env["usl.tese.profile"].sudo().with_context(
                _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
                tracking_disable=True,
                mail_create_nolog=True,
                mail_create_nosubscribe=True,
            )
            if profile:
                profile.with_context(
                    _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
                ).write(values)
                lines_by_code = {
                    line.code: line for line in profile.component_line_ids
                }
                for command in commands:
                    line_values = command[2]
                    line = lines_by_code.get(line_values["code"])
                    if line:
                        line.with_context(
                            _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
                        ).write(line_values)
                    else:
                        line_values["profile_id"] = profile.id
                        self.env["usl.tese.profile.line"].sudo().with_context(
                            _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
                        ).create(line_values)
            else:
                values["component_line_ids"] = commands
                profile = Profile.create(values)
            self._bind(
                SOURCE_PROFILE_MODEL,
                row["id"],
                profile,
                checksum,
            )
            profiles[row["id"]] = profile
        return profiles

    def _payslip_component_commands(self, row, company, profile):
        commands = []
        profile_lines = {
            line.code: line for line in profile.component_line_ids
        }
        for sequence, component in enumerate(TESE_COMPONENTS, start=1):
            code = component["code"]
            account = self._target_account(
                row.get(f"x_account_{code}_id"),
                code,
                company,
            )
            if not account:
                continue
            commands.append(Command.create({
                **component,
                "sequence": sequence * 10,
                "account_id": account.id,
                "amount": row.get(f"x_amount_{code}") or 0,
                "profile_line_id": profile_lines.get(code).id,
            }))
        return commands

    def _finalize_payslip_links(
        self,
        payslip,
        move,
        attachment,
        company,
        collector,
    ):
        move.sudo().write({
            "tese_payslip_id": payslip.id,
            "tese_move_role": "payroll",
            "tese_attachment_id": attachment.id if attachment else False,
        })
        if not company.tese_payroll_journal_id:
            company.sudo().write({
                "tese_payroll_journal_id": move.journal_id.id,
            })
        if not company.tese_collector_partner_id and collector:
            company.sudo().write({
                "tese_collector_partner_id": collector.id,
            })
        if move.state == "posted":
            # Payment residuals may have changed since a previous rehearsal.
            # Re-derive the operational state even when source data is unchanged.
            payslip.action_finalize()
            payslip.with_context(
                _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
            ).write({
                "preparation_ok": True,
                "preparation_message": (
                    "Preparation complete: the payroll entry is balanced and "
                    "posted, and the official TESE PDF is linked."
                ),
            })
        else:
            message = (
                "The draft payroll entry and source payroll record were "
                "restored. Attach the official provider PDF before posting."
            )
            payslip.with_context(
                _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
            ).write({
                "state": "to_post",
                "preparation_message": message,
                "bank_reconcile_message": message,
            })

    def _restore_payslips(
        self,
        payload,
        employees,
        versions,
        profiles,
        partners,
        attachments,
    ):
        payslips = {}
        for row in payload["payslips"]:
            company = self._target_company(row["x_company_id"])
            employee = employees.get(row.get("x_employee_id"))
            profile = profiles.get(row.get("x_profile_id"))
            move = self._traced_move(row.get("x_move_id"))
            if not all((company, employee, profile, move)):
                self._issue(
                    "error",
                    SOURCE_PAYSLIP_MODEL,
                    row["id"],
                    (
                        "The payroll company, employee, profile, or posted "
                        "accounting entry could not be mapped."
                    ),
                )
                continue
            attachment = attachments.get(row.get("source_attachment_id"))
            if not attachment:
                attachment = self._traced(
                    "ir.attachment",
                    "ir.attachment",
                    row.get("source_attachment_id"),
                )
            if not attachment:
                source_is_pre_posting = (
                    row.get("source_move_state") == "draft"
                    and row.get("x_document_status") == "missing"
                )
                if not source_is_pre_posting:
                    self._issue(
                        "error",
                        SOURCE_PAYSLIP_MODEL,
                        row["id"],
                        "The provider payroll PDF could not be mapped.",
                    )
                    continue
            collector = self._target_partner(
                row.get("x_tese_collector_partner_id"),
                partners,
                company,
            )
            version = versions.get(row.get("x_hr_version_id"))
            component_commands = self._payslip_component_commands(
                row,
                company,
                profile,
            )
            if len(component_commands) != len(TESE_COMPONENTS):
                continue
            values = {
                "name": self._text(row.get("x_name")),
                "state": "to_reconcile",
                "company_id": company.id,
                "profile_id": profile.id,
                "employee_id": employee.id,
                "hr_version_id": version.id if version else False,
                "collector_partner_id": collector.id,
                "pay_period": row["x_period_start"],
                "period_start": row.get("x_period_start"),
                "period_end": row.get("x_period_end"),
                "payment_date": row.get("x_payment_date"),
                "payslip_date": row.get("x_payslip_date"),
                "tese_payment_date": row.get("x_tese_payment_date"),
                "tese_reference": (
                    row.get("x_tese_reference")
                    or f"legacy-tese-{row['id']}"
                ),
                "hours": row.get("x_hours") or 0,
                "attachment_id": attachment.id if attachment else False,
                "document_note": row.get("x_document_note"),
                "gross_salary": row.get("x_gross_salary") or 0,
                "employee_contribution_total": (
                    row.get("x_employee_contrib_total") or 0
                ),
                "employer_contribution_total": (
                    row.get("x_employer_contrib_total") or 0
                ),
                "net_social": row.get("x_net_social") or 0,
                "net_before_tax": row.get("x_net_before_tax") or 0,
                "income_tax_base": row.get("x_income_tax_base") or 0,
                "income_tax_rate": row.get("x_income_tax_rate") or 0,
                "income_tax_amount": row.get("x_income_tax_amount") or 0,
                "net_paid": row.get("x_net_paid") or 0,
                "component_line_ids": component_commands,
                "move_id": move.id,
                "move_ref": row.get("x_move_ref") or move.ref,
                "total_debit": row.get("x_total_debit") or 0,
                "total_credit": row.get("x_total_credit") or 0,
                "balance_difference": row.get("x_balance_diff") or 0,
                "preparation_ok": row.get("x_check_ok", True),
                "preparation_message": row.get("x_check_message"),
                "control_checklist": row.get("x_control_checklist"),
                "preparation_warnings": row.get("x_preparation_warnings"),
                "tese_contribution_total": (
                    row.get("x_tese_contrib_total") or 0
                ),
                "tese_income_tax_total": (
                    row.get("x_tese_income_tax_total") or 0
                ),
                "tese_detailed_total": (
                    row.get("x_tese_detailed_total") or 0
                ),
                "tese_bank_amount": row.get("x_tese_bank_amount") or 0,
                "tese_bank_difference": row.get("x_tese_bank_diff") or 0,
                "profile_snapshot_label": (
                    row.get("x_profile_snapshot_label")
                    or profile.display_name
                ),
                "profile_snapshot_text": row.get("x_profile_snapshot_text"),
                "employee_snapshot_name": (
                    row.get("x_employee_snapshot_name")
                    or employee.name
                ),
                "employee_partner_snapshot_id": (
                    employee.work_contact_id.id
                ),
                "hr_wage_snapshot": row.get("x_hr_wage_snapshot") or 0,
                "hr_hours_snapshot": row.get("x_hr_hours_snapshot") or 0,
                "profile_valid_from_snapshot": (
                    row.get("x_profile_valid_from_snapshot")
                ),
                "profile_valid_to_snapshot": (
                    row.get("x_profile_valid_to_snapshot")
                ),
            }
            checksum = self._checksum(row)
            payslip = self._mapped_record(
                SOURCE_PAYSLIP_MODEL,
                row["id"],
                "usl.tese.payslip",
            )
            mapping = self._mapping(
                SOURCE_PAYSLIP_MODEL,
                row["id"],
                "usl.tese.payslip",
            )
            Payslip = self.env["usl.tese.payslip"].sudo().with_context(
                _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
                tracking_disable=True,
                mail_create_nolog=True,
                mail_create_nosubscribe=True,
            )
            if payslip and mapping.source_checksum == checksum:
                self._finalize_payslip_links(
                    payslip,
                    move,
                    attachment,
                    company,
                    collector,
                )
                payslips[row["id"]] = payslip
                continue
            if payslip:
                values.pop("component_line_ids")
                payslip.with_context(
                    _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
                ).write(values)
            else:
                payslip = Payslip.create(values)
            self._finalize_payslip_links(
                payslip,
                move,
                attachment,
                company,
                collector,
            )
            self._bind(
                SOURCE_PAYSLIP_MODEL,
                row["id"],
                payslip,
                checksum,
            )
            payslips[row["id"]] = payslip
        return payslips

    def _subtype(self, source_id, subtype_xmlids):
        xmlid = subtype_xmlids.get(source_id)
        if not xmlid:
            return self.env["mail.message.subtype"]
        return self.env.ref(xmlid, raise_if_not_found=False)

    def _restore_messages(
        self,
        payload,
        employees,
        versions,
        partners,
    ):
        subtype_xmlids = {
            row["res_id"]: row["xmlid"]
            for row in payload["subtype_xmlids"]
        }
        source_records = {
            "hr.employee": employees,
            "hr.version": versions,
        }
        restored = {}
        for row in payload["messages"]:
            target = source_records.get(row["model"], {}).get(row["res_id"])
            if not target:
                self._issue(
                    "error",
                    "mail.message",
                    row["id"],
                    "The chatter message business record could not be mapped.",
                )
                continue
            message = self._mapped_record(
                "mail.message",
                row["id"],
                "mail.message",
            )
            author = partners.get(row.get("author_id"))
            subtype = self._subtype(
                row.get("subtype_id"),
                subtype_xmlids,
            )
            parent = restored.get(row.get("parent_id")) or self._mapped_record(
                "mail.message",
                row.get("parent_id"),
                "mail.message",
            )
            values = {
                "model": target._name,
                "res_id": target.id,
                "subject": self._text(row.get("subject")),
                "body": self._text(row.get("body")),
                "message_type": row.get("message_type") or "comment",
                "email_from": row.get("email_from"),
                "author_id": author.id if author else False,
                "subtype_id": subtype.id if subtype else False,
                "parent_id": parent.id if parent else False,
                "date": row.get("date"),
                "message_id": row.get("message_id"),
                "reply_to": row.get("reply_to"),
            }
            Message = self.env["mail.message"].sudo().with_context(
                tracking_disable=True,
                mail_create_nolog=True,
                mail_create_nosubscribe=True,
            )
            if message:
                message.write(values)
            else:
                message = Message.create(values)
            self._bind(
                "mail.message",
                row["id"],
                message,
                self._checksum(row),
            )
            restored[row["id"]] = message
        return restored

    def _restore_tracking(self, payload, messages):
        restored = {}
        Tracking = self.env["mail.tracking.value"].sudo()
        for row in payload["tracking"]:
            message = messages.get(row["mail_message_id"])
            if not message:
                continue
            field = (
                self.env["ir.model.fields"].sudo().search([
                    ("model", "=", row["source_field_model"]),
                    ("name", "=", row["source_field_name"]),
                ], limit=1)
                if row.get("source_field_model")
                and row.get("source_field_name")
                else self.env["ir.model.fields"]
            )
            if row.get("field_id") and not field:
                self._issue(
                    "warning",
                    "mail.tracking.value",
                    row["id"],
                    (
                        f"Tracking field {row['source_field_model']}."
                        f"{row['source_field_name']} no longer exists."
                    ),
                )
                continue
            tracking = self._mapped_record(
                "mail.tracking.value",
                row["id"],
                "mail.tracking.value",
            )
            values = {
                "mail_message_id": message.id,
                "field_id": field.id if field else False,
                "field_info": row.get("field_info"),
                "old_value_integer": row.get("old_value_integer"),
                "new_value_integer": row.get("new_value_integer"),
                "old_value_char": row.get("old_value_char"),
                "new_value_char": row.get("new_value_char"),
                "old_value_text": row.get("old_value_text"),
                "new_value_text": row.get("new_value_text"),
                "old_value_datetime": row.get("old_value_datetime"),
                "new_value_datetime": row.get("new_value_datetime"),
                "old_value_float": row.get("old_value_float"),
                "new_value_float": row.get("new_value_float"),
            }
            if tracking:
                tracking.write(values)
            else:
                tracking = Tracking.create(values)
            self._bind(
                "mail.tracking.value",
                row["id"],
                tracking,
                self._checksum(row),
            )
            restored[row["id"]] = tracking
        return restored

    def _restore_followers(
        self,
        payload,
        employees,
        versions,
        partners,
    ):
        source_records = {
            "hr.employee": employees,
            "hr.version": versions,
        }
        subtype_by_follower = {}
        for row in payload["follower_subtypes"]:
            subtype = self.env.ref(
                row["subtype_xmlid"],
                raise_if_not_found=False,
            )
            if subtype:
                subtype_by_follower.setdefault(
                    row["follower_id"],
                    [],
                ).append(subtype.id)
        restored = {}
        for row in payload["followers"]:
            target = source_records.get(
                row["res_model"],
                {},
            ).get(row["res_id"])
            partner = partners.get(row.get("partner_id"))
            if not target or not partner:
                self._issue(
                    "warning",
                    "mail.followers",
                    row["id"],
                    "The follower record or partner could not be mapped.",
                )
                continue
            follower = self._mapped_record(
                "mail.followers",
                row["id"],
                "mail.followers",
            )
            values = {
                "res_model": target._name,
                "res_id": target.id,
                "partner_id": partner.id,
                "subtype_ids": [Command.set(
                    subtype_by_follower.get(row["id"], []),
                )],
            }
            if follower:
                follower.write(values)
            else:
                existing = self.env["mail.followers"].sudo().search([
                    ("res_model", "=", target._name),
                    ("res_id", "=", target.id),
                    ("partner_id", "=", partner.id),
                ], limit=1)
                follower = (
                    existing
                    or self.env["mail.followers"].sudo().create(values)
                )
            self._bind(
                "mail.followers",
                row["id"],
                follower,
                self._checksum(row),
            )
            restored[row["id"]] = follower
        return restored

    def _restore_audit_dates(self, payload):
        table_by_source_model = {
            "hr.employee": ("hr_employee", "hr.employee"),
            "hr.version": ("hr_version", "hr.version"),
            "mail.message": ("mail_message", "mail.message"),
            "mail.tracking.value": (
                "mail_tracking_value",
                "mail.tracking.value",
            ),
        }
        rows_by_source_model = {
            "hr.employee": payload["employees"],
            "hr.version": payload["versions"],
            "mail.message": payload["messages"],
            "mail.tracking.value": payload["tracking"],
        }
        for source_model, (table, target_model) in table_by_source_model.items():
            for row in rows_by_source_model[source_model]:
                record = self._mapped_record(
                    source_model,
                    row["id"],
                    target_model,
                )
                if not record:
                    continue
                self.env.cr.execute(
                    f"""
                        UPDATE {table}
                           SET create_date = COALESCE(%s, create_date),
                               write_date = COALESCE(%s, write_date)
                         WHERE id = %s
                    """,
                    (
                        row.get("create_date"),
                        row.get("write_date"),
                        record.id,
                    ),
                )

    def _statistics(
        self,
        employees,
        versions,
        profiles,
        payslips,
        attachments,
        messages,
        tracking,
        followers,
    ):
        payslip_records = self.env["usl.tese.payslip"].sudo().browse(
            [record.id for record in payslips.values()],
        )
        return {
            "employees": len(employees),
            "versions": len(versions),
            "profiles": len(profiles),
            "payslips": len(payslips),
            "payroll_moves": len(payslip_records.mapped("move_id")),
            "payroll_pdfs": len(payslip_records.mapped("attachment_id")),
            "employee_pdfs": len(attachments),
            "messages": len(messages),
            "tracking_values": len(tracking),
            "followers": len(followers),
            "paid": len(payslip_records.filtered(lambda item: item.state == "paid")),
            "to_reconcile": len(
                payslip_records.filtered(
                    lambda item: item.state == "to_reconcile",
                ),
            ),
            "to_post": len(
                payslip_records.filtered(lambda item: item.state == "to_post"),
            ),
        }

    def action_restore(self, payload=None):
        self.ensure_one()
        payload = payload or self._load_source_payload()
        partners = {
            row["id"]: self._target_partner(
                row["id"],
                {item["id"]: item for item in payload["partners"]},
                self._target_company(1),
            )
            for row in payload["partners"]
        }
        employees, versions = self._restore_employees(payload)
        self._restore_images(payload, employees)
        attachments = self._restore_employee_documents(payload, employees)
        profiles = self._restore_profiles(
            payload,
            employees,
            versions,
            {row["id"]: row for row in payload["partners"]},
        )
        payslips = self._restore_payslips(
            payload,
            employees,
            versions,
            profiles,
            {row["id"]: row for row in payload["partners"]},
            attachments,
        )
        messages = self._restore_messages(
            payload,
            employees,
            versions,
            partners,
        )
        tracking = self._restore_tracking(payload, messages)
        followers = self._restore_followers(
            payload,
            employees,
            versions,
            partners,
        )
        self._restore_audit_dates(payload)
        statistics = self._statistics(
            employees,
            versions,
            profiles,
            payslips,
            attachments,
            messages,
            tracking,
            followers,
        )
        blocking = self.issue_ids.filtered(lambda issue: issue.severity == "error")
        values = {
            "status": "partial" if blocking else "passed",
            "finished_at": fields.Datetime.now(),
            "employee_count": statistics["employees"],
            "version_count": statistics["versions"],
            "profile_count": statistics["profiles"],
            "payslip_count": statistics["payslips"],
            "payroll_pdf_count": statistics["payroll_pdfs"],
            "employee_pdf_count": statistics["employee_pdfs"],
            "message_count": statistics["messages"],
            "tracking_count": statistics["tracking_values"],
            "follower_count": statistics["followers"],
            "paid_count": statistics["paid"],
            "to_reconcile_count": statistics["to_reconcile"],
            "issue_count": len(self.issue_ids),
            "statistics_json": statistics,
        }
        self.sudo().write(values)
        return statistics


class UslTeseRestoreMapping(models.Model):
    _name = "usl.tese.restore.mapping"
    _description = "USL TESE Temporary Source Mapping"
    _order = "source_model, source_id, target_model"

    source_database = fields.Char(required=True, index=True)
    source_model = fields.Char(required=True, index=True)
    source_id = fields.Integer(required=True, index=True)
    target_model = fields.Char(required=True, index=True)
    target_id = fields.Integer(required=True, index=True)
    source_checksum = fields.Char(index=True)
    last_run_id = fields.Many2one(
        "usl.tese.restore.run",
        required=True,
        ondelete="cascade",
    )

    _source_target_unique = models.Constraint(
        "UNIQUE(source_database, source_model, source_id, target_model)",
        "A source TESE record can only map to one target record.",
    )


class UslTeseRestoreIssue(models.Model):
    _name = "usl.tese.restore.issue"
    _description = "USL TESE Restoration Issue"
    _order = "severity, source_model, source_id, id"

    run_id = fields.Many2one(
        "usl.tese.restore.run",
        required=True,
        ondelete="cascade",
        index=True,
    )
    severity = fields.Selection(
        [
            ("warning", "Warning"),
            ("error", "Error"),
        ],
        required=True,
        index=True,
    )
    source_model = fields.Char(required=True, index=True)
    source_id = fields.Integer(index=True)
    description = fields.Text(required=True)
