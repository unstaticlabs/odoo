import os

import psycopg2
import psycopg2.extras

from odoo import Command, fields, models


RESTORE_REVISION = 1


class IdentitySourceReader:
    """Read only the identity perimeter from the protected Online restore."""

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
    def _rows(cursor, query, parameters=None):
        cursor.execute(query, parameters or {})
        return [dict(row) for row in cursor.fetchall()]

    def read(self):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            if cursor.fetchone()["transaction_read_only"] != "on":
                raise RuntimeError("Identity source connection is not read-only")
            result = {
                "companies": self._rows(
                    cursor,
                    "SELECT id, partner_id, parent_id, active, sequence "
                    "FROM res_company ORDER BY id",
                ),
                "industries": self._rows(
                    cursor,
                    "SELECT id, name, active, create_date, write_date "
                    "FROM res_partner_industry ORDER BY id",
                ),
                "categories": self._rows(
                    cursor,
                    "SELECT id, name, color, parent_id, active, create_date, write_date "
                    "FROM res_partner_category ORDER BY id",
                ),
                "partners": self._rows(
                    cursor,
                    """
                    SELECT id, company_id, parent_id, user_id, state_id, country_id,
                           industry_id, color, name, ref, lang, tz, vat,
                           company_registry, website, function, type, street,
                           street2, zip, city, email, phone, comment,
                           partner_latitude, partner_longitude, active, employee,
                           is_company, partner_share, message_bounce,
                           supplier_rank, customer_rank, create_date, write_date
                      FROM res_partner
                     ORDER BY id
                    """,
                ),
                "partner_categories": self._rows(
                    cursor,
                    "SELECT partner_id, category_id "
                    "FROM res_partner_res_partner_category_rel "
                    "ORDER BY partner_id, category_id",
                ),
                "banks": self._rows(
                    cursor,
                    """
                    SELECT id, partner_id, sequence, company_id, account_number,
                           clearing_number, holder_name, note, active,
                           allow_out_payment, bank_name, street, street2, zip,
                           city, state_id, country_id, bank_bic,
                           create_date, write_date
                      FROM res_partner_bank
                     ORDER BY id
                    """,
                ),
                "users": self._rows(
                    cursor,
                    """
                    SELECT id, company_id, partner_id, active, login, signature,
                           share, notification_type, create_date, write_date
                      FROM res_users
                     ORDER BY id
                    """,
                ),
                "user_companies": self._rows(
                    cursor,
                    "SELECT user_id, cid AS company_id FROM res_company_users_rel "
                    "ORDER BY user_id, cid",
                ),
                "user_groups": self._rows(
                    cursor,
                    """
                    SELECT relation.uid AS user_id, relation.gid AS group_id,
                           COALESCE((
                               SELECT data.module || '.' || data.name
                                 FROM ir_model_data data
                                WHERE data.model = 'res.groups'
                                  AND data.res_id = relation.gid
                                ORDER BY data.module, data.name
                                LIMIT 1
                           ), '') AS xmlid
                      FROM res_groups_users_rel relation
                     ORDER BY relation.uid, relation.gid
                    """,
                ),
                "xmlids": self._rows(
                    cursor,
                    """
                    SELECT model, res_id, module || '.' || name AS xmlid
                      FROM ir_model_data
                     WHERE model IN (
                         'res.company', 'res.country', 'res.country.state',
                         'res.partner', 'res.users'
                     )
                     ORDER BY model, res_id, module, name
                    """,
                ),
            }
        result["counts"] = {
            key: len(value)
            for key, value in result.items()
            if key not in {"counts", "xmlids"}
        }
        return result


class UslIdentityRestoreRun(models.Model):
    _name = "usl.identity.restore.run"
    _description = "USL Identity Restoration Run"
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

    def _trace_values(self, model, source_id):
        return {
            "rebuild_source_database": self.source_database,
            "rebuild_source_model": model,
            "rebuild_source_id": source_id,
            "rebuild_source_snapshot": self.source_snapshot,
            "rebuild_import_status": "imported",
            "rebuild_import_note": (
                f"Restored by Identity run {self.id}, revision "
                f"{RESTORE_REVISION} from {self.source_database}."
            ),
        }

    def _traced(self, model, source_id):
        return (
            self.env[model]
            .sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("rebuild_source_model", "=", model),
                    ("rebuild_source_id", "=", source_id),
                ],
                limit=1,
            )
        )

    @staticmethod
    def _text(value):
        if isinstance(value, dict):
            return value.get("en_US") or value.get("fr_FR") or next(iter(value.values()), "")
        return value or ""

    def _upsert(self, model, row, values):
        record = self._traced(model, row["id"])
        if not record:
            natural_domains = {
                "res.partner.category": [("name", "=", values.get("name"))],
                "res.partner.industry": [("name", "=", values.get("name"))],
                "res.partner.bank": [
                    ("partner_id", "=", values.get("partner_id")),
                    ("account_number", "=", values.get("account_number")),
                ],
            }
            domain = natural_domains.get(model)
            if domain:
                candidates = (
                    self.env[model]
                    .sudo()
                    .with_context(active_test=False)
                    .search(domain, limit=2)
                )
                if len(candidates) == 1:
                    record = candidates
        values = {**values, **self._trace_values(model, row["id"])}
        target = self.env[model].sudo().with_context(
            active_test=False,
            install_mode=True,
            tracking_disable=True,
            mail_create_nolog=True,
            mail_create_nosubscribe=True,
        )
        if record:
            record.with_context(install_mode=True).write(values)
        else:
            record = target.create(values)
        return record

    def _claim_trace(self, record, model, source_id):
        """Make one target record canonical for a source identity.

        Earlier scoped stages may have reused a native placeholder before the
        target user identity existed. Clear only the stale provenance; never
        delete either business record or move business relationships here.
        """
        duplicates = (
            self.env[model]
            .sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("rebuild_source_model", "=", model),
                    ("rebuild_source_id", "=", source_id),
                    ("id", "!=", record.id),
                ],
            )
        )
        if duplicates:
            duplicates.write(
                {
                    "rebuild_source_database": False,
                    "rebuild_source_model": False,
                    "rebuild_source_id": False,
                    "rebuild_source_snapshot": False,
                    "rebuild_import_status": False,
                    "rebuild_import_note": False,
                },
            )

    def _audit_dates(self, model, records_by_source, rows):
        table = self.env[model]._table
        for row in rows:
            record = records_by_source[row["id"]]
            self.env.cr.execute(
                f"UPDATE {table} SET create_date=COALESCE(%s, create_date), "
                "write_date=COALESCE(%s, write_date) WHERE id=%s",
                (row.get("create_date"), row.get("write_date"), record.id),
            )

    def _xmlid_target(self, xmlids, model, source_id):
        for xmlid in xmlids.get((model, source_id), []):
            target = self.env.ref(xmlid, raise_if_not_found=False)
            if target and target._name == model:
                return target
        return self.env[model]

    def restore(self, source):
        self.ensure_one()
        xmlids = {}
        for row in source["xmlids"]:
            xmlids.setdefault((row["model"], row["res_id"]), []).append(row["xmlid"])

        companies = {
            record.rebuild_source_id: record
            for record in self.env["res.company"].sudo().with_context(active_test=False).search(
                [("rebuild_source_model", "=", "res.company")],
            )
        }
        missing_companies = {row["id"] for row in source["companies"]} - set(companies)
        if missing_companies:
            raise RuntimeError(f"Accounting must restore source companies first: {sorted(missing_companies)}")

        industries = {}
        for row in source["industries"]:
            industries[row["id"]] = self._upsert(
                "res.partner.industry",
                row,
                {"name": self._text(row["name"]), "active": row["active"]},
            )

        categories = {}
        for row in source["categories"]:
            categories[row["id"]] = self._upsert(
                "res.partner.category",
                row,
                {
                    "name": self._text(row["name"]),
                    "color": row["color"],
                    "active": row["active"],
                },
            )
        for row in source["categories"]:
            categories[row["id"]].write(
                {"parent_id": categories.get(row["parent_id"]).id if row["parent_id"] else False},
            )

        resolved_users = {}
        native_users = set()
        native_runtime_xmlids = {
            "base.user_root",
            "base.public_user",
            "base.template_portal_user_id",
        }
        for row in source["users"]:
            source_xmlids = set(xmlids.get(("res.users", row["id"]), []))
            target_login = row["login"]
            if "base.user_admin" in source_xmlids:
                target_login = os.getenv(
                    "IDENTITY_MANAGER_TARGET_LOGIN",
                    "valentin",
                )
            user = self.env["res.users"].sudo().with_context(active_test=False).search(
                [("login", "=", target_login)], limit=1,
            )
            if source_xmlids & native_runtime_xmlids:
                native_users.add(row["id"])
            if not user:
                user = self._xmlid_target(xmlids, "res.users", row["id"])
            if user:
                resolved_users[row["id"]] = user

        countries = {
            row["country_id"]: self._xmlid_target(
                xmlids,
                "res.country",
                row["country_id"],
            )
            for row in source["partners"] + source["banks"]
            if row.get("country_id")
        }
        states = {
            row["state_id"]: self._xmlid_target(xmlids, "res.country.state", row["state_id"])
            for row in source["partners"] + source["banks"]
            if row.get("state_id")
        }

        partners = {}
        partner_to_user = {row["partner_id"]: row["id"] for row in source["users"]}
        native_partner_ids = {
            row["partner_id"]
            for row in source["users"]
            if row["id"] in native_users
        }
        partner_fields = (
            "name", "color", "ref", "vat", "company_registry", "website",
            "function", "type", "street", "street2", "zip", "city", "email",
            "phone", "comment", "partner_latitude", "partner_longitude", "active",
            "employee", "supplier_rank", "customer_rank", "message_bounce",
        )
        valid_languages = {
            item[0] for item in self.env["res.lang"].sudo().get_installed()
        }
        valid_timezones = {
            item[0]
            for item in self.env["res.partner"]
            ._fields["tz"]
            ._description_selection(self.env)
        }
        for row in source["partners"]:
            target_user = resolved_users.get(partner_to_user.get(row["id"]))
            partner = target_user.partner_id if target_user else self._traced("res.partner", row["id"])
            values = {field_name: row.get(field_name) for field_name in partner_fields}
            values.update(
                {
                    "company_id": companies.get(row["company_id"]).id if row["company_id"] else False,
                    "country_id": countries.get(row["country_id"]).id if countries.get(row["country_id"]) else False,
                    "state_id": states.get(row["state_id"]).id if states.get(row["state_id"]) else False,
                    "industry_id": industries.get(row["industry_id"]).id if row["industry_id"] else False,
                    "lang": row["lang"] if row["lang"] in valid_languages else False,
                    "tz": row["tz"] if row["tz"] in valid_timezones else False,
                    **self._trace_values("res.partner", row["id"]),
                },
            )
            if row["id"] in native_partner_ids:
                values = self._trace_values("res.partner", row["id"])
            if partner:
                self._claim_trace(partner, "res.partner", row["id"])
                partner.sudo().with_context(tracking_disable=True).write(values)
            else:
                partner = self.env["res.partner"].sudo().with_context(tracking_disable=True).create(values)
            partners[row["id"]] = partner

        for row in source["partners"]:
            partners[row["id"]].sudo().with_context(tracking_disable=True).write(
                {
                    "parent_id": partners.get(row["parent_id"]).id if row["parent_id"] else False,
                },
            )

        user_companies = {}
        for row in source["user_companies"]:
            user_companies.setdefault(row["user_id"], []).append(row["company_id"])
        users = {}
        for row in source["users"]:
            user = resolved_users.get(row["id"]) or self._traced("res.users", row["id"])
            allowed_companies = [
                companies[source_id].id
                for source_id in user_companies.get(row["id"], [])
            ]
            values = {**self._trace_values("res.users", row["id"])}
            if row["id"] not in native_users:
                values.update(
                    {
                        "partner_id": partners[row["partner_id"]].id,
                        "login": (
                            user.login
                            if "base.user_admin" in set(
                                xmlids.get(("res.users", row["id"]), []),
                            )
                            else row["login"]
                        ),
                        "active": row["active"],
                        "signature": row["signature"],
                        "company_id": companies[row["company_id"]].id,
                        "company_ids": [
                            Command.set(
                                allowed_companies
                                or [companies[row["company_id"]].id],
                            ),
                        ],
                    },
                )
                if row["share"]:
                    values["group_ids"] = [
                        Command.set([self.env.ref("base.group_portal").id]),
                    ]
            notification_types = dict(
                self.env["res.users"]
                ._fields["notification_type"]
                ._description_selection(self.env),
            )
            if (
                row["id"] not in native_users
                and row.get("notification_type") in notification_types
            ):
                values["notification_type"] = row["notification_type"]
            if user:
                self._claim_trace(user, "res.users", row["id"])
                user.sudo().with_context(no_reset_password=True).write(values)
            else:
                user = self.env["res.users"].sudo().with_context(no_reset_password=True).create(values)
            users[row["id"]] = user

        group_equivalents = {
            "accountant.group_account_user": "account.group_account_user",
            "documents.group_documents_manager": (
                "usl_documents.group_documents_manager"
            ),
            "documents.group_documents_system": (
                "usl_documents.group_documents_manager"
            ),
        }
        mapped_user_groups = []
        runtime_user_groups = []
        deferred_user_groups = []
        runtime_group_xmlids = {
            "base.group_everyone",
            "base.group_portal",
            "base.group_public",
            "base.group_user",
        }
        for row in source["user_groups"]:
            if row["xmlid"] in runtime_group_xmlids:
                runtime_user_groups.append(
                    (row["user_id"], row["group_id"], row["xmlid"]),
                )
                continue
            target_xmlid = group_equivalents.get(row["xmlid"], row["xmlid"])
            group = self.env.ref(target_xmlid, raise_if_not_found=False)
            if group and group._name == "res.groups":
                users[row["user_id"]].sudo().write(
                    {"group_ids": [Command.link(group.id)]},
                )
                mapped_user_groups.append(
                    (row["user_id"], row["group_id"], target_xmlid),
                )
            else:
                deferred_user_groups.append(
                    (row["user_id"], row["group_id"], row["xmlid"]),
                )

        categories_by_partner = {}
        for row in source["partner_categories"]:
            categories_by_partner.setdefault(row["partner_id"], []).append(categories[row["category_id"]].id)
        source_partner_rows = {row["id"]: row for row in source["partners"]}
        for source_id, partner in partners.items():
            source_user_id = source_partner_rows[source_id]["user_id"]
            partner.sudo().with_context(tracking_disable=True).write(
                {
                    "category_id": [Command.set(categories_by_partner.get(source_id, []))],
                    "user_id": users[source_user_id].id
                    if source_user_id in users
                    else False,
                },
            )

        banks = {}
        for row in source["banks"]:
            banks[row["id"]] = self._upsert(
                "res.partner.bank",
                row,
                {
                    "partner_id": partners[row["partner_id"]].id,
                    "company_id": companies.get(row["company_id"]).id if row["company_id"] else False,
                    "sequence": row["sequence"],
                    "account_number": row["account_number"],
                    "clearing_number": row["clearing_number"],
                    "holder_name": row["holder_name"],
                    "note": row["note"],
                    "active": row["active"],
                    "allow_out_payment": row["allow_out_payment"],
                    "bank_name": row["bank_name"],
                    "bank_bic": row["bank_bic"],
                    "street": row["street"],
                    "street2": row["street2"],
                    "zip": row["zip"],
                    "city": row["city"],
                    "country_id": countries.get(row["country_id"]).id if countries.get(row["country_id"]) else False,
                    "state_id": states.get(row["state_id"]).id if states.get(row["state_id"]) else False,
                },
            )

        self._audit_dates("res.partner.industry", industries, source["industries"])
        self._audit_dates("res.partner.category", categories, source["categories"])
        self._audit_dates(
            "res.partner",
            partners,
            [
                row
                for row in source["partners"]
                if row["id"] not in native_partner_ids
            ],
        )
        self._audit_dates(
            "res.users",
            users,
            [row for row in source["users"] if row["id"] not in native_users],
        )
        self._audit_dates("res.partner.bank", banks, source["banks"])

        counts = {
            "companies": len(companies),
            "industries": len(industries),
            "categories": len(categories),
            "partners": len(partners),
            "partner_categories": sum(len(value) for value in categories_by_partner.values()),
            "banks": len(banks),
            "users": len(users),
            "user_companies": sum(len(value) for value in user_companies.values()),
            "user_groups": (
                len(mapped_user_groups)
                + len(runtime_user_groups)
                + len(deferred_user_groups)
            ),
        }
        if counts != source["counts"]:
            raise RuntimeError(f"Identity source/target counts differ: {source['counts']} != {counts}")
        self.write(
            {
                "status": "passed",
                "finished_at": fields.Datetime.now(),
                "statistics_json": {
                    "source": source["counts"],
                    "target": counts,
                    "mapped_user_groups": len(mapped_user_groups),
                    "recomputed_runtime_user_groups": len(runtime_user_groups),
                    "deferred_user_group_xmlids": sorted(
                        {
                            xmlid or f"source-group:{group_id}"
                            for _user_id, group_id, xmlid in deferred_user_groups
                        },
                    ),
                },
            },
        )
        return counts


def source_options():
    return {
        "host": os.getenv("IDENTITY_SOURCE_DB_HOST", "accounting-source-db"),
        "port": int(os.getenv("IDENTITY_SOURCE_DB_PORT", "5432")),
        "user": os.getenv("IDENTITY_SOURCE_DB_USER", "odoo"),
        "password": os.getenv("IDENTITY_SOURCE_DB_PASSWORD", "odoo"),
        "database": os.getenv("IDENTITY_SOURCE_DATABASE", "odoo_online_source_saas_19_2"),
    }
