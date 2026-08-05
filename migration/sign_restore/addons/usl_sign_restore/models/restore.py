import base64
import hashlib
import hmac
import json
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras

from odoo import Command, fields, models
from odoo.exceptions import ValidationError


FIELD_XMLIDS = {
    "signature": "sign_oca.sign_field_signature",
    "initial": "usl_sign.field_initials",
    "initials": "usl_sign.field_initials",
    "name": "sign_oca.sign_field_name",
    "email": "sign_oca.sign_field_email",
    "phone": "sign_oca.sign_field_phone",
    "text": "sign_oca.sign_field_text",
    "textarea": "sign_oca.sign_field_text",
    "date": "usl_sign.field_date",
    "company": "usl_sign.field_company",
    "checkbox": "sign_oca.sign_field_check",
}


def source_text(value):
    if isinstance(value, dict):
        return value.get("en_US") or value.get("fr_FR") or next(iter(value.values()), "")
    return value or ""


def binary_sha256(value):
    return hashlib.sha256(value).hexdigest()


def request_fingerprint(original_sha256, final_sha256, name, completed_at, signers):
    payload = {
        "completed_at": str(completed_at or ""),
        "final": final_sha256,
        "name": name or "",
        "original": original_sha256,
        "signers": sorted((email or "").strip().lower() for email in signers),
    }
    return binary_sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )


class SignSourceReader:
    def __init__(self, options):
        self.options = options
        self.filestore = Path(options["filestore"])

    def _query(self, cursor, sql, params=()):
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def read(self):
        connection = psycopg2.connect(
            host=self.options["host"],
            port=self.options["port"],
            user=self.options["user"],
            password=self.options["password"],
            dbname=self.options["database"],
        )
        connection.set_session(readonly=True, autocommit=True)
        try:
            with connection.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            ) as cursor:
                templates = self._query(
                    cursor,
                    """
                    SELECT t.*, d.id AS document_id, d.num_pages,
                           a.id AS attachment_id, a.name AS attachment_name,
                           a.mimetype, a.store_fname, a.checksum, a.file_size,
                           COALESCE(r.communication_company_id, u.company_id) AS company_id,
                           im.model
                      FROM sign_template t
                      JOIN sign_document d ON d.template_id = t.id
                      JOIN ir_attachment a ON a.id = d.attachment_id
                 LEFT JOIN res_users u ON u.id = t.user_id
                 LEFT JOIN LATERAL (
                           SELECT communication_company_id
                             FROM sign_request sr
                            WHERE sr.template_id = t.id
                              AND sr.communication_company_id IS NOT NULL
                         ORDER BY sr.id LIMIT 1
                           ) r ON TRUE
                 LEFT JOIN ir_model im ON im.id = t.model_id
                  ORDER BY t.id, d.sequence, d.id
                    """,
                )
                items = self._query(
                    cursor,
                    """
                    SELECT i.*, d.template_id, typ.item_type,
                           role.name AS role_name
                      FROM sign_item i
                      JOIN sign_document d ON d.id = i.document_id
                      JOIN sign_item_type typ ON typ.id = i.type_id
                 LEFT JOIN sign_item_role role ON role.id = i.responsible_id
                  ORDER BY i.id
                    """,
                )
                requests = self._query(
                    cursor,
                    """
                    SELECT r.*, u.login AS responsible_login
                      FROM sign_request r
                 LEFT JOIN res_users u ON u.id = r.create_uid
                  ORDER BY r.id
                    """,
                )
                signers = self._query(
                    cursor,
                    """
                    SELECT s.*, p.name AS partner_name, p.email AS partner_email,
                           p.phone AS partner_phone,
                           role.name AS role_name
                      FROM sign_request_item s
                 LEFT JOIN res_partner p ON p.id = s.partner_id
                 LEFT JOIN sign_item_role role ON role.id = s.role_id
                  ORDER BY s.sign_request_id, s.mail_sent_order, s.id
                    """,
                )
                completed = self._query(
                    cursor,
                    """
                    SELECT rel.sign_request_id, a.id AS attachment_id,
                           a.name, a.mimetype, a.store_fname, a.checksum,
                           a.file_size
                      FROM sign_request_completed_document_rel rel
                      JOIN ir_attachment a ON a.id = rel.ir_attachment_id
                  ORDER BY rel.sign_request_id, a.id
                    """,
                )
                logs = self._query(
                    cursor,
                    """
                    SELECT sign_request_id, sign_request_item_id, partner_id,
                           action, request_state, ip, latitude, longitude,
                           log_date, create_date
                      FROM sign_log
                  ORDER BY sign_request_id, log_date, id
                    """,
                )
                companies = self._query(
                    cursor,
                    """
                    SELECT c.id, p.name, p.vat
                      FROM res_company c JOIN res_partner p ON p.id = c.partner_id
                    """,
                )
        finally:
            connection.close()
        return {
            "templates": templates,
            "items": items,
            "requests": requests,
            "signers": signers,
            "completed": completed,
            "logs": logs,
            "companies": companies,
        }

    def binary(self, attachment):
        relative = attachment.get("store_fname")
        if not relative:
            raise ValidationError(
                f"Source attachment {attachment['attachment_id']} has no filestore object."
            )
        path = (self.filestore / relative).resolve()
        root = self.filestore.resolve()
        if root not in path.parents or not path.is_file():
            raise ValidationError(
                f"Source attachment {attachment['attachment_id']} is missing."
            )
        data = path.read_bytes()
        if attachment.get("file_size") is not None and len(data) != attachment["file_size"]:
            raise ValidationError(
                f"Source attachment {attachment['attachment_id']} has an invalid size."
            )
        if attachment.get("checksum") and hashlib.sha1(data).hexdigest() != attachment["checksum"]:
            raise ValidationError(
                f"Source attachment {attachment['attachment_id']} failed its checksum."
            )
        return data


class UslSignRestoreIssue(models.Model):
    _name = "usl.sign.restore.issue"
    _description = "USL Sign Restoration Issue"
    _order = "severity desc, id"

    run_id = fields.Many2one("usl.sign.restore.run", required=True, ondelete="cascade")
    severity = fields.Selection(
        [("warning", "Warning"), ("error", "Error")], required=True
    )
    source_kind = fields.Char(required=True)
    source_reference = fields.Char()
    description = fields.Text(required=True)
    resolved = fields.Boolean()


class UslSignRestoreRun(models.Model):
    _name = "usl.sign.restore.run"
    _description = "USL Sign Restoration Run"
    _order = "started_at desc, id desc"

    name = fields.Char(required=True, default="Sign restoration")
    status = fields.Selection(
        [("running", "Running"), ("passed", "Passed"), ("failed", "Failed")],
        required=True,
        default="running",
    )
    source_database = fields.Char(required=True)
    source_snapshot = fields.Char(required=True)
    target_database = fields.Char(required=True)
    started_at = fields.Datetime(required=True, default=fields.Datetime.now)
    finished_at = fields.Datetime()
    statistics_json = fields.Json(readonly=True)
    issue_ids = fields.One2many("usl.sign.restore.issue", "run_id", readonly=True)
    issue_count = fields.Integer(compute="_compute_issue_count")

    def _compute_issue_count(self):
        for run in self:
            run.issue_count = len(run.issue_ids)

    def _issue(self, kind, reference, description, severity="error"):
        return self.env["usl.sign.restore.issue"].create(
            {
                "run_id": self.id,
                "severity": severity,
                "source_kind": kind,
                "source_reference": str(reference or ""),
                "description": description,
            }
        )

    def _company_map(self, payload):
        result = {}
        for row in payload["companies"]:
            domain = [("vat", "=", row["vat"])] if row.get("vat") else [("name", "=", source_text(row["name"]))]
            matches = self.env["res.company"].sudo().search(domain)
            if len(matches) == 1:
                result[row["id"]] = matches
            else:
                self._issue("company", row["id"], "Target company mapping is missing or ambiguous.")
        return result

    def _role(self, name):
        name = source_text(name) or "Signer"
        role = self.env["sign.oca.role"].sudo().search([("name", "=", name)], limit=1)
        return role or self.env["sign.oca.role"].sudo().create(
            {"name": name, "domain": "[]", "partner_selection_policy": "empty"}
        )

    def _partner(self, signer):
        email = (signer.get("signer_email") or signer.get("partner_email") or "").strip().lower()
        if not email:
            self._issue("signer", signer["id"], "Signer has no email address.")
            return self.env["res.partner"]
        partners = self.env["res.partner"].sudo().search([("email", "=ilike", email)])
        if len(partners) > 1:
            exact = partners.filtered(lambda p: (p.email or "").strip().lower() == email)
            partners = exact
        if len(partners) == 1:
            return partners
        if partners:
            self._issue("signer", signer["id"], "Signer email maps to multiple target contacts.")
            return self.env["res.partner"]
        return self.env["res.partner"].sudo().create(
            {
                "name": source_text(signer.get("partner_name")) or email,
                "email": email,
                "phone": signer.get("partner_phone"),
                "mobile": signer.get("sms_number"),
            }
        )

    def _user(self, login):
        return self.env["res.users"].sudo().search([("login", "=", login)], limit=1) or self.env.user

    def _template_items(self, rows):
        commands = []
        review = []
        for row in rows:
            item_type = row["item_type"]
            xmlid = FIELD_XMLIDS.get(item_type)
            field = self.env.ref(xmlid, raise_if_not_found=False) if xmlid else False
            values = [row.get(key) for key in ("posX", "posY", "width", "height")]
            valid = field and row.get("page", 0) > 0 and all(
                value is not None and 0 <= value <= 1 for value in values
            )
            if not valid:
                review.append(f"Unsupported or invalid source field {row['id']} ({item_type}).")
                continue
            commands.append(
                Command.create(
                    {
                        "field_id": field.id,
                        "role_id": self._role(row.get("role_name")).id,
                        "required": bool(row.get("required")),
                        "page": row["page"],
                        "position_x": row["posX"] * 100,
                        "position_y": row["posY"] * 100,
                        "width": row["width"] * 100,
                        "height": row["height"] * 100,
                        "placeholder": source_text(row.get("name")),
                    }
                )
            )
        return commands, review

    def _restore_templates(self, payload, reader, company_map):
        items = defaultdict(list)
        for row in payload["items"]:
            items[row["template_id"]].append(row)
        result = {}
        for row in payload["templates"]:
            company = company_map.get(row["company_id"])
            if not company:
                continue
            data = reader.binary(row)
            digest = binary_sha256(data)
            name = source_text(row["name"])
            template = self.env["sign.oca.template"].sudo().with_context(active_test=False).search(
                [("company_id", "=", company.id), ("name", "=", name), ("document_sha256", "=", digest)],
                limit=1,
            )
            commands, review = self._template_items(items[row["id"]])
            if not template:
                policy = self.env["usl.sign.policy"].sudo().search(
                    [("company_id", "=", company.id), ("is_default", "=", True)], limit=1
                )
                template = self.env["sign.oca.template"].sudo().create(
                    {
                        "name": name,
                        "data": base64.b64encode(data),
                        "filename": row.get("attachment_name") or f"{name}.pdf",
                        "company_id": company.id,
                        "policy_id": policy.id,
                        "expiration_days": row.get("signature_request_validity") or 30,
                        "active": bool(row.get("active")) and not review,
                        "preparation_status": "review_required" if review else "ready",
                        "preparation_note": " ".join(review) or False,
                        "item_ids": commands,
                    }
                )
            result[row["id"]] = template
            for note in review:
                self._issue("template", row["id"], note, "warning")
        return result

    @staticmethod
    def _completion_pair(attachments):
        certificates = [a for a in attachments if "certif" in (a.get("name") or "").lower()]
        signed = [a for a in attachments if a not in certificates]
        return (signed[0], certificates[0]) if len(signed) == len(certificates) == 1 else (None, None)

    def _audit_json(self, request_row, signer_rows, log_rows):
        signer_by_source = {row["id"]: row for row in signer_rows}
        secret = self.env["ir.config_parameter"].sudo().get_str("database.secret")
        events = []
        for log in log_rows:
            signer = signer_by_source.get(log.get("sign_request_item_id"), {})
            events.append(
                {
                    "action": log.get("action"),
                    "date": str(log.get("log_date") or log.get("create_date") or ""),
                    "ip_hmac_sha256": hmac.new(
                        secret.encode(), (log.get("ip") or "").encode(), hashlib.sha256
                    ).hexdigest()
                    if log.get("ip")
                    else None,
                    "request_state": log.get("request_state"),
                    "signer_email": (signer.get("signer_email") or signer.get("partner_email") or "").lower(),
                }
            )
        return json.dumps(
            {"source": "Odoo Online Sign", "completion_date": str(request_row.get("completion_date") or ""), "events": events},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def _restore_requests(self, payload, reader, company_map, template_map):
        signers = defaultdict(list)
        completed = defaultdict(list)
        logs = defaultdict(list)
        for row in payload["signers"]:
            signers[row["sign_request_id"]].append(row)
        for row in payload["completed"]:
            completed[row["sign_request_id"]].append(row)
        for row in payload["logs"]:
            logs[row["sign_request_id"]].append(row)
        restored = 0
        for row in payload["requests"]:
            template = template_map.get(row["template_id"])
            company = company_map.get(row["communication_company_id"])
            signed_attachment, certificate_attachment = self._completion_pair(completed[row["id"]])
            if not template or not company or not signed_attachment or not certificate_attachment:
                self._issue("request", row["id"], "Completed request lacks an unambiguous template, company, signed PDF, or completion evidence.")
                continue
            original = reader.binary(next(t for t in payload["templates"] if t["id"] == row["template_id"]))
            final = reader.binary(signed_attachment)
            certificate = reader.binary(certificate_attachment)
            signer_rows = signers[row["id"]]
            signer_emails = [s.get("signer_email") or s.get("partner_email") for s in signer_rows]
            fingerprint = request_fingerprint(binary_sha256(original), binary_sha256(final), source_text(row["subject"]), row.get("completion_date"), signer_emails)
            request = self.env["sign.oca.request"].sudo().with_context(active_test=False).search(
                [("historical", "=", True), ("idempotency_key", "=", fingerprint)], limit=1
            )
            if request:
                request.with_context(usl_sign_historical_restore=True).write(
                    {
                        "achieved_assurance": False,
                        "authentication_method": False,
                        "migration_assurance_unproven": True,
                        "expires_at": False,
                    }
                )
                restored += 1
                continue
            signer_commands = []
            for sequence, signer in enumerate(signer_rows, 1):
                partner = self._partner(signer)
                if not partner:
                    continue
                signer_commands.append(
                    Command.create(
                        {
                            "partner_id": partner.id,
                            "role_id": self._role(signer.get("role_name")).id,
                            "sequence": sequence * 10,
                            "state": "signed",
                            "signed_on": signer.get("signing_date") or row.get("completion_date"),
                            "authentication_method": False,
                            "achieved_assurance": False,
                        }
                    )
                )
            if len(signer_commands) != len(signer_rows):
                self._issue("request", row["id"], "One or more signers could not be mapped.")
                continue
            policy = self.env["usl.sign.policy"].sudo().search(
                [("company_id", "=", company.id), ("assurance_level", "=", "standard")], limit=1
            )
            request = self.env["sign.oca.request"].sudo().with_context(
                usl_sign_historical_restore=True, tracking_disable=True, mail_create_nolog=True
            ).create(
                {
                    "name": source_text(row["subject"]) or source_text(row.get("reference")) or template.name,
                    "template_id": template.id,
                    "template_version": template.version,
                    "company_id": company.id,
                    "policy_id": policy.id,
                    "requested_assurance": "standard",
                    "achieved_assurance": False,
                    "authentication_method": False,
                    "provider_code": "odoo_online",
                    "historical": True,
                    "migration_assurance_unproven": True,
                    "state": "completed",
                    "signed": True,
                    "data": base64.b64encode(original),
                    "filename": template.filename,
                    "signatory_data": template._get_signatory_data(),
                    "original_data": base64.b64encode(original),
                    "original_filename": template.filename,
                    "original_sha256": binary_sha256(original),
                    "final_data": base64.b64encode(final),
                    "final_filename": signed_attachment.get("name"),
                    "final_sha256": binary_sha256(final),
                    "evidence_status": "available",
                    "validation_status": "unknown",
                    "completed_at": row.get("completion_date"),
                    "sent_at": row.get("create_date"),
                    "expires_at": False,
                    "idempotency_key": fingerprint,
                    "user_id": self._user(row.get("responsible_login")).id,
                    "signer_ids": signer_commands,
                }
            )
            evidence = [
                ("original", template.filename, original, "application/pdf", "valid"),
                ("signed", signed_attachment.get("name"), final, "application/pdf", "unknown"),
                ("completion_evidence", certificate_attachment.get("name"), certificate, "application/pdf", "unknown"),
                ("audit_trail", f"{request.name} - source audit.json", self._audit_json(row, signer_rows, logs[row["id"]]), "application/json", "unknown"),
            ]
            for kind, name, data, mimetype, validation in evidence:
                self.env["usl.sign.evidence"].sudo().create(
                    {"request_id": request.id, "kind": kind, "name": name, "data": base64.b64encode(data), "mimetype": mimetype, "validation_status": validation}
                )
            restored += 1
        return restored

    @classmethod
    def restore_from_source(cls, env, options):
        run = env["usl.sign.restore.run"].sudo().create(
            {
                "source_database": options["database"],
                "source_snapshot": options["snapshot"],
                "target_database": env.cr.dbname,
            }
        )
        reader = SignSourceReader(options)
        try:
            payload = reader.read()
            companies = run._company_map(payload)
            templates = run._restore_templates(payload, reader, companies)
            restored_requests = run._restore_requests(payload, reader, companies, templates)
            blocking = run.issue_ids.filtered(lambda issue: issue.severity == "error")
            statistics = {
                "source": {
                    "templates": len(payload["templates"]),
                    "requests": len(payload["requests"]),
                    "signers": len(payload["signers"]),
                },
                "target": {
                    "unique_templates": len({template.id for template in templates.values()}),
                    "template_links": len(templates),
                    "request_links": restored_requests,
                    "signer_links": len(payload["signers"])
                    if restored_requests == len(payload["requests"])
                    else 0,
                },
            }
            run.write({"status": "failed" if blocking else "passed", "finished_at": fields.Datetime.now(), "statistics_json": statistics})
        except Exception as error:
            run._issue("restore", False, str(error))
            run.write({"status": "failed", "finished_at": fields.Datetime.now()})
            raise
        return run, run.statistics_json
