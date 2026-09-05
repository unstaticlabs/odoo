import ast
import hashlib
import os
from pathlib import Path

import psycopg2
import psycopg2.extras

from odoo import Command, fields, models
from odoo.tools import BinaryBytes

RESTORE_REVISION = 2
SOURCE_FILESTORE = Path(
    os.getenv("IDENTITY_SOURCE_FILESTORE", "/mnt/accounting-source/filestore"),
).resolve()

NATIVE_FILTER_IDS = frozenset({1, 2, 3, 4, 5, 20})
MIGRATED_FILTER_IDS = frozenset({6, 7, 9, 14, 15, 17, 19})
DROPPED_SALES_MARKETING_FILTER_IDS = frozenset({10, 12, 16, 18})
EXPECTED_FILTER_IDS = (
    NATIVE_FILTER_IDS
    | MIGRATED_FILTER_IDS
    | DROPPED_SALES_MARKETING_FILTER_IDS
)
NATIVE_EXPORT_IDS = frozenset({1, 2})
DROPPED_SALES_MARKETING_EXPORT_IDS = frozenset({3, 4})
DROPPED_AI_EXPORT_IDS = frozenset({7, 8, 9, 10, 11, 12, 13})
EXPECTED_EXPORT_IDS = (
    NATIVE_EXPORT_IDS
    | DROPPED_SALES_MARKETING_EXPORT_IDS
    | DROPPED_AI_EXPORT_IDS
)
HOME_FILTER_IDS = (14, 6)
HOME_PROJECT_LIMIT = 4
HOME_PROJECT_PRIORITIES = (
    ("usl admin",),
    ("sbfh admin", "sbfh prod"),
    ("sbfh vault",),
    ("gbc ops",),
)
HOME_LAYOUT = {
    "version": 1,
    "order": [
        "activities",
        "my_tasks",
        "favorites",
        "ai_pipelines",
        "accounting",
    ],
    "hidden": [],
}


class _SafeDomainSymbol:
    """Represent one whitelisted Odoo domain symbol without evaluating code."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


def parse_saved_filter_domain(source):
    """Parse literal domains plus Odoo's dynamic ``uid`` symbol safely.

    Saved filters are evaluated by Odoo when a user opens the associated
    action.  The Online source contains one legitimate dynamic filter using
    ``uid``.  ``ast.literal_eval`` cannot represent that symbol, while
    ``safe_eval`` would execute source-controlled expressions during the
    migration.  This deliberately tiny AST interpreter keeps the supported
    data shape and rejects calls, attributes, operators and every other name.
    """

    def convert(node):
        if isinstance(node, ast.Expression):
            return convert(node.body)
        if isinstance(node, ast.List):
            return [convert(item) for item in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(convert(item) for item in node.elts)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name) and node.id == "uid":
            return _SafeDomainSymbol("uid")
        raise ValueError(f"unsupported saved-filter syntax: {type(node).__name__}")

    return convert(ast.parse(source or "[]", mode="eval"))


def source_binary(row):
    path = (SOURCE_FILESTORE / row["store_fname"]).resolve()
    if SOURCE_FILESTORE not in path.parents or not path.is_file():
        raise RuntimeError(f"Identity source attachment {row['id']} is missing or unsafe")
    content = path.read_bytes()
    if len(content) != row["file_size"]:
        raise RuntimeError(f"Identity source attachment {row['id']} size changed")
    checksum = hashlib.sha1(content, usedforsecurity=False).hexdigest()
    if checksum != row["checksum"]:
        raise RuntimeError(f"Identity source attachment {row['id']} checksum changed")
    return content


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
                message = "Identity source connection is not read-only"
                raise RuntimeError(message)
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
                "images": self._rows(
                    cursor,
                    """
                    SELECT id, res_id, store_fname, checksum, file_size, mimetype
                      FROM ir_attachment
                     WHERE res_model = 'res.partner'
                       AND res_field = 'image_1920'
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
                         'ir.actions.act_window',
                         'res.company', 'res.country', 'res.country.state',
                         'res.partner', 'res.users'
                     )
                     ORDER BY model, res_id, module, name
                    """,
                ),
                "filters": self._rows(
                    cursor,
                    """
                    SELECT id, action_id, embedded_action_id,
                           embedded_parent_res_id, create_uid, write_uid, name,
                           sort, model_id, domain, context, is_default, active,
                           create_date, write_date
                      FROM ir_filters
                     ORDER BY id
                    """,
                ),
                "filter_users": self._rows(
                    cursor,
                    """
                    SELECT ir_filters_id AS filter_id,
                           res_users_id AS user_id
                      FROM ir_filters_res_users_rel
                     ORDER BY ir_filters_id, res_users_id
                    """,
                ),
                "exports": self._rows(
                    cursor,
                    """
                    SELECT id, create_uid, write_uid, name, resource,
                           create_date, write_date
                      FROM ir_exports
                     ORDER BY id
                    """,
                ),
                "export_lines": self._rows(
                    cursor,
                    """
                    SELECT id, export_id, create_uid, write_uid, name,
                           create_date, write_date
                      FROM ir_exports_line
                     ORDER BY export_id, id
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

    def _create_restored_user(self, values):
        """Create a source user without retaining target onboarding todos."""
        task_model = (
            self.env["project.task"]
            if "project.task" in self.env.registry
            else False
        )
        task_ids_before = (
            set(task_model.sudo().search([]).ids)
            if task_model is not False
            else set()
        )
        user = (
            self.env["res.users"]
            .sudo()
            .with_context(no_reset_password=True)
            .create(values)
        )
        if task_model is not False:
            task_model.sudo().search(
                [("id", "not in", list(task_ids_before) or [0])],
            ).unlink()
        return user

    @staticmethod
    def _company_partner_targets(source_companies, companies):
        """Bind each source company partner to the native target company partner."""
        targets = {}
        for row in source_companies:
            source_partner_id = row.get("partner_id")
            if not source_partner_id:
                raise RuntimeError(
                    f"Source company {row['id']} has no partner identity",
                )
            target_partner = companies[row["id"]].partner_id
            existing = targets.get(source_partner_id)
            if existing and existing != target_partner:
                raise RuntimeError(
                    "Source company partner identity is shared by multiple "
                    f"target companies: {source_partner_id}",
                )
            targets[source_partner_id] = target_partner
        return targets

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
            mail_auto_subscribe_no_notify=True,
        )
        if record:
            record.with_context(
                install_mode=True,
                tracking_disable=True,
                mail_create_nolog=True,
                mail_create_nosubscribe=True,
                mail_auto_subscribe_no_notify=True,
            ).write(values)
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

    def _preference_target(self, model, source_id):
        record = self._traced(model, source_id)
        if not record:
            raise RuntimeError(
                f"Saved-filter source reference {model} {source_id} has no "
                "canonical rebuilt target",
            )
        return record.id

    def _translate_filter_domain(self, row):
        try:
            domain = parse_saved_filter_domain(row["domain"])
        except (SyntaxError, ValueError) as error:
            raise RuntimeError(
                f"Saved filter {row['id']} has an invalid source domain",
            ) from error
        field_models = {
            ("account.move", "journal_id"): "account.journal",
            ("account.move.line", "account_id"): "account.account",
            ("account.move.line", "partner_id"): "res.partner",
            ("project.task", "stage_id"): "project.task.type",
            ("project.task", "tag_ids"): "project.tags",
        }

        def translate(term):
            if not isinstance(term, (list, tuple)) or len(term) < 3:
                return term
            source_model = field_models.get((row["model_id"], term[0]))
            if not source_model or term[1] not in {"=", "!=", "in", "not in"}:
                return term
            source_values = term[2] if isinstance(term[2], (list, tuple)) else [term[2]]
            target_values = [
                self._preference_target(source_model, int(source_id))
                for source_id in source_values
            ]
            target_value = target_values if isinstance(term[2], (list, tuple)) else target_values[0]
            return (term[0], term[1], target_value, *term[3:])

        return [translate(term) for term in domain]

    @staticmethod
    def _literal_value(value, expected_type, label):
        try:
            parsed = ast.literal_eval(value or repr(expected_type()))
        except (SyntaxError, ValueError) as error:
            raise RuntimeError(f"Invalid {label} while building Home") from error
        if not isinstance(parsed, expected_type):
            raise TypeError(f"Invalid {label} while building Home")
        return parsed

    def _home_project_sort_key(self, project):
        names = {
            (project.with_context(lang=lang).name or "").strip().casefold()
            for lang in ("en_US", "fr_FR")
        }
        for priority, aliases in enumerate(HOME_PROJECT_PRIORITIES):
            if names.intersection(aliases):
                return priority, project.sequence, project.id
        return len(HOME_PROJECT_PRIORITIES), project.sequence, project.id

    def _home_view_values(self, favorite_filter):
        action = favorite_filter.action_id.sudo().exists()
        if not action or action.type != "ir.actions.act_window":
            raise RuntimeError(
                f"Home saved view {favorite_filter.name} has no window action",
            )
        window_action = self.env[action.type].sudo().browse(action.id).exists()
        action_xmlid = action.get_external_id().get(action.id)
        return {
            "name": favorite_filter.name,
            "target_type": "view",
            "action_id": action.id,
            "action_xmlid": action_xmlid,
            "filter_id": favorite_filter.id,
            "res_model": favorite_filter.model_id,
            "view_mode": window_action.view_mode,
            "domain_json": self._literal_value(
                favorite_filter.domain,
                list,
                f"domain for saved filter {favorite_filter.name}",
            ),
            "context_json": self._literal_value(
                favorite_filter.context,
                dict,
                f"context for saved filter {favorite_filter.name}",
            ),
            "order_by_json": self._literal_value(
                favorite_filter.sort,
                list,
                f"ordering for saved filter {favorite_filter.name}",
            ),
        }

    def _home_project_values(self, project):
        action = project.action_view_tasks()
        action_record = self.env.ref(
            "project.act_project_project_2_project_task_all",
        )
        context = action.get("context") or {}
        if isinstance(context, str):
            context = self._literal_value(
                context,
                dict,
                f"context for project {project.name}",
            )
        return {
            "name": project.name,
            "target_type": "view",
            "action_id": action_record.id,
            "action_xmlid": "project.act_project_project_2_project_task_all",
            "res_model": "project.task",
            "view_mode": action.get("view_mode") or action_record.view_mode,
            "domain_json": [
                ("project_id", "=", project.id),
                ("has_template_ancestor", "=", False),
            ],
            "context_json": context,
            "company_id": project.company_id.id,
        }

    def _restore_valentin_home(self, source, users, filters_by_source):
        if "usl.home.favorite" not in self.env.registry:
            message = "The usl_home product module must be installed before preferences"
            raise RuntimeError(message)
        manager_source_ids = [
            row["res_id"]
            for row in source["xmlids"]
            if row["model"] == "res.users" and row["xmlid"] == "base.user_admin"
        ]
        if len(manager_source_ids) != 1 or manager_source_ids[0] not in users:
            message = "The Online administrator cannot be resolved for Home"
            raise RuntimeError(message)
        valentin = users[manager_source_ids[0]]
        if valentin.login != os.getenv("IDENTITY_MANAGER_TARGET_LOGIN", "valentin"):
            message = "The Online administrator is not mapped to Valentin"
            raise RuntimeError(message)

        favorites = self.env["usl.home.favorite"].sudo()
        favorites.search([("user_id", "=", valentin.id)]).unlink()
        values_list = []

        service = self.env["usl.home.service"].with_user(valentin)
        available_widgets = set(service._available_widgets())
        if "my_tasks" in available_widgets:
            values_list.append({
                "name": "My Tasks",
                "target_type": "provider",
                "provider_key": "my_tasks",
            })

        favorite_projects = self.env["project.project"].with_user(valentin).search([
            ("active", "=", True),
            ("favorite_user_ids", "in", valentin.id),
        ])
        selected_projects = sorted(
            favorite_projects,
            key=self._home_project_sort_key,
        )[:HOME_PROJECT_LIMIT]
        values_list.extend(
            self._home_project_values(project)
            for project in selected_projects
        )

        if "ai_pipelines" in available_widgets:
            values_list.append({
                "name": "AI Pipelines",
                "target_type": "provider",
                "provider_key": "ai_pipelines",
            })
        if "accounting" in available_widgets:
            values_list.append({
                "name": "Accounting Hygiene",
                "target_type": "provider",
                "provider_key": "accounting_hygiene",
            })
        for source_id in HOME_FILTER_IDS:
            favorite_filter = filters_by_source.get(source_id)
            if not favorite_filter:
                raise RuntimeError(f"Home source saved filter {source_id} was not restored")
            values_list.append(self._home_view_values(favorite_filter))

        for sequence, values in enumerate(values_list, start=1):
            favorites.create({
                **values,
                "user_id": valentin.id,
                "sequence": sequence * 10,
            })
        settings = self.env["res.users.settings"].sudo()._find_or_create_for_user(
            valentin,
        )
        settings.write({
            "usl_home_layout": HOME_LAYOUT,
            "usl_home_favorites_initialized": True,
        })
        valentin.sudo().write({
            "action_id": self.env.ref("usl_home.action_usl_home").id,
        })
        return {
            "user_login": valentin.login,
            "favorite_count": len(values_list),
            "favorite_names": [values["name"] for values in values_list],
            "project_ids": [project.id for project in selected_projects],
            "saved_filter_source_ids": list(HOME_FILTER_IDS),
            "layout": HOME_LAYOUT,
        }

    def _restore_preferences(self, source, users):
        actual_filter_ids = {row["id"] for row in source["filters"]}
        if actual_filter_ids != EXPECTED_FILTER_IDS:
            raise RuntimeError(
                "The locked saved-filter perimeter changed: "
                f"{sorted(actual_filter_ids)}",
            )
        actual_export_ids = {row["id"] for row in source["exports"]}
        if actual_export_ids != EXPECTED_EXPORT_IDS:
            raise RuntimeError(
                "The locked saved-export perimeter changed: "
                f"{sorted(actual_export_ids)}",
            )
        filter_users = {}
        for relation in source["filter_users"]:
            filter_users.setdefault(relation["filter_id"], []).append(
                users[relation["user_id"]].id,
            )
        action_xmlids = {
            row["res_id"]: row["xmlid"]
            for row in source["xmlids"]
            if row["model"] == "ir.actions.act_window"
        }
        migrated = []
        filters_by_source = {}
        for row in source["filters"]:
            if row["id"] not in MIGRATED_FILTER_IDS:
                continue
            action_xmlid = action_xmlids.get(row["action_id"])
            action = (
                self.env.ref(action_xmlid, raise_if_not_found=False)
                if action_xmlid
                else self.env["ir.actions.actions"]
            )
            if row["id"] == 14:
                action = self.env.ref(
                    "rebuild_account_migration.action_rebuild_account_reconcile_bank_transactions",
                )
            if row["action_id"] and not action:
                raise RuntimeError(
                    f"Saved filter {row['id']} has no equivalent target action",
                )
            users_ids = sorted(filter_users.get(row["id"], []))
            values = {
                "name": row["name"],
                "model_id": row["model_id"],
                "domain": repr(self._translate_filter_domain(row)),
                "context": row["context"] or "{}",
                "sort": row["sort"] or "[]",
                "is_default": bool(row["is_default"]),
                "active": bool(row["active"]),
                "action_id": action.id if action else False,
                "user_ids": [Command.set(users_ids)],
            }
            candidates = self.env["ir.filters"].sudo().search([
                ("name", "=", row["name"]),
                ("model_id", "=", row["model_id"]),
                ("user_ids", "in", users_ids or [False]),
            ])
            exact = candidates.filtered(
                lambda item: sorted(item.user_ids.ids) == users_ids,
            )
            if len(exact) > 1:
                raise RuntimeError(
                    f"Saved filter {row['id']} has ambiguous target candidates",
                )
            target = exact or self.env["ir.filters"].sudo().create(values)
            if exact:
                target.write(values)
            creator = users.get(row["create_uid"])
            writer = users.get(row["write_uid"])
            self.env.cr.execute(
                "UPDATE ir_filters SET create_date=COALESCE(%s, create_date), "
                "write_date=COALESCE(%s, write_date), "
                "create_uid=COALESCE(%s, create_uid), "
                "write_uid=COALESCE(%s, write_uid) WHERE id=%s",
                (
                    row["create_date"],
                    row["write_date"],
                    creator.id if creator else None,
                    writer.id if writer else None,
                    target.id,
                ),
            )
            migrated.append(target.id)
            filters_by_source[row["id"]] = target
        return {
            "filters": {
                "migrated": sorted(MIGRATED_FILTER_IDS),
                "native_recomputed": sorted(NATIVE_FILTER_IDS),
                "deliberately_not_copied_sales_marketing": sorted(
                    DROPPED_SALES_MARKETING_FILTER_IDS,
                ),
                "target_ids": migrated,
            },
            "exports": {
                "native_recomputed": sorted(NATIVE_EXPORT_IDS),
                "deliberately_not_copied_sales_marketing": sorted(
                    DROPPED_SALES_MARKETING_EXPORT_IDS,
                ),
                "deliberately_not_copied_ai_experiments": sorted(
                    DROPPED_AI_EXPORT_IDS,
                ),
            },
            "home": self._restore_valentin_home(
                source,
                users,
                filters_by_source,
            ),
        }

    def restore_preferences(self, source):
        """Finalize saved filters after their business targets exist."""
        self.ensure_one()
        if self.status != "passed":
            message = "Identity business restoration must pass before preferences"
            raise RuntimeError(message)
        users = {
            row["id"]: self._traced("res.users", row["id"])
            for row in source["users"]
        }
        missing_users = sorted(
            source_id for source_id, user in users.items() if not user
        )
        if missing_users:
            raise RuntimeError(
                f"Saved preferences reference missing users: {missing_users}",
            )
        dispositions = self._restore_preferences(source, users)
        statistics = dict(self.statistics_json or {})
        statistics["preference_dispositions"] = dispositions
        self.write({"statistics_json": statistics})
        return dispositions

    def _completed_preference_dispositions(self):
        """Return the newest completed preference audit for this snapshot."""
        self.ensure_one()
        previous_runs = self.search(
            [
                ("id", "!=", self.id),
                ("source_database", "=", self.source_database),
                ("source_snapshot", "=", self.source_snapshot),
                ("status", "=", "passed"),
            ],
            order="id desc",
        )
        return next(
            (
                dispositions
                for previous_run in previous_runs
                if (
                    dispositions := (previous_run.statistics_json or {}).get(
                        "preference_dispositions",
                    )
                )
                and dispositions.get("status") != "deferred"
                and dispositions.get("home")
            ),
            None,
        )

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
        company_partners = self._company_partner_targets(
            source["companies"],
            companies,
        )

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
            company_partner = company_partners.get(row["id"])
            if (
                target_user
                and company_partner
                and target_user.partner_id != company_partner
            ):
                raise RuntimeError(
                    "Source partner is both a user and a company identity but "
                    f"maps to different target partners: {row['id']}",
                )
            partner = (
                target_user.partner_id
                if target_user
                else company_partner or self._traced("res.partner", row["id"])
            )
            values = {field_name: row.get(field_name) for field_name in partner_fields}
            # SaaS 19.3 can retain NULL ranks on archived technical partners,
            # while Community stores the native zero default.  This carries no
            # customer or supplier semantics, so make the translation explicit.
            for rank_field in ("supplier_rank", "customer_rank"):
                if values[rank_field] is None:
                    values[rank_field] = 0
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
                        "usl_expense_multi_company": (
                            not row["share"] and len(allowed_companies) > 1
                        ),
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
                # mail's security alert is triggered by the presence of the
                # ``login`` key, even when its value is unchanged. A restore
                # must never enqueue a security email for a no-op write.
                if values.get("login") == user.login:
                    values.pop("login")
                user.sudo().with_context(
                    no_reset_password=True,
                    tracking_disable=True,
                    mail_create_nolog=True,
                    mail_create_nosubscribe=True,
                    mail_auto_subscribe_no_notify=True,
                ).write(values)
            else:
                # project_todo creates a personal welcome task whenever an
                # internal user is created.  That is useful for a new Odoo
                # database, but it is not source business data and would make
                # a reconstructed target diverge once per restored user.
                user = self._create_restored_user(values)
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
            source_partner = source_partner_rows[source_id]
            source_user_id = source_partner["user_id"]
            partner_values = {
                "category_id": [Command.set(categories_by_partner.get(source_id, []))],
                "user_id": users[source_user_id].id
                if source_user_id in users
                else False,
            }
            if source_id not in native_partner_ids:
                # Odoo deactivates a partner while creating its archived user.
                # Online can retain the contact independently, so restore the
                # authoritative contact lifecycle afterward. Built-in runtime
                # identities keep their native target lifecycle instead.
                partner_values["active"] = source_partner["active"]
            partner.sudo().with_context(tracking_disable=True).write(partner_values)

        for row in source["images"]:
            partner = partners.get(row["res_id"])
            if not partner:
                raise RuntimeError(
                    f"Identity image {row['id']} references missing partner {row['res_id']}",
                )
            partner.sudo().with_context(tracking_disable=True).write(
                {"image_1920": BinaryBytes(source_binary(row))},
            )

        for row in source["companies"]:
            if companies[row["id"]].partner_id != partners[row["partner_id"]]:
                raise RuntimeError(
                    "Restored company does not own its source partner identity: "
                    f"company {row['id']}, partner {row['partner_id']}",
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
            "images": len(source["images"]),
            "filters": len(source["filters"]),
            "filter_users": len(source["filter_users"]),
            "exports": len(source["exports"]),
            "export_lines": len(source["export_lines"]),
        }
        if counts != source["counts"]:
            raise RuntimeError(f"Identity source/target counts differ: {source['counts']} != {counts}")
        # A reconstruction resume may replay Identity after saved preferences
        # were already finalized for this same locked snapshot.  Preserve that
        # completed disposition on the new audit run so the immediate validator
        # rechecks the existing filters instead of incorrectly requiring them
        # to be absent.  A different snapshot must always start deferred.
        previous_preferences = self._completed_preference_dispositions()
        preference_dispositions = (
            previous_preferences
            if previous_preferences
            else {"status": "deferred"}
        )
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
                    # Project stages and tags do not exist yet. A governed
                    # post-Project step resolves every saved-filter reference
                    # before this temporary module can be finalized.
                    "preference_dispositions": preference_dispositions,
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
        "database": os.getenv("IDENTITY_SOURCE_DATABASE", "odoo_online_source_saas_19_3"),
    }
