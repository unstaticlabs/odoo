import hashlib
import json

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..services import field_content
from .constants import INTERNAL_OPERATION, TRUST_LEVELS


class SignEvidence(models.Model):
    _name = "usl.sign.evidence"
    _description = "Immutable Signature Evidence Artifact"
    _order = "create_date, id"

    request_id = fields.Many2one(
        "sign.oca.request", required=True, index=True, ondelete="restrict",
    )
    signer_id = fields.Many2one(
        "sign.oca.request.signer", index=True, ondelete="restrict",
    )
    company_id = fields.Many2one(related="request_id.company_id", store=True, index=True)
    kind = fields.Selection(
        [
            ("source", "Source document"),
            ("frozen", "Frozen signing document"),
            ("signed", "Final validated signed document"),
            ("consent", "Consent evidence"),
            ("authentication", "Authentication evidence"),
            ("snapshot", "Frozen policy and field snapshot"),
            ("lifecycle", "Lifecycle event history"),
            ("certificate", "Certificate or chain"),
            ("timestamp", "Timestamp or revocation evidence"),
            ("validation", "DSS validation report"),
            ("external", "External-provider evidence"),
            ("completion", "Completion certificate"),
            ("manifest", "Signed evidence manifest"),
            ("dossier", "Paperless archival dossier"),
            ("decline", "Decline evidence"),
            ("expiration", "Expiration evidence"),
            ("cancellation", "Cancellation evidence"),
        ],
        required=True,
        index=True,
    )
    name = fields.Char(required=True)
    data = fields.Binary(required=True, attachment=True)
    mimetype = fields.Char(required=True, default="application/octet-stream")
    sha256 = fields.Char(required=True, readonly=True, index=True)
    metadata = fields.Json(readonly=True)
    created_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get("usl_sign_evidence_create") is not INTERNAL_OPERATION:
            msg = "Use the controlled evidence operation."
            raise AccessError(msg)
        for values in vals_list:
            raw = field_content(values.get("data"))
            if not raw:
                msg = "Evidence artifacts cannot be empty."
                raise ValidationError(msg)
            digest = hashlib.sha256(raw).hexdigest()
            if values.get("sha256") and values["sha256"] != digest:
                msg = "The evidence checksum does not match its content."
                raise ValidationError(msg)
            values["sha256"] = digest
        return super().create(vals_list)

    def write(self, values):
        msg = "Signature evidence is append-only."
        raise AccessError(msg)

    def unlink(self):
        msg = "Signature evidence cannot be deleted."
        raise AccessError(msg)


class SignEvent(models.Model):
    _name = "usl.sign.event"
    _description = "Tamper-evident Signature Lifecycle Event"
    _order = "request_id, sequence"

    request_id = fields.Many2one(
        "sign.oca.request", required=True, index=True, ondelete="restrict",
    )
    company_id = fields.Many2one(related="request_id.company_id", store=True, index=True)
    sequence = fields.Integer(required=True, readonly=True)
    event_type = fields.Char(required=True, readonly=True, index=True)
    state_from = fields.Char(readonly=True)
    state_to = fields.Char(readonly=True)
    actor_id = fields.Many2one("res.users", readonly=True, ondelete="set null")
    signer_id = fields.Many2one(
        "sign.oca.request.signer", readonly=True, ondelete="restrict",
    )
    authentication_method = fields.Char(readonly=True)
    ip_address = fields.Char(readonly=True)
    user_agent = fields.Char(readonly=True)
    occurred_at = fields.Datetime(required=True, readonly=True)
    payload = fields.Json(required=True, readonly=True)
    previous_hash = fields.Char(readonly=True)
    payload_sha256 = fields.Char(required=True, readonly=True)
    event_hash = fields.Char(required=True, readonly=True, index=True)

    _request_sequence_unique = models.Constraint(
        "UNIQUE(request_id, sequence)", "Event sequence must be unique per request.",
    )

    @api.model
    def append(
        self,
        request_record,
        event_type,
        *,
        state_from=None,
        state_to=None,
        signer=None,
        authentication_method=None,
        ip_address=None,
        user_agent=None,
        payload=None,
        occurred_at=None,
    ):
        request_record.ensure_one()
        self.env.cr.execute(
            "SELECT id FROM sign_oca_request WHERE id = %s FOR UPDATE",
            [request_record.id],
        )
        previous = self.search(
            [("request_id", "=", request_record.id)],
            order="sequence desc",
            limit=1,
        )
        sequence = (previous.sequence if previous else 0) + 1
        occurred_at = occurred_at or fields.Datetime.now()
        actor_id = self.env.user.id if not self.env.user._is_public() else None
        canonical_payload = {
            "actor_id": actor_id,
            "authentication_method": authentication_method or None,
            "event_type": event_type,
            "ip_address": ip_address or None,
            "occurred_at": fields.Datetime.to_string(occurred_at),
            "payload": payload or {},
            "request_id": request_record.id,
            "sequence": sequence,
            "signer_id": signer.id if signer else None,
            "state_from": state_from or None,
            "state_to": state_to or None,
            "user_agent": user_agent or None,
        }
        serialized = json.dumps(
            canonical_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode()
        payload_sha256 = hashlib.sha256(serialized).hexdigest()
        event_hash = hashlib.sha256(
            f"{previous.event_hash if previous else ''}:{payload_sha256}".encode(),
        ).hexdigest()
        return super().create(
            {
                "request_id": request_record.id,
                "sequence": sequence,
                "event_type": event_type,
                "state_from": state_from,
                "state_to": state_to,
                "actor_id": actor_id,
                "signer_id": signer.id if signer else False,
                "authentication_method": authentication_method,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "occurred_at": occurred_at,
                "payload": canonical_payload,
                "previous_hash": previous.event_hash if previous else False,
                "payload_sha256": payload_sha256,
                "event_hash": event_hash,
            },
        )

    def verify_chain(self):
        events = self.sorted(lambda event: event.sequence)
        previous_hash = ""
        for expected_sequence, event in enumerate(events, start=1):
            payload = {
                "actor_id": event.actor_id.id or None,
                "authentication_method": event.authentication_method or None,
                "event_type": event.event_type,
                "ip_address": event.ip_address or None,
                "occurred_at": fields.Datetime.to_string(event.occurred_at),
                "payload": (event.payload or {}).get("payload") or {},
                "request_id": event.request_id.id,
                "sequence": event.sequence,
                "signer_id": event.signer_id.id or None,
                "state_from": event.state_from or None,
                "state_to": event.state_to or None,
                "user_agent": event.user_agent or None,
            }
            serialized = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode()
            payload_sha256 = hashlib.sha256(serialized).hexdigest()
            event_hash = hashlib.sha256(
                f"{previous_hash}:{payload_sha256}".encode(),
            ).hexdigest()
            if (
                event.sequence != expected_sequence
                or (event.previous_hash or "") != previous_hash
                or event.payload != payload
                or event.payload_sha256 != payload_sha256
                or event.event_hash != event_hash
            ):
                raise ValidationError(
                    f"Signature event chain verification failed at sequence {event.sequence}.",
                )
            previous_hash = event_hash
        return events[-1:] if events else self.browse()

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get("usl_sign_event_append") is not INTERNAL_OPERATION:
            msg = "Use the controlled append operation for signature events."
            raise AccessError(msg)
        return super().create(vals_list)

    def write(self, values):
        msg = "Signature events are append-only."
        raise AccessError(msg)

    def unlink(self):
        msg = "Signature events cannot be deleted."
        raise AccessError(msg)


class SignValidation(models.Model):
    _name = "usl.sign.validation"
    _description = "Independent Signature Validation Run"
    _order = "create_date desc, id desc"

    request_id = fields.Many2one(
        "sign.oca.request", required=True, index=True, ondelete="restrict",
    )
    company_id = fields.Many2one(related="request_id.company_id", store=True, index=True)
    engine = fields.Char(required=True, readonly=True, default="EU DSS")
    engine_version = fields.Char(required=True, readonly=True)
    expected_trust = fields.Selection(TRUST_LEVELS, readonly=True)
    achieved_trust = fields.Selection(TRUST_LEVELS, readonly=True)
    status = fields.Selection(
        [("valid", "Valid"), ("invalid", "Invalid"), ("indeterminate", "Indeterminate")],
        required=True,
        readonly=True,
    )
    signature_count = fields.Integer(readonly=True)
    qualified_provider = fields.Char(readonly=True)
    certificate_summary = fields.Json(readonly=True)
    timestamp_summary = fields.Json(readonly=True)
    revocation_summary = fields.Json(readonly=True)
    summary = fields.Text(readonly=True)
    report_evidence_id = fields.Many2one("usl.sign.evidence", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get("usl_sign_validation_create") is not INTERNAL_OPERATION:
            msg = "Use the controlled validation operation."
            raise AccessError(msg)
        return super().create(vals_list)

    def write(self, values):
        msg = "Validation results cannot be changed."
        raise AccessError(msg)

    def unlink(self):
        msg = "Validation results cannot be deleted."
        raise AccessError(msg)
