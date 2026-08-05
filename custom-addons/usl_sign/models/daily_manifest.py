import base64
import hashlib
import json
from datetime import datetime, time, timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..services import DSSClient, DSSServiceError
from .constants import INTERNAL_OPERATION


class SignDailyManifest(models.Model):
    _name = "usl.sign.daily.manifest"
    _description = "Signed Daily Signature Event-Head Manifest"
    _order = "manifest_date desc, company_id, id desc"

    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="restrict",
    )
    manifest_date = fields.Date(required=True, index=True)
    state = fields.Selection(
        [("signed", "Signed"), ("failed", "Signing failed")],
        required=True,
        readonly=True,
    )
    payload = fields.Binary(required=True, readonly=True, attachment=True)
    payload_sha256 = fields.Char(required=True, readonly=True, index=True)
    event_count = fields.Integer(required=True, readonly=True)
    request_count = fields.Integer(required=True, readonly=True)
    signature = fields.Binary(readonly=True, attachment=True)
    signature_algorithm = fields.Char(readonly=True)
    certificate_chain = fields.Json(readonly=True)
    signed_at = fields.Datetime(readonly=True)
    anchoring_status = fields.Selection(
        [
            ("not_configured", "Independent anchoring not configured"),
            ("pending", "Anchoring pending"),
            ("anchored", "Anchored"),
            ("failed", "Anchoring failed"),
        ],
        default="not_configured",
        required=True,
        readonly=True,
    )
    anchoring_receipt = fields.Binary(readonly=True, attachment=True)
    failure_code = fields.Char(readonly=True)

    _company_day_unique = models.Constraint(
        "UNIQUE(company_id, manifest_date)",
        "A company can have only one daily Sign event-head manifest.",
    )

    @api.model
    def _canonical_payload(self, company, manifest_date):
        start = datetime.combine(manifest_date, time.min)
        end = start + timedelta(days=1)
        events = self.env["usl.sign.event"].sudo().search(
            [
                ("company_id", "=", company.id),
                ("occurred_at", ">=", start),
                ("occurred_at", "<", end),
            ],
            order="request_id, sequence",
        )
        heads = {}
        for sign_request in events.mapped("request_id"):
            sign_request.event_ids.verify_chain()
        for event in events:
            heads[event.request_id.id] = {
                "request_id": event.request_id.id,
                "request_reference": event.request_id.name,
                "sequence": event.sequence,
                "event_hash": event.event_hash,
            }
        payload = {
            "format": "usl-sign-daily-event-heads-v1",
            "company_id": company.id,
            "manifest_date": fields.Date.to_string(manifest_date),
            "event_count": len(events),
            "request_heads": sorted(heads.values(), key=lambda row: row["request_id"]),
        }
        raw = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode()
        return raw, len(events), len(heads)

    @api.model
    def build_for_day(self, company, manifest_date):
        manifest_date = fields.Date.to_date(manifest_date)
        manifest = self.search(
            [("company_id", "=", company.id), ("manifest_date", "=", manifest_date)],
            limit=1,
        )
        if manifest.state == "signed":
            return manifest
        raw, event_count, request_count = self._canonical_payload(company, manifest_date)
        values = {
            "company_id": company.id,
            "manifest_date": manifest_date,
            "payload": base64.b64encode(raw),
            "payload_sha256": hashlib.sha256(raw).hexdigest(),
            "event_count": event_count,
            "request_count": request_count,
        }
        try:
            signed = DSSClient().sign_manifest(raw)
            values.update(
                {
                    "state": "signed",
                    "signature": base64.b64encode(base64.b64decode(signed["signature"])),
                    "signature_algorithm": signed["signatureAlgorithm"],
                    "certificate_chain": signed["certificateChain"],
                    "signed_at": fields.Datetime.now(),
                    "failure_code": False,
                },
            )
        except (DSSServiceError, KeyError, ValueError, TypeError) as error:
            values.update(
                {
                    "state": "failed",
                    "failure_code": type(error).__name__,
                },
            )
        if manifest:
            manifest.with_context(usl_sign_daily_manifest_retry=INTERNAL_OPERATION).write(values)
            return manifest
        return self.with_context(usl_sign_daily_manifest_build=INTERNAL_OPERATION).create(values)

    @api.model
    def _cron_build_daily_manifests(self):
        manifest_date = fields.Date.today() - timedelta(days=1)
        for company in self.env["res.company"].sudo().search([]):
            self.sudo().build_for_day(company, manifest_date)

    def action_retry(self):
        for manifest in self:
            if manifest.state != "failed":
                msg = "Only a failed daily manifest can be retried."
                raise ValidationError(msg)
            self.sudo().build_for_day(manifest.company_id, manifest.manifest_date)
        return True

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get("usl_sign_daily_manifest_build") is not INTERNAL_OPERATION:
            msg = "Daily manifests are created by the controlled signing job."
            raise AccessError(msg)
        return super().create(vals_list)

    def write(self, values):
        if self.env.context.get("usl_sign_daily_manifest_retry") is not INTERNAL_OPERATION:
            msg = "Signed daily manifests are immutable."
            raise AccessError(msg)
        if self.filtered(lambda manifest: manifest.state != "failed"):
            msg = "A signed daily manifest cannot be changed."
            raise AccessError(msg)
        return super().write(values)

    def unlink(self):
        msg = "Daily event-head manifests cannot be deleted."
        raise AccessError(msg)
