import base64
import hashlib
import html
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras
from psycopg2 import sql
from odoo import Command, fields, models

from odoo.addons.usl_collaboration_restore.routing import (
    DIRECT_MODELS,
    EXPECTED_COUNTS,
    EXPECTED_MESSAGE_DISPOSITIONS,
    EXTERNAL_ARCHIVE_MODELS,
    TRANSLATED_MODELS,
    route_model,
    route_technical_table,
)

SOURCE_SHA256 = "ad313e28586fafa27a4f6a266df57080456613dff1c8c2c6d7e012732bf633b1"
SENSITIVE_FIELD_PATTERN = re.compile(r"(^|_)(ssn|ssnid|social_security)(_|$)", re.I)


class UslCollaborationRestoreMapping(models.Model):
    _name = "usl.collaboration.restore.mapping"
    _description = "Temporary Collaboration Source Binding"
    _order = "source_model, source_id, target_model"

    source_snapshot = fields.Char(required=True, index=True)
    source_model = fields.Char(required=True, index=True)
    source_id = fields.Integer(required=True, index=True)
    target_model = fields.Char(required=True, index=True)
    target_id = fields.Integer(required=True, index=True)
    source_checksum = fields.Char(required=True)

    _source_target_unique = models.Constraint(
        "UNIQUE(source_snapshot, source_model, source_id, target_model)",
        "A source collaboration row may only map once to a target model.",
    )


class UslCollaborationRestoreRun(models.Model):
    _name = "usl.collaboration.restore.run"
    _description = "USL Collaboration Restoration Run"
    _order = "started_at desc, id desc"

    name = fields.Char(required=True, default="Collaboration restoration")
    source_snapshot = fields.Char(required=True, index=True)
    source_database = fields.Char(required=True)
    target_database = fields.Char(required=True)
    status = fields.Selection(
        [("running", "Running"), ("passed", "Passed"), ("failed", "Failed")],
        required=True,
        default="running",
    )
    started_at = fields.Datetime(required=True, default=fields.Datetime.now)
    finished_at = fields.Datetime()
    statistics_json = fields.Json(readonly=True)
    evidence_sha256 = fields.Char(readonly=True)

    @staticmethod
    def _text(value):
        if isinstance(value, dict):
            return value.get("fr_FR") or value.get("en_US") or next(iter(value.values()), "")
        return value or ""

    @staticmethod
    def _checksum(value):
        return hashlib.sha256(
            json.dumps(value, default=str, ensure_ascii=False, sort_keys=True).encode(),
        ).hexdigest()

    @staticmethod
    def _rows(cursor, query, params=()):
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def _source_payload(self):
        connection = psycopg2.connect(
            host=os.environ.get("COLLABORATION_SOURCE_DB_HOST", "accounting-source-db"),
            port=int(os.environ.get("COLLABORATION_SOURCE_DB_PORT", "5432")),
            user=os.environ.get("COLLABORATION_SOURCE_DB_USER", "odoo"),
            password=os.environ.get("COLLABORATION_SOURCE_DB_PASSWORD", "odoo"),
            dbname=self.source_database,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        connection.set_session(readonly=True, autocommit=False)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                if cursor.fetchone()["transaction_read_only"] != "on":
                    message = "Collaboration extraction requires a read-only source transaction"
                    raise RuntimeError(message)
                messages = self._rows(cursor, """
                    SELECT id, parent_id, res_id, record_company_id, subtype_id,
                           mail_activity_type_id, author_id, create_uid, write_uid,
                           subject, model, message_type, email_from, message_id,
                           reply_to, incoming_email_cc, outgoing_email_to,
                           incoming_email_to, email_layout_xmlid,
                           reply_to_force_new, email_add_signature,
                           body, is_internal, date, create_date, write_date
                      FROM mail_message ORDER BY id
                """)
                payload = {
                    "messages": messages,
                    "tracking": self._rows(cursor, """
                        SELECT value.*, field.model AS field_model,
                               field.name AS field_name, field.ttype AS field_type
                          FROM mail_tracking_value value
                     LEFT JOIN ir_model_fields field ON field.id = value.field_id
                         ORDER BY value.id
                    """),
                    "recipients": self._rows(cursor, """
                        SELECT mail_message_id AS message_id,
                               res_partner_id AS partner_id
                          FROM mail_message_res_partner_rel
                         ORDER BY mail_message_id, res_partner_id
                    """),
                    "message_attachments": self._rows(cursor, """
                        SELECT message_id, attachment_id
                          FROM message_attachment_rel
                         ORDER BY message_id, attachment_id
                    """),
                    "message_attachment_details": self._rows(cursor, """
                        SELECT relation.message_id, relation.attachment_id,
                               attachment.name, attachment.checksum,
                               attachment.file_size, attachment.mimetype,
                               attachment.res_model, attachment.res_id
                          FROM message_attachment_rel relation
                          JOIN ir_attachment attachment
                            ON attachment.id = relation.attachment_id
                         ORDER BY relation.message_id, relation.attachment_id
                    """),
                    "reactions": self._rows(cursor, """
                        SELECT id, message_id, partner_id, guest_id, content
                          FROM mail_message_reaction ORDER BY id
                    """),
                    "link_previews": self._rows(cursor, """
                        SELECT id, source_url, og_type, og_title, og_site_name,
                               og_image, og_mimetype, image_mimetype, og_description,
                               create_date, write_date
                          FROM mail_link_preview ORDER BY id
                    """),
                    "message_link_previews": self._rows(cursor, """
                        SELECT id, message_id, link_preview_id, sequence, is_hidden,
                               create_date, write_date
                          FROM mail_message_link_preview ORDER BY id
                    """),
                    "followers": self._rows(cursor, """
                        SELECT id, res_id, partner_id, res_model
                          FROM mail_followers ORDER BY id
                    """),
                    "follower_subtypes": self._rows(cursor, """
                        SELECT mail_followers_id AS follower_id,
                               mail_message_subtype_id AS subtype_id
                          FROM mail_followers_mail_message_subtype_rel
                         ORDER BY mail_followers_id, mail_message_subtype_id
                    """),
                    "message_subtypes": self._rows(cursor, """
                        SELECT id, parent_id, sequence, relation_field, res_model,
                               name, description, internal, "default", hidden,
                               track_recipients, create_date, write_date
                          FROM mail_message_subtype ORDER BY id
                    """),
                    "activities": self._rows(cursor, """
                        SELECT id, res_id, activity_type_id, user_id, create_uid,
                               write_uid, res_model, summary, date_deadline, date_done,
                               note, feedback, automated, active, create_date, write_date
                          FROM mail_activity ORDER BY id
                    """),
                    "activity_types": self._rows(cursor, """
                        SELECT id, name, active, category, summary, icon,
                               decoration_type, delay_count, delay_unit, delay_from,
                               res_model
                          FROM mail_activity_type ORDER BY id
                    """),
                    "channels": self._rows(cursor, """
                        SELECT id, parent_channel_id, from_message_id, group_public_id,
                               name, channel_type, default_display_mode, description,
                               active, is_readonly, create_date, write_date
                          FROM discuss_channel ORDER BY id
                    """),
                    "channel_members": self._rows(cursor, """
                        SELECT id, partner_id, channel_id, seen_message_id,
                               new_message_separator, custom_notifications,
                               channel_role, is_favorite, create_date, write_date
                          FROM discuss_channel_member ORDER BY id
                    """),
                    "channel_groups": self._rows(cursor, """
                        SELECT discuss_channel_id AS channel_id,
                               res_groups_id AS group_id
                          FROM discuss_channel_res_groups_rel
                         ORDER BY discuss_channel_id, res_groups_id
                    """),
                    "xmlids": self._rows(cursor, """
                        SELECT model, res_id, module || '.' || name AS xmlid
                          FROM ir_model_data
                         WHERE model IN (
                            'mail.message.subtype', 'mail.activity.type',
                            'mail.activity.plan', 'mail.activity.plan.template',
                            'mail.canned.response', 'mail.template', 'sms.template',
                            'discuss.channel', 'res.groups'
                         )
                         ORDER BY model, res_id, module, name
                    """),
                    "users": self._rows(cursor, """
                        SELECT id, partner_id, active, share, login
                          FROM res_users ORDER BY id
                    """),
                    "return_types": self._rows(cursor, """
                        SELECT id, country_id, name, category, states_workflow,
                               default_deadline_periodicity, deadline_periodicity,
                               active, create_date, write_date
                          FROM account_return_type ORDER BY id
                    """),
                    "returns": self._rows(cursor, """
                        SELECT id, type_id, company_id, name, state, audit_status,
                               date_from, date_to, date_deadline, date_submission,
                               total_amount_to_pay, period_amount_to_pay, active,
                               is_completed, manually_created, create_date, write_date
                          FROM account_return ORDER BY id
                    """),
                    "knowledge": self._rows(cursor, """
                        SELECT id, parent_id, root_article_id, name, body, active,
                               is_locked, internal_permission, create_uid, write_uid,
                               last_edition_date, create_date, write_date
                          FROM knowledge_article ORDER BY id
                    """),
                    "document_nodes": self._rows(cursor, """
                        SELECT id, folder_id, company_id, owner_id, create_uid,
                               write_uid, type, name, url, active,
                               access_internal, create_date, write_date
                          FROM documents_document
                         ORDER BY id
                    """),
                    "sign_requests": self._rows(cursor, """
                        SELECT id, subject, reference, state, completion_date,
                               active, create_date, write_date
                          FROM sign_request ORDER BY id
                    """),
                    "sign_attachments": self._rows(cursor, """
                        SELECT id, res_id, name, checksum, store_fname,
                               file_size, mimetype, create_date
                          FROM ir_attachment
                         WHERE res_model = 'sign.request'
                         ORDER BY res_id, create_date, id
                    """),
                    "sign_email_links": self._rows(cursor, """
                        SELECT DISTINCT message.id AS message_id,
                               request.id AS request_id
                          FROM mail_message message
                          JOIN mail_mail outgoing
                            ON outgoing.mail_message_id = message.id
                          JOIN sign_request request
                            ON request.subject = message.subject
                          JOIN sign_request_item signer
                            ON signer.sign_request_id = request.id
                     LEFT JOIN res_partner signer_partner
                            ON signer_partner.id = signer.partner_id
                         WHERE message.model IS NULL
                           AND outgoing.state = 'sent'
                           AND outgoing.create_date >= request.create_date
                           AND outgoing.create_date < request.create_date + INTERVAL '2 days'
                           AND lower(outgoing.email_to) LIKE '%%' || lower(
                               COALESCE(NULLIF(signer.signer_email, ''), signer_partner.email)
                           ) || '%%'
                           AND NOT EXISTS (
                               SELECT 1 FROM message_attachment_rel relation
                                WHERE relation.message_id = message.id
                           )
                         ORDER BY message.id, request.id
                    """),
                    "notifications": self._rows(cursor, """
                        SELECT id, mail_message_id, notification_type,
                               notification_status, failure_type
                          FROM mail_notification ORDER BY id
                    """),
                    "mail_queue": self._rows(cursor, """
                        SELECT id, mail_message_id, state, is_notification,
                               auto_delete, scheduled_date, email_cc, email_to,
                               "references", headers, failure_type, failure_reason,
                               create_date, write_date
                          FROM mail_mail ORDER BY id
                    """),
                    "aliases": self._rows(cursor, """
                        SELECT alias.id, alias.alias_force_thread_id,
                               alias.alias_parent_thread_id, alias.alias_name,
                               alias.alias_contact, alias.alias_status,
                               alias.alias_defaults, alias.alias_incoming_local,
                               alias.create_date, alias.write_date,
                               model.model AS alias_model,
                               parent.model AS alias_parent_model,
                               domain.name AS source_alias_domain
                          FROM mail_alias alias
                          JOIN ir_model model ON model.id = alias.alias_model_id
                     LEFT JOIN ir_model parent ON parent.id = alias.alias_parent_model_id
                     LEFT JOIN mail_alias_domain domain ON domain.id = alias.alias_domain_id
                         ORDER BY alias.id
                    """),
                }
                handled_tables = {
                    "discuss_channel", "discuss_channel_member",
                    "discuss_channel_res_groups_rel", "mail_activity",
                    "mail_activity_type", "mail_alias", "mail_followers",
                    "mail_followers_mail_message_subtype_rel", "mail_link_preview",
                    "mail_mail", "mail_message", "mail_message_link_preview",
                    "mail_message_reaction", "mail_message_res_partner_rel",
                    "mail_message_subtype", "mail_notification", "mail_tracking_value",
                }
                cursor.execute(r"""
                    SELECT tablename
                      FROM pg_catalog.pg_tables
                     WHERE schemaname = 'public'
                       AND (tablename LIKE 'mail\_%' ESCAPE E'\\'
                            OR tablename LIKE 'discuss\_%' ESCAPE E'\\'
                            OR tablename = 'sms_template')
                     ORDER BY tablename
                """)
                technical_rows = []
                for item in cursor.fetchall():
                    table = item["tablename"]
                    if table in handled_tables:
                        continue
                    cursor.execute(
                        sql.SQL("SELECT to_jsonb(source) AS source FROM {} source ORDER BY to_jsonb(source)::text").format(
                            sql.Identifier(table),
                        ),
                    )
                    technical_rows.extend(
                        {"table": table, "source": row["source"]}
                        for row in cursor.fetchall()
                    )
                payload["technical_rows"] = technical_rows
        finally:
            connection.close()
        return payload

    def _mapping(self, source_model, source_id, target_model=None):
        domain = [
            ("source_snapshot", "=", self.source_snapshot),
            ("source_model", "=", source_model),
            ("source_id", "=", source_id),
        ]
        if target_model:
            domain.append(("target_model", "=", target_model))
        return self.env["usl.collaboration.restore.mapping"].sudo().search(domain, limit=1)

    def _bind(self, source_model, source_id, record, source_row):
        values = {
            "source_snapshot": self.source_snapshot,
            "source_model": source_model,
            "source_id": source_id,
            "target_model": record._name,
            "target_id": record.id,
            "source_checksum": self._checksum(source_row),
        }
        binding = self._mapping(source_model, source_id, record._name)
        if binding:
            binding.write(values)
        else:
            binding = self.env["usl.collaboration.restore.mapping"].sudo().create(values)
        return binding

    def _bound_record(self, source_model, source_id, target_model):
        binding = self._mapping(source_model, source_id, target_model)
        Target = self.env.get(target_model, False)
        # An Odoo model proxy is an empty recordset and therefore falsey.  Test
        # the sentinel explicitly or every valid model is treated as missing.
        if not binding or Target is False:
            return Target
        return Target.sudo().browse(binding.target_id).exists()

    def _traced(self, model, source_id, source_model=None):
        Model = self.env.get(model, False)
        if not source_id or Model is False:
            return Model
        Model = Model.sudo().with_context(active_test=False)
        if "rebuild_source_id" not in Model._fields:
            return Model
        domain = [("rebuild_source_id", "=", source_id)]
        if source_model and "rebuild_source_model" in Model._fields:
            if model == "account.move":
                domain.append(("rebuild_source_model", "=like", f"{source_model}%"))
            else:
                domain.append(("rebuild_source_model", "=", source_model))
        records = Model.search(domain, limit=2)
        return records if len(records) == 1 else Model

    def _existing_message(self, source_id):
        bound = self._bound_record("mail.message", source_id, "mail.message")
        if bound:
            return bound
        traced = self._traced("mail.message", source_id, "mail.message")
        if traced:
            return traced
        if "usl.tese.restore.mapping" in self.env:
            mapping = self.env["usl.tese.restore.mapping"].sudo().search([
                ("source_model", "=", "mail.message"),
                ("source_id", "=", source_id),
                ("target_model", "=", "mail.message"),
            ], limit=1)
            if mapping:
                return self.env["mail.message"].sudo().browse(mapping.target_id).exists()
        return self.env["mail.message"]

    def _target_xmlid(self, xmlids, model, source_id):
        if not source_id:
            return self.env[model]
        xmlid = xmlids.get((model, source_id))
        return self.env.ref(xmlid, raise_if_not_found=False) if xmlid else self.env[model]

    def _documents_evidence(self):
        path = Path(os.environ.get("COLLABORATION_DOCUMENTS_EVIDENCE", ""))
        if not path.is_file():
            message = "Full Documents restore evidence is required for Collaboration"
            raise RuntimeError(message)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != "usl-documents-source-restore-result-v1":
            message = "Unexpected Documents restore evidence schema"
            raise RuntimeError(message)
        if payload.get("source_dump_sha256") != SOURCE_SHA256:
            message = "Documents evidence belongs to a different source dump"
            raise RuntimeError(message)
        if not payload.get("source_profile_is_full"):
            message = "Collaboration requires full Documents restore evidence"
            raise RuntimeError(message)
        document_map = {}
        attachment_map = {}
        sha1_map = {}
        for item in payload.get("documents", []):
            target_id = item["odoo_document_id"]
            for source_id in item.get("source_document_ids", []):
                document_map[source_id] = target_id
            for source_id in item.get("source_attachment_ids", []):
                attachment_map[source_id] = target_id
            for source in item.get("source_truth", []):
                if source.get("sha1"):
                    sha1_map[source["sha1"]] = target_id
        return document_map, attachment_map, sha1_map

    def _restore_legacy_declarations(self, payload, users):
        Rule = self.env["rebuild.account.declaration.rule"].sudo().with_context(active_test=False)
        Declaration = self.env["rebuild.account.declaration"].sudo()
        companies = {
            row["company_id"]: self._traced("res.company", row["company_id"], "res.company")
            for row in payload["returns"]
        }
        returns_by_type = defaultdict(list)
        for row in payload["returns"]:
            returns_by_type[row["type_id"]].append(row)
        semantic_codes = [
            re.sub(r"[^A-Z0-9]+", "_", self._text(row["name"]).upper()).strip("_")[:48]
            for row in payload["return_types"]
        ]
        if len(set(semantic_codes)) != len(semantic_codes):
            message = "Retired declaration type names do not form unique semantic keys"
            raise RuntimeError(message)
        rules = {}
        for row in payload["return_types"]:
            name = self._text(row["name"]) or f"Retired declaration workflow {row['id']}"
            slug = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")[:48]
            code = f"LEGACY_{slug}"
            existing = Rule.search([("code", "=", code), ("version", "=", "retired")], limit=1)
            dates = [item["date_from"] for item in returns_by_type[row["id"]] if item["date_from"]]
            end_dates = [
                item["date_to"]
                for item in returns_by_type[row["id"]]
                if item["date_to"]
            ]
            source_created = fields.Date.to_date(row["create_date"])
            source_updated = fields.Date.to_date(row["write_date"] or row["create_date"])
            values = {
                "name": f"{name} (retired legacy workflow)",
                "code": code,
                "company_id": next((
                    companies[item["company_id"]].id
                    for item in returns_by_type[row["id"]]
                    if companies.get(item["company_id"])
                ), False),
                "origin": "company",
                "source_module": "rebuild_account_migration",
                "definition_version": "retired",
                "lifecycle": "deprecated",
                "active": False,
                "country_id": self.env.ref("base.fr").id,
                "category": "legacy",
                "cadence": "annual",
                "form_code": name,
                "version": "retired",
                "effective_from": min(dates) if dates else source_created,
                "effective_to": max(end_dates or dates or [source_updated]),
                "official_source_label": "Retired Odoo Online declaration workflow",
                "official_url": "https://www.impots.gouv.fr/",
                "portal_url": "https://cfspro.impots.gouv.fr/mire/accueil.do",
                "applicability_guidance": "Retained only to display historical business records and chatter.",
                "filing_guidance": "Do not use this retired definition for a new filing.",
                "deadline_guidance": "The original record deadline is retained on each archived declaration.",
                "business_purpose": "Read-only continuity for a retired source workflow.",
                "expected_outcome": "Historical records remain accessible without entering the current scheduler.",
                "technical_model": "rebuild.account.declaration",
            }
            if existing:
                existing.with_context(accounting_definition_seed=True, tracking_disable=True).write(values)
                rule = existing
            else:
                rule = Rule.with_context(accounting_definition_seed=True, tracking_disable=True).create(values)
            rules[row["id"]] = rule
            self._bind("account.return.type", row["id"], rule, row)
        declarations = {}
        for row in payload["returns"]:
            company = companies[row["company_id"]]
            rule = rules[row["type_id"]]
            if not company or not rule:
                continue
            existing = self._bound_record("account.return", row["id"], Declaration._name)
            snapshot = rule._definition_snapshot()
            snapshot["legacy_business_record"] = {
                "name": self._text(row["name"]),
                "state": row["state"],
                "audit_status": row["audit_status"],
                "active": bool(row["active"]),
                "completed": bool(row["is_completed"]),
                "submitted_on": str(row["date_submission"] or ""),
            }
            values = {
                "name": self._text(row["name"]) or rule.name,
                "company_id": company.id,
                "rule_id": rule.id,
                "definition_snapshot": snapshot,
                "period_start": row["date_from"],
                "period_end": row["date_to"],
                "fiscalyear_start": row["date_from"],
                "fiscalyear_end": row["date_to"],
                "deadline_date": row["date_deadline"] or row["date_to"],
                "deadline_basis": "Original deadline retained from the retired source workflow.",
                "applicability": "applicable",
                "applicability_reason": "Archived source business obligation; excluded from current scheduling.",
                "status": "archived",
                "validation_status": "not_run",
                "review_status": "not_started",
                "filing_status": "not_started",
                "payment_status": "not_assessed",
                "acceptance_status": "not_submitted",
                "amount_due": row["total_amount_to_pay"] or row["period_amount_to_pay"] or 0,
            }
            context = {"tracking_disable": True, "mail_create_nolog": True, "mail_create_nosubscribe": True}
            if existing:
                existing.with_context(**context).write(values)
                declaration = existing
            else:
                declaration = Declaration.with_context(**context).create(values)
            declarations[row["id"]] = declaration
            self._bind("account.return", row["id"], declaration, row)
            self._stamp_audit(declaration, row, users)
        return rules, declarations

    def _archive_bytes(
        self, filename, content, mimetype, *, company, confidentiality="internal",
        source="odoo_attachment",
    ):
        """Return the checksum-canonical Documents root for exact bytes."""
        Document = self.env["usl.document"].sudo()
        checksum = hashlib.sha256(content).hexdigest()
        existing = Document.search([
            ("availability_state", "=", "available"),
            "|", ("checksum", "=", checksum), ("version_ids.checksum", "=", checksum),
        ], limit=1)
        if existing:
            return existing
        upload = Document.with_user(self.env.ref("base.user_root")).with_company(company).upload_from_odoo(
            filename,
            base64.b64encode(content).decode(),
            mimetype or "application/octet-stream",
            company_id=company.id,
            confidentiality=confidentiality,
            source=source,
        )
        if upload["state"] == "duplicate":
            return Document.browse(upload["document_id"])
        if upload["state"] != "processing":
            raise RuntimeError(f"Documents archival failed for {filename!r}: {upload}")
        operation = self.env["usl.document.operation"].sudo().browse(upload["operation_id"])
        deadline = time.monotonic() + 180
        while operation.state == "processing" and time.monotonic() < deadline:
            operation.poll()
            # Paperless completes asynchronously.  Commit only the canonical
            # document operation so a later source-row failure can safely reuse
            # the same checksum root instead of uploading duplicate bytes.
            self.env.cr.commit()
            operation.invalidate_recordset()
            if operation.state == "processing":
                time.sleep(2)
        if operation.state != "archived" or not operation.document_id:
            raise RuntimeError(
                f"Documents archival failed for {filename!r}: {operation.error_message}",
            )
        return operation.document_id

    def _restore_sign_documents(self, payload, evidence_sha1_map):
        """Archive exact Sign request payloads and select one root per request."""
        attachment_documents = {}
        by_request = defaultdict(list)
        # Odoo Online export packages expose the filestore contents directly;
        # unlike a live Odoo data directory they do not add a database-name
        # level beneath ``filestore``.
        source_root = Path("/mnt/accounting-source/filestore")
        for row in payload["sign_attachments"]:
            document = self._bound_record("ir.attachment", row["id"], "usl.document")
            if not document:
                target_id = evidence_sha1_map.get(row["checksum"])
                document = self.env["usl.document"].sudo().browse(target_id).exists() if target_id else False
            content = None
            if not document:
                source_path = source_root / row["store_fname"]
                if not source_path.is_file():
                    raise RuntimeError(f"Signed source attachment {row['id']} is missing")
                content = source_path.read_bytes()
                if hashlib.sha1(content).hexdigest() != row["checksum"]:
                    raise RuntimeError(f"Signed source attachment {row['id']} checksum changed")
                if row["file_size"] is not None and len(content) != row["file_size"]:
                    raise RuntimeError(f"Signed source attachment {row['id']} size changed")
                document = self._archive_bytes(
                    row["name"] or f"signed-evidence-{row['id']}",
                    content,
                    row["mimetype"],
                    company=self.env.company,
                    confidentiality="internal",
                )
            attachment_documents[row["id"]] = document
            by_request[row["res_id"]].append((row["create_date"], row["id"], document))
            self._bind("ir.attachment", row["id"], document, row)

        request_documents = {}
        for request in payload["sign_requests"]:
            candidates = sorted(by_request[request["id"]], key=lambda item: (item[0], item[1]))
            if not candidates:
                continue
            canonical = candidates[-1][2]
            request_documents[request["id"]] = canonical
            self._bind("sign.request", request["id"], canonical, request)
            links = "".join(
                f'<li><a href="/odoo/usl.document/{document.id}">{html.escape(next(row["name"] for row in payload["sign_attachments"] if row["id"] == attachment_id) or "Signed evidence")}</a></li>'
                for _created, attachment_id, document in candidates
            )
            values = {
                "model": canonical._name,
                "res_id": canonical.id,
                "body": f"<p><strong>Signed evidence for this legacy request</strong></p><ul>{links}</ul>",
                "message_type": "comment",
                "subtype_id": self.env.ref("mail.mt_note").id,
                "is_internal": True,
                "date": request["completion_date"] or request["write_date"],
            }
            note = self._bound_record("sign.request.documents.note", request["id"], "mail.message")
            if note:
                note.write(values)
            else:
                note = self.env["mail.message"].sudo().with_context(
                    tracking_disable=True,
                    mail_create_nolog=True,
                    mail_create_nosubscribe=True,
                ).create(values)
            self._bind("sign.request.documents.note", request["id"], note, request)
        return attachment_documents, request_documents

    def _restore_late_expenses(self, payload):
        source_ids = sorted({
            row["res_id"] for row in payload["messages"]
            if row["model"] == "hr.expense" and row["res_id"]
        })
        missing = [
            source_id for source_id in source_ids
            if not self._traced("hr.expense", source_id, "hr.expense")
        ]
        if not missing:
            return
        Run = self.env.get("rebuild.account.import.run", False)
        if Run is False:
            raise RuntimeError(f"Accounting expense materializer is unavailable for {missing}")
        run = Run.sudo().create({"name": "Collaboration late expense reconciliation"})
        run.run_source_faithful_expense_materialization_from_source({
            "source_database": self.source_database,
            "source_snapshot_id": self.source_snapshot,
            "source_dump_sha256": SOURCE_SHA256,
            "source_version": "Odoo Online Enterprise saas~19.3",
            "target_database": self.env.cr.dbname,
            "date_from": "2024-01-10",
            "date_to": "2026-08-31",
            "source_company_ids": [1, 8],
            # Collaboration restores the original source messages and their
            # attachment relations immediately after this native materializer.
            # Do not create Accounting's synthetic attachment note here: it
            # can notify existing expense followers and would be replaced by
            # the source message in the same transaction anyway.
            "defer_attachment_chatter_to_collaboration": True,
            "source_host": os.environ.get("COLLABORATION_SOURCE_DB_HOST", "accounting-source-db"),
            "source_port": int(os.environ.get("COLLABORATION_SOURCE_DB_PORT", "5432")),
            "source_user": os.environ.get("COLLABORATION_SOURCE_DB_USER", "odoo"),
            "source_password": os.environ.get("COLLABORATION_SOURCE_DB_PASSWORD", "odoo"),
        })
        remaining = [
            source_id for source_id in missing
            if not self._traced("hr.expense", source_id, "hr.expense")
        ]
        if remaining:
            raise RuntimeError(f"Accounting could not materialize late source expenses {remaining}")

    def _stamp_audit(self, record, row, users):
        table = record._table
        creator = users.get(row.get("create_uid"))
        writer = users.get(row.get("write_uid"))
        self.env.cr.execute(
            f'UPDATE "{table}" SET create_date=COALESCE(%s, create_date), '
            f'write_date=COALESCE(%s, write_date), create_uid=COALESCE(%s, create_uid), '
            f'write_uid=COALESCE(%s, write_uid) WHERE id=%s',
            (row.get("create_date"), row.get("write_date"), creator.id if creator else None,
             writer.id if writer else None, record.id),
        )
        record.invalidate_recordset()

    def action_restore(self):
        self.ensure_one()
        side_effect_models = tuple(
            name for name in ("mail.notification", "mail.mail", "sms.sms", "mail.push")
            if name in self.env
        )
        side_effect_ids_before = {
            name: set(self.env[name].sudo().search([]).ids)
            for name in side_effect_models
        }
        side_effect_before = {
            name: len(ids)
            for name, ids in side_effect_ids_before.items()
        }

        def assert_no_delivery_state(stage):
            current_ids = {
                name: set(self.env[name].sudo().search([]).ids)
                for name in side_effect_models
            }
            if current_ids != side_effect_ids_before:
                delta = {
                    name: {
                        "created": sorted(ids - side_effect_ids_before[name]),
                        "removed": sorted(side_effect_ids_before[name] - ids),
                    }
                    for name, ids in current_ids.items()
                    if ids != side_effect_ids_before[name]
                }
                details = {}
                for name, changes in delta.items():
                    if not changes["created"]:
                        continue
                    Model = self.env[name].sudo()
                    diagnostic_fields = [
                        field_name
                        for field_name in (
                            "subject",
                            "model",
                            "res_id",
                            "mail_message_id",
                            "state",
                            "email_from",
                            "email_to",
                        )
                        if field_name in Model._fields
                    ]
                    details[name] = Model.browse(changes["created"]).read(
                        diagnostic_fields,
                    )
                raise RuntimeError(
                    f"Collaboration restoration produced outbound delivery state "
                    f"during {stage}: {delta}; records={details}",
                )
            return {
                name: len(ids)
                for name, ids in current_ids.items()
            }
        payload = self._source_payload()
        source_messages_by_id = {row["id"]: row for row in payload["messages"]}
        actual = {
            "activities": len(payload["activities"]),
            "aliases": len(payload["aliases"]),
            "attachment_relations": len(payload["message_attachments"]),
            "cross_accounting_parent_links": sum(
                bool(row["parent_id"])
                and row["model"].startswith("account.")
                and bool(parent := source_messages_by_id.get(row["parent_id"]))
                and (parent["model"] or "").startswith("account.")
                and (parent["model"], parent["res_id"]) != (row["model"], row["res_id"])
                for row in payload["messages"]
            ),
            "followers": len(payload["followers"]),
            "mail_queue": len(payload["mail_queue"]),
            "messages": len(payload["messages"]),
            "notifications": len(payload["notifications"]),
            "parent_links": sum(bool(row["parent_id"]) for row in payload["messages"]),
            "tracking": len(payload["tracking"]),
        }
        if actual != EXPECTED_COUNTS:
            raise RuntimeError(f"Locked Collaboration source counts changed: {actual}")
        unclassified = sorted({
            row["model"] for row in payload["messages"]
            if route_model(row["model"]) == "unclassified"
        })
        if unclassified:
            raise RuntimeError(f"Unclassified source chatter models: {unclassified}")

        # Accounting's first source-faithful expense pass contained 432 rows;
        # the locked source now exposes nine additional draft business records
        # which own 77 messages.  Reuse the proven native Accounting
        # materializer rather than inventing partial expense records here.
        self._restore_late_expenses(payload)
        assert_no_delivery_state("late Expense reconciliation")

        xmlids = {(row["model"], row["res_id"]): row["xmlid"] for row in payload["xmlids"]}
        partners = {
            source_id: self._traced("res.partner", source_id, "res.partner")
            for source_id in {
                *(row["author_id"] for row in payload["messages"] if row["author_id"]),
                *(row["partner_id"] for row in payload["recipients"]),
                *(row["partner_id"] for row in payload["followers"] if row["partner_id"]),
                *(row["partner_id"] for row in payload["reactions"] if row["partner_id"]),
                *(row["partner_id"] for row in payload["channel_members"] if row["partner_id"]),
            }
        }
        users = {
            row["id"]: self._traced("res.users", row["id"], "res.users")
            for row in payload["users"]
        }
        companies = {
            row["record_company_id"]: self._traced("res.company", row["record_company_id"], "res.company")
            for row in payload["messages"] if row["record_company_id"]
        }
        active_internal_partners = {
            row["partner_id"] for row in payload["users"] if row["active"] and not row["share"]
        }
        document_ids, document_attachment_ids, document_sha1_ids = self._documents_evidence()
        document_records = {
            source_id: self.env["usl.document"].sudo().browse(target_id).exists()
            for source_id, target_id in document_ids.items()
        }
        retired_document_node_ids = {
            row["id"]
            for row in payload["document_nodes"]
            if row["type"] in {"folder", "url"}
        }
        rules, declarations = self._restore_legacy_declarations(payload, users)
        sign_attachment_documents, sign_documents = self._restore_sign_documents(
            payload,
            document_sha1_ids,
        )
        assert_no_delivery_state("document and legacy-record materialization")
        document_attachment_ids.update({
            source_id: document.id
            for source_id, document in sign_attachment_documents.items()
        })
        sign_request_by_attachment = {
            row["id"]: sign_documents.get(row["res_id"])
            for row in payload["sign_attachments"]
        }
        sign_email_targets = {
            row["message_id"]: sign_documents.get(row["request_id"])
            for row in payload["sign_email_links"]
        }

        channel_map = self._restore_channels(payload, xmlids, partners)
        assert_no_delivery_state("Discuss channel materialization")
        record_maps = {
            "account.return": declarations,
            "account.return.type": rules,
            "documents.document": document_records,
            "sign.request": sign_documents,
            "discuss.channel": channel_map,
        }
        message_attachments = defaultdict(list)
        for row in payload["message_attachments"]:
            message_attachments[row["message_id"]].append(row["attachment_id"])
        recipients = defaultdict(list)
        for row in payload["recipients"]:
            recipients[row["message_id"]].append(row["partner_id"])
        evidence_root = Path(os.environ["COLLABORATION_EVIDENCE_DIR"])
        dispositions = {"messages": [], "tracking": [], "followers": [], "activities": [], "other": []}
        for row in payload["knowledge"]:
            dispositions["other"].append({
                "model": "knowledge.article",
                "id": row["id"],
                "disposition": "deliberately_not_copied",
                "reason": "approved Knowledge demo/configuration exclusion",
                "source_sha256": self._checksum(row),
            })
        subtype_map = self._restore_subtypes(payload, xmlids, dispositions)
        messages = {}
        dropped_message_ids = set()

        for row in payload["messages"]:
            target = self._resolve_message_target(
                row,
                record_maps,
                message_attachments[row["id"]],
                document_attachment_ids,
                sign_request_by_attachment,
                sign_email_targets,
            )
            route = route_model(row["model"])
            if route == "deliberately_not_copied":
                dropped_message_ids.add(row["id"])
                dispositions["messages"].append({
                    "id": row["id"],
                    "disposition": "deliberately_not_copied",
                    "reason": "approved Knowledge demo/configuration exclusion",
                    "source_model": row["model"], "source_res_id": row["res_id"],
                    "source_parent_id": row["parent_id"], "source_sha256": self._checksum(row),
                })
                continue
            if (
                row["model"] == "documents.document"
                and row["res_id"] in retired_document_node_ids
            ):
                dropped_message_ids.add(row["id"])
                dispositions["messages"].append({
                    "id": row["id"],
                    "disposition": "deliberately_not_copied",
                    "reason": "retired Documents folder or URL activity",
                    "source_model": row["model"], "source_res_id": row["res_id"],
                    "source_parent_id": row["parent_id"],
                    "source_sha256": self._checksum(row),
                    "source": row,
                })
                continue
            if not target:
                approved_models = EXTERNAL_ARCHIVE_MODELS | {"res.partner", "product.product"}
                no_business_payload = (
                    row["model"] in approved_models
                    and row["message_type"] in {"notification", "tracking"}
                    and row["is_internal"]
                    and not row["subject"]
                    and not row["parent_id"]
                    and not row["incoming_email_cc"]
                    and not row["incoming_email_to"]
                    and not row["outgoing_email_to"]
                    and not recipients[row["id"]]
                    and not message_attachments[row["id"]]
                )
                if not no_business_payload:
                    raise RuntimeError(
                        "A Collaboration message has no canonical target and is "
                        f"not an approved configuration-only drop: {row['id']} "
                        f"({row['model']}, {row['res_id']})",
                    )
                dropped_message_ids.add(row["id"])
                dispositions["messages"].append({
                    "id": row["id"],
                    "disposition": "deliberately_not_copied",
                    "reason": "generated configuration audit traffic",
                    "source_model": row["model"], "source_res_id": row["res_id"],
                    "source_parent_id": row["parent_id"], "source_sha256": self._checksum(row),
                })
                continue
            message = self._restore_message(
                row, target, partners, companies, subtype_map, xmlids, recipients[row["id"]],
                message_attachments[row["id"]],
            )
            messages[row["id"]] = message
            dispositions["messages"].append({
                "id": row["id"], "disposition": "native_visible",
                "source_model": row["model"], "source_res_id": row["res_id"],
                "source_parent_id": row["parent_id"],
                "source_sha256": self._checksum(row),
                "target_model": message.model, "target_id": message.res_id,
                "target_message_id": message.id,
                "body_sha256": hashlib.sha256(str(message.body or "").encode()).hexdigest(),
            })
        assert_no_delivery_state("message restoration")

        for row in payload["messages"]:
            message = messages.get(row["id"])
            parent = messages.get(row["parent_id"])
            source_parent = source_messages_by_id.get(row["parent_id"])
            if message and parent and message.parent_id != parent:
                message.sudo().write({"parent_id": parent.id})
            if row["parent_id"]:
                dispositions["other"].append({
                    "model": "mail.message.parent",
                    "id": row["id"],
                    "parent_id": row["parent_id"],
                    "source_model": row["model"], "source_res_id": row["res_id"],
                    "parent_source_model": source_parent["model"] if source_parent else None,
                    "parent_source_res_id": source_parent["res_id"] if source_parent else None,
                    "disposition": (
                        "native_parent"
                        if message and parent
                        else "deliberately_not_copied"
                        if row["id"] in dropped_message_ids
                        else "unresolved_parent_reference"
                    ),
                })
        for row in payload["messages"]:
            if messages.get(row["id"]):
                self._stamp_audit(messages[row["id"]], row, users)

        self._restore_tracking(
            payload["tracking"],
            messages,
            users,
            dispositions,
            dropped_message_ids,
        )
        assert_no_delivery_state("tracking restoration")
        self._restore_followers(
            payload, record_maps, partners, active_internal_partners, subtype_map, dispositions,
        )
        assert_no_delivery_state("follower restoration")
        self._restore_activities(payload, record_maps, users, xmlids, dispositions)
        assert_no_delivery_state("activity restoration")
        self._restore_reactions_previews(payload, messages, partners, dispositions)
        assert_no_delivery_state("reaction and link-preview restoration")
        self._finish_channel_members(payload, channel_map, messages, partners)
        assert_no_delivery_state("Discuss membership restoration")
        self._record_discuss_dispositions(payload, channel_map, dispositions)
        self._restore_aliases(payload, record_maps, dispositions)
        assert_no_delivery_state("alias restoration")
        self._record_attachment_recipient_dispositions(
            payload, messages, partners, document_attachment_ids, subtype_map,
            dispositions, dropped_message_ids,
        )
        self._remove_synthetic_accounting_notes(messages)
        synthetic_notes_remaining = self.env["mail.message"].sudo().search_count([
            ("model", "in", ["account.move", "hr.expense"]),
            ("body", "in", [
                "Supporting file restored from the source record's chatter.",
                "<p>Supporting file restored from the source record's chatter.</p>",
            ]),
        ])
        if synthetic_notes_remaining:
            raise RuntimeError(
                f"{synthetic_notes_remaining} synthetic Accounting attachment notes remain",
            )

        archives = []
        dispositions["other"].extend([
            {"model": "mail.notification", "id": row["id"], "disposition": "discard_delivery_state", "source": row}
            for row in payload["notifications"]
        ])
        dispositions["other"].extend([
            {"model": "mail.mail", "id": row["id"], "disposition": "archive_envelope_only" if row["mail_message_id"] not in messages else "discard_sent_queue", "source": row}
            for row in payload["mail_queue"]
        ])
        dispositions["other"].extend(
            self._technical_row_disposition(row, xmlids) for row in payload["technical_rows"]
        )
        side_effect_after = assert_no_delivery_state("final disposition generation")
        exact_dispositions = {
            "messages": len(dispositions["messages"]),
            "tracking": len(dispositions["tracking"]),
            "followers": len(dispositions["followers"]),
            "activities": len(dispositions["activities"]),
            "attachments": sum(
                row["model"] == "message_attachment_rel" for row in dispositions["other"]
            ),
            "aliases": sum(row["model"] == "mail.alias" for row in dispositions["other"]),
            "parent_links": sum(
                row["model"] == "mail.message.parent" for row in dispositions["other"]
            ),
            "notifications": sum(
                row["model"] == "mail.notification" for row in dispositions["other"]
            ),
            "mail_queue": sum(row["model"] == "mail.mail" for row in dispositions["other"]),
        }
        expected_dispositions = {
            "messages": EXPECTED_COUNTS["messages"],
            "tracking": EXPECTED_COUNTS["tracking"],
            "followers": EXPECTED_COUNTS["followers"],
            "activities": EXPECTED_COUNTS["activities"],
            "attachments": EXPECTED_COUNTS["attachment_relations"],
            "aliases": EXPECTED_COUNTS["aliases"],
            "parent_links": EXPECTED_COUNTS["parent_links"],
            "notifications": EXPECTED_COUNTS["notifications"],
            "mail_queue": EXPECTED_COUNTS["mail_queue"],
        }
        if exact_dispositions != expected_dispositions:
            raise RuntimeError(
                f"Collaboration rows lack exact dispositions: "
                f"{exact_dispositions} != {expected_dispositions}",
            )
        relational_dispositions = {
            "activity_types": sum(
                row["model"] == "mail.activity.type" for row in dispositions["other"]
            ),
            "channel_groups": sum(
                row["model"] == "discuss.channel.group" for row in dispositions["other"]
            ),
            "channel_members": sum(
                row["model"] == "discuss.channel.member" for row in dispositions["other"]
            ),
            "channels": sum(
                row["model"] == "discuss.channel" for row in dispositions["other"]
            ),
            "follower_subtypes": sum(
                row["model"] == "mail.follower.subtype" for row in dispositions["other"]
            ),
            "link_previews": sum(
                row["model"] == "mail.link.preview" for row in dispositions["other"]
            ),
            "message_link_previews": sum(
                row["model"] == "mail.message.link.preview"
                for row in dispositions["other"]
            ),
            "message_subtypes": sum(
                row["model"] == "mail.message.subtype" for row in dispositions["other"]
            ),
            "reactions": sum(
                row["model"] == "mail.message.reaction" for row in dispositions["other"]
            ),
            "recipients": sum(
                row["model"] == "mail.message.recipient" for row in dispositions["other"]
            ),
            "technical_rows": sum(
                bool(row.get("source_table")) for row in dispositions["other"]
            ),
        }
        expected_relational_dispositions = {
            "activity_types": len(payload["activity_types"]),
            "channel_groups": len(payload["channel_groups"]),
            "channel_members": len(payload["channel_members"]),
            "channels": len(payload["channels"]),
            "follower_subtypes": len(payload["follower_subtypes"]),
            "link_previews": len(payload["link_previews"]),
            "message_link_previews": len(payload["message_link_previews"]),
            "message_subtypes": len(payload["message_subtypes"]),
            "reactions": len(payload["reactions"]),
            "recipients": len(payload["recipients"]),
            "technical_rows": len(payload["technical_rows"]),
        }
        if relational_dispositions != expected_relational_dispositions:
            raise RuntimeError(
                "Collaboration relations lack exact dispositions: "
                f"{relational_dispositions} != {expected_relational_dispositions}",
            )
        evidence = {
            "schema": "usl-collaboration-disposition-v1",
            "source_dump_sha256": SOURCE_SHA256,
            "source_snapshot": self.source_snapshot,
            "counts": actual,
            "summary": {
                "complete": True,
                "dispositions": exact_dispositions,
                "relational_dispositions": relational_dispositions,
                "external_messages": 0,
                "deliberately_not_copied_messages": len(dropped_message_ids),
                "visible_messages": len(messages),
            },
            "visible_message_count": len(messages),
            "external_message_count": 0,
            "deliberately_not_copied_message_count": len(dropped_message_ids),
            "archives": archives,
            "outbound_side_effect_counts_before": side_effect_before,
            "outbound_side_effect_counts_after": side_effect_after,
            "outbound_side_effect_delta": {
                name: side_effect_after[name] - side_effect_before[name]
                for name in side_effect_models
            },
            "synthetic_accounting_attachment_notes_remaining": synthetic_notes_remaining,
            "dispositions": dispositions,
        }
        evidence_path = evidence_root / "collaboration-disposition.json"
        evidence_bytes = (json.dumps(evidence, default=str, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        temporary = evidence_path.with_suffix(".json.tmp")
        temporary.write_bytes(evidence_bytes)
        temporary.chmod(0o600)
        temporary.replace(evidence_path)
        digest = hashlib.sha256(evidence_bytes).hexdigest()
        evidence_path.with_suffix(".json.sha256").write_text(f"{digest}  {evidence_path.name}\n", encoding="utf-8")
        evidence_path.with_suffix(".json.sha256").chmod(0o600)
        statistics = {
            **actual,
            "visible_messages": len(messages),
            "external_messages": 0,
            "deliberately_not_copied_messages": len(dropped_message_ids),
            "archives": len(archives),
        }
        if (
            statistics["visible_messages"]
            != EXPECTED_MESSAGE_DISPOSITIONS["visible"]
            or statistics["external_messages"]
            != EXPECTED_MESSAGE_DISPOSITIONS["external"]
            or statistics["deliberately_not_copied_messages"]
            != EXPECTED_MESSAGE_DISPOSITIONS["deliberately_not_copied"]
        ):
            raise RuntimeError(f"Collaboration disposition baseline changed: {statistics}")
        self.write({
            "status": "passed", "finished_at": fields.Datetime.now(),
            "statistics_json": statistics, "evidence_sha256": digest,
        })
        return statistics

    def _resolve_message_target(
        self,
        row,
        record_maps,
        attachment_ids,
        document_attachment_ids,
        sign_request_by_attachment,
        sign_email_targets,
    ):
        model = row["model"]
        route = route_model(model)
        if route == "native":
            return self._traced(model, row["res_id"], model)
        if route in {"translated", "discuss"}:
            return record_maps.get(model, {}).get(row["res_id"])
        if not model:
            if target := sign_email_targets.get(row["id"]):
                return target
            sign_targets = {
                sign_request_by_attachment.get(source_id) for source_id in attachment_ids
            }
            sign_targets.discard(None)
            if len(sign_targets) == 1:
                return sign_targets.pop()
        if not model and attachment_ids:
            targets = {document_attachment_ids.get(source_id) for source_id in attachment_ids}
            targets.discard(None)
            if len(targets) == 1:
                return self.env["usl.document"].sudo().browse(targets.pop()).exists()
        return False

    def _restore_message(
        self, row, target, partners, companies, subtype_map, xmlids, recipient_ids, attachment_ids,
    ):
        existing = self._existing_message(row["id"])
        subtype = subtype_map.get(row["subtype_id"])
        activity_type = self._target_xmlid(xmlids, "mail.activity.type", row["mail_activity_type_id"])
        target_attachments = [
            attachment.id for source_id in attachment_ids
            if (attachment := self._traced("ir.attachment", source_id, "ir.attachment"))
        ]
        target_recipients = [partners[source_id].id for source_id in recipient_ids if partners.get(source_id)]
        values = {
            "model": target._name,
            "res_id": target.id,
            "subject": row["subject"],
            "message_type": row["message_type"] or "comment",
            "email_from": row["email_from"],
            "message_id": row["message_id"],
            "reply_to": row["reply_to"],
            "incoming_email_cc": row["incoming_email_cc"],
            "outgoing_email_to": row["outgoing_email_to"],
            "incoming_email_to": row["incoming_email_to"],
            "email_layout_xmlid": row["email_layout_xmlid"],
            "reply_to_force_new": row["reply_to_force_new"],
            "email_add_signature": row["email_add_signature"],
            "body": row["body"] or "",
            "is_internal": row["is_internal"],
            "date": row["date"],
            "author_id": partners[row["author_id"]].id if partners.get(row["author_id"]) else False,
            "subtype_id": subtype.id if subtype else False,
            "mail_activity_type_id": activity_type.id if activity_type else False,
            "record_company_id": companies[row["record_company_id"]].id if companies.get(row["record_company_id"]) else False,
            "partner_ids": [Command.set(target_recipients)],
            "attachment_ids": [Command.set(target_attachments)],
        }
        Message = self.env["mail.message"].sudo().with_context(
            tracking_disable=True, mail_create_nolog=True, mail_create_nosubscribe=True,
            mail_notify_force_send=False,
        )
        if existing:
            existing.write(values)
            message = existing
        else:
            message = Message.create(values)
        self._bind("mail.message", row["id"], message, row)
        return message

    def _restore_subtypes(self, payload, xmlids, dispositions):
        Subtype = self.env["mail.message.subtype"].sudo()
        result = {}
        for row in payload["message_subtypes"]:
            subtype = self._target_xmlid(xmlids, "mail.message.subtype", row["id"])
            source_model = row["res_model"]
            target_model = TRANSLATED_MODELS.get(source_model, source_model)
            supported = not source_model or route_model(source_model) in {
                "native", "translated", "discuss",
            }
            if not subtype and supported and (not target_model or target_model in self.env):
                candidates = Subtype.search([
                    ("res_model", "=", target_model or False),
                    ("name", "=", self._text(row["name"])),
                    ("internal", "=", bool(row["internal"])),
                ], limit=2)
                if len(candidates) > 1:
                    raise RuntimeError(
                        f"Message subtype {row['id']} has an ambiguous target semantic key",
                    )
                subtype = candidates
            if not subtype and supported and (not target_model or target_model in self.env):
                values = {
                    "name": self._text(row["name"]),
                    "description": self._text(row["description"]),
                    "res_model": target_model or False,
                    "sequence": row["sequence"],
                    "internal": bool(row["internal"]),
                    "default": bool(row["default"]),
                    "hidden": bool(row["hidden"]),
                    "track_recipients": bool(row["track_recipients"]),
                }
                if (
                    row["relation_field"] and target_model
                    and row["relation_field"] in self.env[target_model]._fields
                ):
                    values["relation_field"] = row["relation_field"]
                subtype = Subtype.create(values)
            if subtype:
                result[row["id"]] = subtype
                self._bind("mail.message.subtype", row["id"], subtype, row)
            dispositions["other"].append({
                "model": "mail.message.subtype", "id": row["id"],
                "disposition": "native" if subtype else "installed_module_or_private_archive",
                "target_id": subtype.id if subtype else None, "source": row,
            })
        for row in payload["message_subtypes"]:
            subtype = result.get(row["id"])
            parent = result.get(row["parent_id"])
            if subtype and parent and subtype.parent_id != parent:
                subtype.write({"parent_id": parent.id})
        return result

    def _tracking_display(self, row):
        info = row.get("field_info") or {}
        label = (info.get("description") or info.get("desc")) if isinstance(info, dict) else None
        label = label or row.get("field_name") or "Removed field"
        if self._is_sensitive_tracking(row):
            return f"{html.escape(label)}: <em>sensitive value changed; values retained in restricted evidence</em>"
        old = next((row.get(name) for name in (
            "old_value_char", "old_value_text", "old_value_datetime", "old_value_float", "old_value_integer",
        ) if row.get(name) not in (None, "")), "∅")
        new = next((row.get(name) for name in (
            "new_value_char", "new_value_text", "new_value_datetime", "new_value_float", "new_value_integer",
        ) if row.get(name) not in (None, "")), "∅")
        return f"{html.escape(label)}: {html.escape(str(old))} → {html.escape(str(new))}"

    @staticmethod
    def _is_sensitive_tracking(row):
        info = row.get("field_info") or {}
        label = " ".join(str(info.get(key) or "") for key in ("description", "desc", "name"))
        return bool(
            SENSITIVE_FIELD_PATTERN.search(row.get("field_name") or "")
            or "social security" in label.lower()
            or "sécurité sociale" in label.lower(),
        )

    def _restore_tracking(
        self,
        rows,
        messages,
        users,
        dispositions,
        dropped_message_ids,
    ):
        legacy = defaultdict(list)
        disposition_by_id = {}
        Tracking = self.env["mail.tracking.value"].sudo()
        value_fields = (
            "old_value_integer", "new_value_integer", "old_value_char", "new_value_char",
            "old_value_text", "new_value_text", "old_value_datetime", "new_value_datetime",
            "old_value_float", "new_value_float", "field_info",
        )
        for row in rows:
            message = messages.get(row["mail_message_id"])
            if not message:
                if row["mail_message_id"] not in dropped_message_ids:
                    raise RuntimeError(
                        "Tracking history has no canonical message and was not "
                        f"approved for exclusion: {row['id']}",
                    )
                disposition = {
                    "id": row["id"],
                    "disposition": "deliberately_not_copied",
                    "source_sha256": self._checksum(row),
                }
                dispositions["tracking"].append(disposition)
                disposition_by_id[row["id"]] = disposition
                continue
            field = self.env["ir.model.fields"].sudo()
            if (
                not self._is_sensitive_tracking(row)
                and message.model == row.get("field_model")
                and row.get("field_name")
            ):
                field = field.search([
                    ("model", "=", message.model), ("name", "=", row["field_name"]),
                    ("ttype", "=", row.get("field_type")),
                ], limit=1)
            if not field:
                legacy[row["mail_message_id"]].append(row)
                disposition = {
                    "id": row["id"], "disposition": "visible_legacy_note", "source": row,
                }
                dispositions["tracking"].append(disposition)
                disposition_by_id[row["id"]] = disposition
                continue
            existing = self._bound_record("mail.tracking.value", row["id"], "mail.tracking.value")
            if not existing:
                traced = self._traced("mail.tracking.value", row["id"], "mail.tracking.value")
                existing = traced if traced else Tracking
            values = {"mail_message_id": message.id, "field_id": field.id, **{name: row.get(name) for name in value_fields}}
            tracking = existing
            if tracking:
                tracking.write(values)
            else:
                tracking = Tracking.create(values)
            self._bind("mail.tracking.value", row["id"], tracking, row)
            disposition = {
                "id": row["id"], "disposition": "native_tracking",
                "target_tracking_id": tracking.id, "source": row,
            }
            dispositions["tracking"].append(disposition)
            disposition_by_id[row["id"]] = disposition
        for source_message_id, values in legacy.items():
            parent = messages[source_message_id]
            existing = self._bound_record("mail.tracking.legacy.note", source_message_id, "mail.message")
            body = "<p><strong>Legacy field changes</strong></p><ul>" + "".join(
                f"<li>{self._tracking_display(row)}</li>" for row in values
            ) + "</ul>"
            vals = {
                "model": parent.model, "res_id": parent.res_id, "parent_id": parent.id,
                "author_id": parent.author_id.id, "date": parent.date, "body": body,
                "message_type": "comment", "subtype_id": self.env.ref("mail.mt_note").id,
                "is_internal": True,
            }
            if existing:
                existing.write(vals)
                note = existing
            else:
                note = self.env["mail.message"].sudo().with_context(
                    tracking_disable=True, mail_create_nolog=True, mail_create_nosubscribe=True,
                ).create(vals)
            self._bind("mail.tracking.legacy.note", source_message_id, note, values)
            for row in values:
                disposition_by_id[row["id"]]["target_legacy_note_id"] = note.id

    def _restore_followers(
        self, payload, record_maps, partners, active_internal_partners, subtype_map, dispositions,
    ):
        subtypes = defaultdict(list)
        for row in payload["follower_subtypes"]:
            subtype = subtype_map.get(row["subtype_id"])
            if subtype:
                subtypes[row["follower_id"]].append(subtype.id)
        for row in payload["followers"]:
            target = self._resolve_business_target(row["res_model"], row["res_id"], record_maps)
            partner = partners.get(row["partner_id"])
            if not target or not partner or row["partner_id"] not in active_internal_partners:
                if row["res_model"] in EXTERNAL_ARCHIVE_MODELS:
                    dispositions["followers"].append({
                        "id": row["id"],
                        "disposition": "deliberately_not_copied",
                        "source_sha256": self._checksum(row),
                    })
                else:
                    dispositions["followers"].append({
                        "id": row["id"],
                        "disposition": "archive_not_subscribed",
                        "source": row,
                    })
                continue
            existing = self._bound_record("mail.followers", row["id"], "mail.followers")
            if not existing:
                existing = self.env["mail.followers"].sudo().search([
                    ("res_model", "=", target._name), ("res_id", "=", target.id),
                    ("partner_id", "=", partner.id),
                ], limit=1)
            values = {
                "res_model": target._name, "res_id": target.id, "partner_id": partner.id,
                "subtype_ids": [Command.set(subtypes[row["id"]])],
            }
            if existing:
                existing.write(values)
                follower = existing
            else:
                follower = self.env["mail.followers"].sudo().create(values)
            self._bind("mail.followers", row["id"], follower, row)
            dispositions["followers"].append({
                "id": row["id"], "disposition": "live_internal_subscription",
                "target_follower_id": follower.id, "source": row,
            })

    def _resolve_business_target(self, model, source_id, record_maps):
        if model in DIRECT_MODELS:
            return self._traced(model, source_id, model)
        return record_maps.get(model, {}).get(source_id)

    def _activity_type(self, row, types, xmlids, target_model):
        target = self._target_xmlid(xmlids, "mail.activity.type", row["activity_type_id"])
        if target:
            return target
        source = types[row["activity_type_id"]]
        name = self._text(source["name"])
        semantic_model = target_model if source["res_model"] else False
        existing = self.env["mail.activity.type"].sudo().search([
            ("name", "=", name), ("res_model", "=", semantic_model),
        ], limit=2)
        if len(existing) > 1:
            raise RuntimeError(
                f"Activity type {name!r}/{source['res_model']!r} is ambiguous in the target",
            )
        if existing:
            return existing
        return self.env["mail.activity.type"].sudo().create({
            "name": name, "summary": self._text(source["summary"]),
            "category": source["category"], "icon": source["icon"],
            "decoration_type": source["decoration_type"], "delay_count": source["delay_count"],
            "delay_unit": source["delay_unit"], "delay_from": source["delay_from"],
            "res_model": semantic_model, "active": source["active"],
        })

    def _restore_activities(self, payload, record_maps, users, xmlids, dispositions):
        types = {row["id"]: row for row in payload["activity_types"]}
        roger = users.get(7)
        if not roger:
            message = "Source user 7 (Roger) has no unique target identity"
            raise RuntimeError(message)
        for row in payload["activities"]:
            target = self._resolve_business_target(row["res_model"], row["res_id"], record_maps)
            if row["res_model"] == "sign.request" and not row["active"]:
                if target:
                    creator = users.get(row["create_uid"])
                    body = (
                        "<p><strong>Completed legacy signing activity</strong></p>"
                        + (row["note"] or "")
                    )
                    if row["feedback"]:
                        body += f"<p><strong>Feedback:</strong> {html.escape(row['feedback'])}</p>"
                    values = {
                        "model": target._name,
                        "res_id": target.id,
                        "body": body,
                        "subject": row["summary"],
                        "message_type": "comment",
                        "subtype_id": self.env.ref("mail.mt_note").id,
                        "date": row["date_done"] or row["write_date"] or row["create_date"],
                        "is_internal": True,
                    }
                    if creator and creator.partner_id:
                        values["author_id"] = creator.partner_id.id
                    note = self._bound_record(
                        "mail.activity.legacy.note", row["id"], "mail.message",
                    )
                    if note:
                        note.write(values)
                    else:
                        note = self.env["mail.message"].sudo().with_context(
                            tracking_disable=True,
                            mail_create_nolog=True,
                            mail_create_nosubscribe=True,
                        ).create(values)
                    self._bind("mail.activity.legacy.note", row["id"], note, row)
                    dispositions["activities"].append({
                        "id": row["id"],
                        "disposition": "completed_sign_note",
                        "target_message_id": note.id,
                        "source": row,
                    })
                else:
                    dispositions["activities"].append({
                        "id": row["id"],
                        "disposition": "external_archive",
                        "source": row,
                    })
                continue
            if row["res_model"] == "account.journal" and row["active"]:
                if target:
                    values = {
                        "model": target._name, "res_id": target.id,
                        "body": ("<p><strong>Legacy activity (not actionable)</strong></p>" + (row["note"] or "")
                                 + "<p>The source reminder depended on the retired Enterprise bank-consent workflow.</p>"),
                        "subject": row["summary"], "message_type": "comment",
                        "subtype_id": self.env.ref("mail.mt_note").id,
                        "date": row["create_date"], "is_internal": True,
                    }
                    note = self._bound_record("mail.activity.legacy.note", row["id"], "mail.message")
                    if note:
                        note.write(values)
                    else:
                        note = self.env["mail.message"].sudo().with_context(
                            tracking_disable=True, mail_create_nolog=True, mail_create_nosubscribe=True,
                        ).create(values)
                    self._bind("mail.activity.legacy.note", row["id"], note, row)
                dispositions["activities"].append({
                    "id": row["id"], "disposition": "obsolete_bank_note", "source": row,
                })
                continue
            if not target or row["res_model"] == "sign.request":
                dispositions["activities"].append({
                    "id": row["id"], "disposition": "external_archive", "source": row,
                })
                continue
            user = users.get(row["user_id"]) if row["user_id"] else (roger if row["res_model"] == "project.task" else False)
            activity_type = self._activity_type(row, types, xmlids, target._name)
            if not user or not activity_type:
                dispositions["activities"].append({
                    "id": row["id"], "disposition": "external_archive", "source": row,
                })
                continue
            note = row["note"] or False
            if not row["user_id"]:
                note = (note or "") + "<p><em>The source activity had no assignee. It is assigned to Roger, its source creator, for review.</em></p>"
            existing = self._bound_record("mail.activity", row["id"], "mail.activity")
            if not existing:
                traced = self._traced("mail.activity", row["id"], "mail.activity")
                existing = traced if traced else self.env["mail.activity"]
            values = {
                "res_model_id": self.env["ir.model"]._get_id(target._name), "res_id": target.id,
                "activity_type_id": activity_type.id, "user_id": user.id,
                "summary": row["summary"], "date_deadline": row["date_deadline"],
                "note": note, "feedback": row["feedback"], "automated": row["automated"],
            }
            # mail.activity.create()/write() normally notifies a newly assigned
            # user and may enqueue an email.  Restoration must preserve the
            # work item without replaying its historical assignment event.
            Activity = self.env["mail.activity"].sudo().with_context(
                mail_activity_quick_update=True,
                mail_notify_force_send=False,
                mail_create_nosubscribe=True,
            )
            if existing:
                existing.with_context(
                    mail_activity_quick_update=True,
                    mail_notify_force_send=False,
                    mail_create_nosubscribe=True,
                ).sudo().write(values)
                activity = existing
            else:
                activity = Activity.create(values)
            self._bind("mail.activity", row["id"], activity, row)
            self._stamp_audit(activity, row, users)
            if not row["active"]:
                self.env.cr.execute("UPDATE mail_activity SET active=FALSE, date_done=%s WHERE id=%s", (row["date_done"], activity.id))
                activity.invalidate_recordset(["active", "date_done"])
            dispositions["activities"].append({
                "id": row["id"], "disposition": "native_activity",
                "target_activity_id": activity.id, "target_user_id": user.id, "source": row,
            })

    def _restore_channels(self, payload, xmlids, partners):
        result = {}
        # install_mode suppresses discuss.channel.create()'s normal behaviour of
        # adding the current operator.  A restore must reproduce the exact
        # source participant set, including one-person historical chats.
        Channel = self.env["discuss.channel"].sudo().with_context(
            active_test=False,
            install_mode=True,
        )
        source_members = defaultdict(set)
        source_groups = defaultdict(list)
        for row in payload["channel_members"]:
            if partners.get(row["partner_id"]):
                source_members[row["channel_id"]].add(partners[row["partner_id"]].id)
        for row in payload["channel_groups"]:
            group = self._target_xmlid(xmlids, "res.groups", row["group_id"])
            if not group:
                raise RuntimeError(
                    f"Discuss channel group {row['group_id']} has no target XML ID",
                )
            source_groups[row["channel_id"]].append(group.id)
        for row in payload["channels"]:
            existing = self._bound_record("discuss.channel", row["id"], "discuss.channel")
            if not existing:
                existing = self._target_xmlid(xmlids, "discuss.channel", row["id"])
            if not existing and row["channel_type"] == "chat":
                candidates = Channel.search([("channel_type", "=", "chat")])
                existing = candidates.filtered(lambda channel: set(channel.channel_member_ids.partner_id.ids) == source_members[row["id"]])[:1]
            values = {
                "name": row["name"], "channel_type": row["channel_type"],
                "description": row["description"], "active": row["active"],
            }
            public_group = self._target_xmlid(xmlids, "res.groups", row["group_public_id"])
            if row["channel_type"] == "channel":
                if row["group_public_id"] and not public_group:
                    raise RuntimeError(
                        f"Discuss public group {row['group_public_id']} has no target XML ID",
                    )
                values["group_public_id"] = public_group.id if public_group else False
                values["group_ids"] = [Command.set(source_groups[row["id"]])]
            elif row["channel_type"] == "chat" and not existing:
                # Odoo only permits chat members to be created atomically with
                # the chat.  Adding the second member afterwards is rejected by
                # discuss.channel.member.create(), even under sudo.  Supplying
                # the exact mapped source participant set also prevents the
                # current migration user from leaking into a restored chat.
                values["channel_member_ids"] = [
                    Command.create({"partner_id": partner_id})
                    for partner_id in sorted(source_members[row["id"]])
                ]
            if "is_readonly" in Channel._fields:
                values["is_readonly"] = row["is_readonly"]
            if existing:
                existing.write(values)
                channel = existing
            else:
                channel = Channel.create(values)
            result[row["id"]] = channel
            self._bind("discuss.channel", row["id"], channel, row)
        for row in payload["channels"]:
            channel = result[row["id"]]
            parent = result.get(row["parent_channel_id"])
            if parent and channel.parent_channel_id != parent:
                channel.write({"parent_channel_id": parent.id})
        return result

    def _finish_channel_members(self, payload, channels, messages, partners):
        Member = self.env["discuss.channel.member"].sudo()
        expected_partners = defaultdict(set)
        for channel in channels.values():
            expected_partners[channel.id]
        for row in payload["channel_members"]:
            channel = channels.get(row["channel_id"])
            partner = partners.get(row["partner_id"])
            if not channel or not partner:
                continue
            expected_partners[channel.id].add(partner.id)
            member = self._bound_record("discuss.channel.member", row["id"], Member._name)
            if not member:
                member = Member.search([("channel_id", "=", channel.id), ("partner_id", "=", partner.id)], limit=1)
            values = {"channel_id": channel.id, "partner_id": partner.id}
            for name in ("custom_notifications", "channel_role", "is_favorite"):
                if name in Member._fields:
                    values[name] = row[name]
            seen = messages.get(row["seen_message_id"])
            separator = messages.get(row["new_message_separator"])
            if seen:
                values["seen_message_id"] = seen.id
            if separator and "new_message_separator" in Member._fields:
                values["new_message_separator"] = separator.id
            if member:
                member.write(values)
            else:
                member = Member.create(values)
            self._bind("discuss.channel.member", row["id"], member, row)
        for channel_id, partner_ids in expected_partners.items():
            extras = Member.search([
                ("channel_id", "=", channel_id), ("partner_id", "not in", list(partner_ids)),
            ])
            extras.unlink()

    def _restore_reactions_previews(self, payload, messages, partners, dispositions):
        Reaction = self.env["mail.message.reaction"].sudo()
        for row in payload["reactions"]:
            message = messages.get(row["message_id"])
            partner = partners.get(row["partner_id"])
            if not message or not partner:
                dispositions["other"].append({
                    "model": "mail.message.reaction", "id": row["id"],
                    "disposition": "external_archive", "source": row,
                })
                continue
            reaction = self._bound_record("mail.message.reaction", row["id"], Reaction._name)
            values = {"message_id": message.id, "partner_id": partner.id, "content": row["content"]}
            if reaction:
                reaction.write(values)
            else:
                reaction = Reaction.search([
                    ("message_id", "=", message.id), ("partner_id", "=", partner.id),
                    ("content", "=", row["content"]),
                ], limit=1) or Reaction.create(values)
            self._bind("mail.message.reaction", row["id"], reaction, row)
            dispositions["other"].append({
                "model": "mail.message.reaction", "id": row["id"],
                "disposition": "native", "target_id": reaction.id, "source": row,
            })
        previews = {}
        Preview = self.env["mail.link.preview"].sudo()
        for row in payload["link_previews"]:
            preview = Preview.search([("source_url", "=", row["source_url"])], limit=1)
            values = {name: row[name] for name in (
                "source_url", "og_type", "og_title", "og_site_name", "og_image",
                "og_mimetype", "image_mimetype", "og_description",
            )}
            if preview:
                preview.write(values)
            else:
                preview = Preview.create(values)
            previews[row["id"]] = preview
            self._bind("mail.link.preview", row["id"], preview, row)
            dispositions["other"].append({
                "model": "mail.link.preview", "id": row["id"],
                "disposition": "native", "target_id": preview.id, "source": row,
            })
        Link = self.env["mail.message.link.preview"].sudo()
        for row in payload["message_link_previews"]:
            message = messages.get(row["message_id"])
            preview = previews.get(row["link_preview_id"])
            if not message or not preview:
                dispositions["other"].append({
                    "model": "mail.message.link.preview", "id": row["id"],
                    "disposition": "private_archive", "source": row,
                })
                continue
            link = self._bound_record("mail.message.link.preview", row["id"], Link._name)
            values = {"message_id": message.id, "link_preview_id": preview.id, "sequence": row["sequence"], "is_hidden": row["is_hidden"]}
            if link:
                link.write(values)
            else:
                link = Link.search([("message_id", "=", message.id), ("link_preview_id", "=", preview.id)], limit=1) or Link.create(values)
            self._bind("mail.message.link.preview", row["id"], link, row)
            dispositions["other"].append({
                "model": "mail.message.link.preview", "id": row["id"],
                "disposition": "native", "target_id": link.id, "source": row,
            })

    def _restore_aliases(self, payload, record_maps, dispositions):
        Alias = self.env["mail.alias"].sudo()
        for row in payload["aliases"]:
            source_model = row["alias_model"]
            parent = self._resolve_business_target(
                row["alias_parent_model"], row["alias_parent_thread_id"], record_maps,
            ) if row["alias_parent_model"] and row["alias_parent_thread_id"] else False
            forced = self._resolve_business_target(
                source_model, row["alias_force_thread_id"], record_maps,
            ) if row["alias_force_thread_id"] else False
            model = self.env["ir.model"].sudo().search([("model", "=", source_model)], limit=1)
            alias_name = row["alias_name"] or False
            supported = source_model in DIRECT_MODELS and source_model in self.env
            if not supported or not model or not alias_name:
                dispositions["other"].append({
                    "model": "mail.alias", "id": row["id"],
                    "disposition": "private_archive" if alias_name else "recomputed_disabled_alias",
                    "source": row,
                })
                continue
            company = parent.company_id if parent and "company_id" in parent._fields else self.env.company
            domain = company.alias_domain_id or self.env.company.alias_domain_id
            if not domain:
                configured_name = os.environ.get("COLLABORATION_TARGET_MAIL_DOMAIN", "").strip().lower()
                if not configured_name:
                    raise RuntimeError(
                        f"No target mail domain can receive source alias {row['id']}",
                    )
                if configured_name == (row.get("source_alias_domain") or "").strip().lower():
                    message = (
                        "COLLABORATION_TARGET_MAIL_DOMAIN must not reuse the "
                        "source Odoo Online domain"
                    )
                    raise RuntimeError(message)
                candidates = self.env["mail.alias.domain"].sudo().search([
                    ("name", "=", configured_name),
                ], limit=2)
                if len(candidates) > 1:
                    raise RuntimeError(f"Target mail domain {configured_name!r} is ambiguous")
                domain = candidates or self.env["mail.alias.domain"].sudo().create({
                    "name": configured_name,
                })
                if not company.alias_domain_id:
                    company.sudo().write({"alias_domain_id": domain.id})
            if domain.name == (row.get("source_alias_domain") or ""):
                raise RuntimeError(
                    f"Target alias {row['id']} would retain the source Odoo Online domain",
                )
            alias = self._traced("mail.alias", row["id"], "mail.alias")
            if not alias and parent and "alias_id" in parent._fields:
                alias = parent.alias_id
            if not alias:
                alias = Alias.search([
                    ("alias_name", "=", alias_name), ("alias_domain_id", "=", domain.id),
                ], limit=1)
            values = {
                "alias_name": alias_name,
                "alias_domain_id": domain.id,
                "alias_model_id": model.id,
                "alias_contact": "partners" if row["alias_contact"] == "employees" else row["alias_contact"],
                "alias_incoming_local": bool(row["alias_incoming_local"]),
            }
            if forced:
                values["alias_force_thread_id"] = forced.id
            if parent:
                values.update({
                    "alias_parent_model_id": self.env["ir.model"]._get_id(parent._name),
                    "alias_parent_thread_id": parent.id,
                })
            if alias:
                alias.write(values)
            else:
                values["alias_defaults"] = "{}"
                alias = Alias.create(values)
            self._bind("mail.alias", row["id"], alias, row)
            dispositions["other"].append({
                "model": "mail.alias", "id": row["id"], "disposition": "target_domain_alias",
                "target_id": alias.id, "target_local_part": alias.alias_name,
                "target_domain": domain.name, "source": row,
            })

    def _record_discuss_dispositions(self, payload, channels, dispositions):
        for row in payload["channels"]:
            channel = channels.get(row["id"])
            dispositions["other"].append({
                "model": "discuss.channel", "id": row["id"],
                "disposition": "native" if channel else "private_archive",
                "target_id": channel.id if channel else None, "source": row,
            })
        for row in payload["channel_members"]:
            member = self._bound_record("discuss.channel.member", row["id"], "discuss.channel.member")
            dispositions["other"].append({
                "model": "discuss.channel.member", "id": row["id"],
                "disposition": "native" if member else "private_archive",
                "target_id": member.id if member else None, "source": row,
            })
        for row in payload["channel_groups"]:
            group = self._target_xmlid(
                {(item["model"], item["res_id"]): item["xmlid"] for item in payload["xmlids"]},
                "res.groups", row["group_id"],
            )
            channel = channels.get(row["channel_id"])
            dispositions["other"].append({
                "model": "discuss.channel.group", "id": f"{row['channel_id']}:{row['group_id']}",
                "disposition": "native" if channel and group and group in channel.group_ids else "private_archive",
                "source": row,
            })

    def _record_attachment_recipient_dispositions(
        self,
        payload,
        messages,
        partners,
        document_attachment_ids,
        subtype_map,
        dispositions,
        dropped_message_ids,
    ):
        for row in payload["message_attachment_details"]:
            message = messages.get(row["message_id"])
            attachment = self._traced("ir.attachment", row["attachment_id"], "ir.attachment")
            canonical_document = document_attachment_ids.get(row["attachment_id"])
            if message and attachment and attachment in message.attachment_ids:
                if row["checksum"] and attachment.checksum != row["checksum"]:
                    raise RuntimeError(
                        f"Attachment checksum changed for source attachment {row['attachment_id']}",
                    )
                outcome = "native_message_attachment"
                target = {"target_attachment_id": attachment.id}
            elif message and canonical_document:
                outcome = "canonical_document_payload"
                target = {"target_document_id": canonical_document}
            else:
                outcome = (
                    "deliberately_not_copied"
                    if row["message_id"] in dropped_message_ids
                    else "unresolved_attachment_relationship"
                )
                target = {}
            dispositions["other"].append({
                "model": "message_attachment_rel",
                "id": f"{row['message_id']}:{row['attachment_id']}",
                "disposition": outcome,
                "source_sha256": self._checksum(row),
                **target,
            })
        for row in payload["recipients"]:
            message = messages.get(row["message_id"])
            partner = partners.get(row["partner_id"])
            dispositions["other"].append({
                "model": "mail.message.recipient",
                "id": f"{row['message_id']}:{row['partner_id']}",
                "disposition": (
                    "native"
                    if message and partner
                    else "deliberately_not_copied"
                    if row["message_id"] in dropped_message_ids
                    else "unresolved_recipient_relationship"
                ),
                "target_message_id": message.id if message else None,
                "target_partner_id": partner.id if partner else None,
            })
        for row in payload["follower_subtypes"]:
            follower = self._bound_record("mail.followers", row["follower_id"], "mail.followers")
            subtype = subtype_map.get(row["subtype_id"])
            dispositions["other"].append({
                "model": "mail.follower.subtype", "id": f"{row['follower_id']}:{row['subtype_id']}",
                "disposition": "native" if follower and subtype and subtype in follower.subtype_ids else "private_archive",
                "source": row,
            })
        for row in payload["activity_types"]:
            dispositions["other"].append({
                "model": "mail.activity.type", "id": row["id"],
                "disposition": "xmlid_or_semantic_recompute", "source": row,
            })

    def _technical_row_disposition(self, row, xmlids):
        table = row["table"]
        source = row["source"]
        # Odoo's psycopg JSON adapter can return jsonb composites as their raw
        # JSON string.  Normalize that representation before classifying it;
        # scalar JSON remains wrapped so it still receives one exact outcome.
        if isinstance(source, str):
            try:
                source = json.loads(source)
            except json.JSONDecodeError:
                source = {"raw": source}
        if not isinstance(source, dict):
            source = {"value": source}
        model = table.replace("_", ".")
        disposition = route_technical_table(table)
        target_xmlid = None
        if table == "mail_alias_domain":
            disposition = "target_domain_recompute"
        elif disposition == "xmlid_or_installed_module_recompute":
            target_xmlid = xmlids.get((model, source.get("id")))
            target = self.env.ref(target_xmlid, raise_if_not_found=False) if target_xmlid else False
            disposition = "installed_module_recompute" if target else "private_archive"
        result = {
            "model": model,
            "id": source.get("id") or self._checksum(source),
            "disposition": disposition,
            "source_table": table,
            "source_sha256": self._checksum(source),
        }
        if target_xmlid:
            result["target_xmlid"] = target_xmlid
        if result["disposition"] == "private_archive":
            result["source"] = source
        return result

    def _remove_synthetic_accounting_notes(self, source_messages):
        originals = self.env["mail.message"].sudo().browse([message.id for message in source_messages.values()])
        source_attachment_ids = originals.attachment_ids.ids
        if not source_attachment_ids:
            return
        generated = self.env["mail.message"].sudo().search([
            ("id", "not in", originals.ids),
            ("model", "in", ["account.move", "hr.expense"]),
            ("attachment_ids", "in", source_attachment_ids),
            ("body", "in", [
                "Supporting file restored from the source record's chatter.",
                "<p>Supporting file restored from the source record's chatter.</p>",
            ]),
        ])
        generated.unlink()
