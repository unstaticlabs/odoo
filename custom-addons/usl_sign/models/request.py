import hashlib
import json
import secrets
from base64 import b64decode, b64encode
from copy import deepcopy
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from io import BytesIO

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.pdf import PdfReader

from .policy import ASSURANCE_LEVELS, AUTHENTICATION_METHODS


REQUEST_STATES = [
    ("draft", "Draft"),
    ("ready", "Ready"),
    ("sent", "Sent"),
    ("viewed", "Viewed"),
    ("partial", "Partially signed"),
    ("completed", "Completed"),
    ("declined", "Declined"),
    ("expired", "Expired"),
    ("cancelled", "Cancelled"),
    ("action_required", "Action required"),
]

SIGNER_STATES = [
    ("draft", "Draft"),
    ("notified", "Invitation sent"),
    ("viewed", "Viewed"),
    ("signed", "Signed"),
    ("declined", "Declined"),
    ("expired", "Expired"),
    ("cancelled", "Cancelled"),
    ("error", "Action required"),
]

ACTIVE_REQUEST_STATES = {"sent", "viewed", "partial", "action_required"}
TERMINAL_REQUEST_STATES = {"completed", "declined", "expired", "cancelled"}


class SignRequest(models.Model):
    _inherit = "sign.oca.request"

    state = fields.Selection(
        selection_add=REQUEST_STATES,
        ondelete={state: "set default" for state, _label in REQUEST_STATES},
        default="draft",
        required=True,
        copy=False,
        tracking=True,
        index=True,
    )
    policy_id = fields.Many2one(
        "usl.sign.policy",
        required=True,
        domain="[('company_id', '=', company_id), ('active', '=', True)]",
        tracking=True,
    )
    requested_assurance = fields.Selection(
        ASSURANCE_LEVELS, required=True, readonly=True, tracking=True
    )
    achieved_assurance = fields.Selection(
        ASSURANCE_LEVELS, copy=False, readonly=True, tracking=True
    )
    authentication_method = fields.Selection(
        AUTHENTICATION_METHODS, copy=False, readonly=True
    )
    template_version = fields.Integer(readonly=True)
    frozen_layout = fields.Json(copy=False, readonly=True)
    original_data = fields.Binary(copy=False, readonly=True, attachment=True)
    original_filename = fields.Char(copy=False, readonly=True)
    original_sha256 = fields.Char(copy=False, readonly=True, index=True)
    final_data = fields.Binary(copy=False, readonly=True, attachment=True)
    final_filename = fields.Char(copy=False, readonly=True)
    final_sha256 = fields.Char(copy=False, readonly=True, index=True)
    evidence_ids = fields.One2many("usl.sign.evidence", "request_id")
    evidence_count = fields.Integer(compute="_compute_evidence_count")
    evidence_status = fields.Selection(
        [
            ("not_expected", "Not expected"),
            ("pending", "Pending"),
            ("available", "Available"),
            ("missing", "Action required"),
        ],
        default="not_expected",
        copy=False,
        readonly=True,
    )
    validation_status = fields.Selection(
        [
            ("not_checked", "Not checked"),
            ("valid", "Valid"),
            ("invalid", "Invalid"),
            ("unknown", "Unknown"),
        ],
        default="not_checked",
        copy=False,
        readonly=True,
    )
    provider_code = fields.Selection(
        [
            ("yousign", "Yousign"),
            ("odoo_online", "Odoo Online (historical)"),
        ],
        copy=False,
        readonly=True,
    )
    provider_transaction_id = fields.Char(copy=False, readonly=True, index=True)
    provider_document_id = fields.Char(copy=False, readonly=True)
    provider_status = fields.Char(copy=False, readonly=True)
    provider_field_map = fields.Json(copy=False, readonly=True)
    provider_last_event_at = fields.Datetime(copy=False, readonly=True)
    provider_environment = fields.Selection(
        [("sandbox", "Sandbox"), ("production", "Production")],
        copy=False,
        readonly=True,
    )
    idempotency_key = fields.Char(
        copy=False,
        readonly=True,
        index=True,
        default=lambda self: secrets.token_urlsafe(24),
    )
    sent_at = fields.Datetime(copy=False, readonly=True)
    completed_at = fields.Datetime(copy=False, readonly=True)
    expires_at = fields.Datetime(copy=False)
    reminder_days = fields.Integer(default=3)
    max_reminders = fields.Integer(default=5)
    reminder_count = fields.Integer(copy=False, readonly=True, default=0)
    last_reminder_at = fields.Datetime(copy=False, readonly=True)
    responsible_message = fields.Text()
    last_error = fields.Text(copy=False, readonly=True)
    recovery_action = fields.Char(copy=False, readonly=True)
    last_reconciled_at = fields.Datetime(copy=False, readonly=True)
    next_step = fields.Char(compute="_compute_next_step")
    signing_order = fields.Boolean()
    historical = fields.Boolean(copy=False, readonly=True)
    migration_assurance_unproven = fields.Boolean(copy=False, readonly=True)
    provider_ready = fields.Boolean(related="company_id.sign_provider_ready")
    provider_setup_message = fields.Char(compute="_compute_provider_setup_message")

    _idempotency_unique = models.Constraint(
        "UNIQUE(idempotency_key)", "The signature request operation key must be unique."
    )
    _provider_transaction_unique = models.Constraint(
        "UNIQUE(provider_code, provider_environment, provider_transaction_id)",
        "This provider transaction is already linked to another request.",
    )

    @api.model
    def _default_policy(self, company):
        return self.env["usl.sign.policy"].search(
            [("company_id", "=", company.id), ("is_default", "=", True)], limit=1
        )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            company = self.env["res.company"].browse(
                vals.get("company_id") or self.env.company.id
            )
            template = self.env["sign.oca.template"].browse(vals.get("template_id"))
            policy = self.env["usl.sign.policy"].browse(vals.get("policy_id"))
            if template:
                vals.setdefault("company_id", template.company_id.id)
                vals.setdefault("policy_id", template.policy_id.id)
                vals.setdefault("template_version", template.version)
                vals.setdefault("reminder_days", template.reminder_days)
                vals.setdefault("max_reminders", template.max_reminders)
                vals.setdefault("signing_order", template.signing_order)
            if not vals.get("policy_id"):
                policy = self._default_policy(company)
                vals["policy_id"] = policy.id
            else:
                policy = self.env["usl.sign.policy"].browse(vals["policy_id"])
            vals.setdefault("requested_assurance", policy.assurance_level)
            vals.setdefault("authentication_method", policy.authentication_method)
            vals.setdefault("provider_code", policy.provider_code)
            vals.setdefault("max_reminders", policy.max_reminders)
            if not vals.get("expires_at"):
                expiration_days = (
                    template.expiration_days if template else policy.expiration_days
                )
                vals["expires_at"] = fields.Datetime.now() + timedelta(
                    days=expiration_days
                )
        records = super().create(vals_list)
        for record in records:
            record._post_business_event(
                self.env._("Signature request created: %(name)s", name=record.name)
            )
        return records

    @api.depends("evidence_ids")
    def _compute_evidence_count(self):
        for request in self:
            request.evidence_count = len(request.evidence_ids)

    @api.depends("state", "evidence_status", "last_error")
    def _compute_next_step(self):
        labels = {
            "draft": self.env._("Complete the request and review the document."),
            "ready": self.env._("Send the request."),
            "sent": self.env._("Waiting for signer action."),
            "viewed": self.env._("The document was viewed; waiting for signature."),
            "partial": self.env._("Waiting for the remaining signers."),
            "completed": self.env._("Review or download the signed evidence."),
            "declined": self.env._("Review the decline and create a new request if needed."),
            "expired": self.env._("Create a replacement request if signature is still needed."),
            "cancelled": self.env._("No further action is expected."),
            "action_required": self.env._("Review the error and reconcile provider status."),
        }
        for request in self:
            request.next_step = labels.get(request.state)
            if request.state == "completed" and request.evidence_status != "available":
                request.next_step = self.env._(
                    "Signature is complete; retrieve the pending evidence."
                )

    @api.depends("provider_ready", "company_id")
    def _compute_provider_setup_message(self):
        for sign_request in self:
            sign_request.provider_setup_message = (
                False
                if sign_request.provider_ready
                else self.env._(
                    "Sending is unavailable until a Sign administrator enables the company provider, API credential and webhook secret."
                )
            )

    @api.constrains("policy_id", "company_id", "template_id")
    def _check_company_policy(self):
        for request in self:
            if request.policy_id.company_id != request.company_id:
                raise ValidationError(
                    self.env._("The signature policy must belong to the request company.")
                )
            if request.template_id and request.template_id.company_id != request.company_id:
                raise ValidationError(
                    self.env._("The template and request must belong to the same company.")
                )

    def _validate_source_pdf(self):
        self.ensure_one()
        if not self.data:
            raise ValidationError(self.env._("Upload a PDF before preparing the request."))
        try:
            reader = PdfReader(BytesIO(b64decode(self.data)))
            if getattr(reader, "is_encrypted", False):
                raise ValidationError(
                    self.env._("The PDF is encrypted. Upload an unlocked PDF to continue.")
                )
            if not reader.pages:
                raise ValidationError(self.env._("The PDF does not contain any pages."))
        except ValidationError:
            raise
        except Exception as error:
            raise ValidationError(
                self.env._("The uploaded file is not a readable PDF. Replace it and try again.")
            ) from error
        return reader

    def _validate_preparation(self):
        self.ensure_one()
        self._validate_source_pdf()
        if not self.signer_ids:
            raise ValidationError(self.env._("Add at least one signer."))
        role_ids = set(self.signer_ids.mapped("role_id").ids)
        field_roles = {
            int(item.get("role_id"))
            for item in (self.signatory_data or {}).values()
            if item.get("role_id")
        }
        missing_roles = field_roles - role_ids
        if missing_roles:
            role_names = ", ".join(
                self.env["sign.oca.role"].browse(list(missing_roles)).mapped("name")
            )
            raise ValidationError(
                self.env._("Assign a signer to these required roles: %(roles)s", roles=role_names)
            )
        if not (self.signatory_data or {}):
            raise ValidationError(
                self.env._("Place at least one field on the document before sending it.")
            )
        signature_roles = {
            int(item.get("role_id"))
            for item in (self.signatory_data or {}).values()
            if item.get("role_id")
            and self.env["sign.oca.field"].browse(item.get("field_id")).usl_kind
            == "signature"
        }
        missing_signature_roles = role_ids - signature_roles
        if missing_signature_roles:
            role_names = ", ".join(
                self.env["sign.oca.role"]
                .browse(list(missing_signature_roles))
                .mapped("name")
            )
            raise ValidationError(
                self.env._(
                    "Place a signature field for these signer roles: %(roles)s",
                    roles=role_names,
                )
            )
        for signer in self.signer_ids:
            if not signer.partner_id.email:
                raise ValidationError(
                    self.env._(
                        "Add an email address to signer %(signer)s.",
                        signer=signer.partner_id.display_name,
                    )
                )
            if self.authentication_method in {
                "otp_sms",
                "identity_verification",
                "qualified_identity",
            } and not (signer.partner_id.mobile or signer.partner_id.phone):
                raise ValidationError(
                    self.env._(
                        "Add a mobile phone number to signer %(signer)s for this assurance policy.",
                        signer=signer.partner_id.display_name,
                    )
                )
        if self.expires_at and self.expires_at <= fields.Datetime.now():
            raise ValidationError(self.env._("Choose an expiration date in the future."))
        return True

    def action_mark_ready(self):
        for request in self:
            if request.state != "draft":
                raise ValidationError(self.env._("Only draft requests can be prepared."))
            request._validate_preparation()
            request.state = "ready"
        return True

    def _freeze_document(self):
        self.ensure_one()
        self._validate_preparation()
        if self.original_data:
            return
        raw = b64decode(self.data)
        sha256 = hashlib.sha256(raw).hexdigest()
        self.with_context(usl_sign_freeze=True).write(
            {
                "original_data": self.data,
                "original_filename": self.filename or f"{self.name}.pdf",
                "original_sha256": sha256,
                "frozen_layout": deepcopy(self.signatory_data or {}),
                "evidence_status": "pending",
            }
        )
        self.env["usl.sign.evidence"].create(
            {
                "request_id": self.id,
                "kind": "original",
                "name": self.original_filename,
                "data": self.original_data,
                "mimetype": "application/pdf",
                "validation_status": "valid",
            }
        )
        self.message_post(body=self.env._("The source document and field layout were frozen."))

    def action_send(self, sign_now=False, message=""):
        del sign_now
        for request in self:
            if request.historical:
                raise ValidationError(
                    self.env._("Historical requests are read-only and cannot be sent.")
                )
            if request.state not in {"draft", "ready", "action_required"}:
                return False
            if (
                not request.provider_ready
                and not request.env.context.get("usl_sign_skip_provider_ready")
            ):
                raise ValidationError(request.provider_setup_message)
            request._freeze_document()
            if message:
                request.responsible_message = message
            request._provider_send()
        return True

    def action_reconcile(self):
        for request in self:
            if not request.provider_transaction_id and request.state != "action_required":
                raise ValidationError(
                    self.env._("This request has no provider transaction to reconcile.")
                )
            request._provider_reconcile(manual=True)
        return True

    def action_send_reminder(self):
        self._send_due_reminders(force=True)
        return True

    def _send_due_reminders(self, force=False):
        now = fields.Datetime.now()
        for sign_request in self:
            if sign_request.state not in {"sent", "viewed", "partial"}:
                if force:
                    raise ValidationError(
                        self.env._("Reminders can only be sent for an active request.")
                    )
                continue
            if sign_request.reminder_count >= sign_request.max_reminders:
                if force:
                    raise ValidationError(
                        self.env._("The maximum number of reminders has been reached.")
                    )
                continue
            due_at = (
                sign_request.last_reminder_at or sign_request.sent_at or sign_request.create_date
            ) + timedelta(days=sign_request.reminder_days)
            if not force and (not sign_request.reminder_days or due_at > now):
                continue
            signers = sign_request.signer_ids.filtered(
                lambda signer: signer.state in {"notified", "viewed"}
                and not signer.access_revoked
            )
            if sign_request.signing_order and signers:
                first_sequence = min(signers.mapped("sequence"))
                signers = signers.filtered(lambda signer: signer.sequence == first_sequence)
            if not signers:
                continue
            template = self.env.ref("usl_sign.mail_template_sign_reminder")
            for signer in signers:
                template.send_mail(signer.id, force_send=False)
                signer.write(
                    {
                        "reminder_sent_at": now,
                        "reminder_count": signer.reminder_count + 1,
                    }
                )
            sign_request.with_context(usl_sign_transition=True).write(
                {
                    "last_reminder_at": now,
                    "reminder_count": sign_request.reminder_count + 1,
                }
            )
        return True

    def _store_terminal_evidence(self, kind, explanation, signer=False, reference=False):
        self.ensure_one()
        reference = reference or f"{kind}:{self.id}:{fields.Datetime.now()}"
        existing = self.env["usl.sign.evidence"].search_count(
            [
                ("request_id", "=", self.id),
                ("kind", "=", kind),
                ("provider_reference", "=", reference),
            ],
            limit=1,
        )
        if existing:
            return
        facts = {
            "request": self.name,
            "request_id": self.id,
            "provider": self.provider_code,
            "provider_transaction": self.provider_transaction_id,
            "event": kind,
            "recorded_at": fields.Datetime.to_string(fields.Datetime.now()),
            "explanation": explanation,
            "signer_id": signer.id if signer else False,
        }
        self.env["usl.sign.evidence"].create(
            {
                "request_id": self.id,
                "signer_id": signer.id if signer else False,
                "kind": kind,
                "name": f"{self.name} - {kind}.json",
                "data": b64encode(json.dumps(facts, sort_keys=True).encode()),
                "mimetype": "application/json",
                "provider_reference": reference,
                "retrieved_at": fields.Datetime.now(),
                "validation_status": "valid",
            }
        )

    def _notify_responsible(self, subject, body):
        for sign_request in self:
            sign_request.message_post(
                subject=subject,
                body=body,
                partner_ids=sign_request.user_id.partner_id.ids,
                subtype_xmlid="mail.mt_comment",
            )

    def _expire_request(self):
        for sign_request in self.filtered(
            lambda item: item.state in {"sent", "viewed", "partial", "action_required"}
        ):
            sign_request.with_context(usl_sign_transition=True).write(
                {"state": "expired", "provider_status": "expired"}
            )
            sign_request.signer_ids.filtered(
                lambda signer: signer.state != "signed"
            ).write({"state": "expired", "access_revoked": True})
            sign_request._store_terminal_evidence(
                "expiration", self.env._("The request reached its configured expiration."),
                reference=f"expiration:{sign_request.id}",
            )
            sign_request._notify_responsible(
                self.env._("Signature request expired"),
                self.env._("The signature request %(name)s expired.", name=sign_request.name),
            )
            sign_request._post_business_event(
                self.env._("Signature request expired: %(name)s", name=sign_request.name)
            )

    @api.model
    def _cron_sign_operations(self):
        now = fields.Datetime.now()
        expired = self.search(
            [
                ("state", "in", list(ACTIVE_REQUEST_STATES)),
                ("expires_at", "!=", False),
                ("expires_at", "<=", now),
            ],
            limit=100,
        )
        expired._expire_request()
        reminders = self.search(
            [
                ("state", "in", ["sent", "viewed", "partial"]),
                ("reminder_days", ">", 0),
                ("reminder_count", "<", 10),
            ],
            limit=100,
        )
        reminders._send_due_reminders()

    def cancel(self):
        for request in self:
            if request.state in TERMINAL_REQUEST_STATES:
                continue
            request._provider_cancel()
            request.with_context(usl_sign_transition=True).write(
                {"state": "cancelled", "provider_status": "cancelled"}
            )
            request.signer_ids.write({"state": "cancelled", "access_revoked": True})
            request._store_terminal_evidence(
                "cancellation",
                self.env._("The responsible user cancelled the request."),
                reference=f"cancellation:{request.id}",
            )
            request._notify_responsible(
                self.env._("Signature request cancelled"),
                self.env._("The signature request %(name)s was cancelled.", name=request.name),
            )
            request._post_business_event(
                self.env._("Signature request cancelled: %(name)s", name=request.name)
            )
        return True

    def _post_business_event(self, body):
        for request in self:
            if request.record_ref and hasattr(request.record_ref, "message_post"):
                request.record_ref.message_post(body=body)

    def _set_action_required(self, explanation, recovery_action):
        self.ensure_one()
        self.with_context(usl_sign_transition=True).write(
            {
                "state": "action_required",
                "last_error": explanation,
                "recovery_action": recovery_action,
            }
        )
        self.message_post(
            body=self.env._(
                "Signature processing needs attention. %(explanation)s",
                explanation=explanation,
            )
        )
        self._post_business_event(
            self.env._("Signature request requires action: %(name)s", name=self.name)
        )
        self._notify_responsible(
            self.env._("Signature request needs attention"),
            self.env._(
                "The signature request %(name)s needs attention: %(explanation)s",
                name=self.name,
                explanation=explanation,
            ),
        )

    def write(self, vals):
        if (
            self.filtered("historical")
            and vals
            and not self.env.context.get("usl_sign_historical_restore")
        ):
            raise ValidationError(
                self.env._("Historical signature requests are read-only evidence.")
            )
        frozen_fields = {
            "data",
            "filename",
            "signatory_data",
            "signer_ids",
            "template_id",
            "template_version",
            "company_id",
            "policy_id",
            "requested_assurance",
        }
        if not self.env.context.get("usl_sign_freeze") and frozen_fields.intersection(vals):
            frozen = self.filtered(lambda request: request.state not in {"draft", "ready"})
            if frozen:
                raise ValidationError(
                    self.env._(
                        "A sent request is frozen. Cancel it and create a replacement request to make material changes."
                    )
                )
        if "state" in vals and not self.env.context.get("usl_sign_transition"):
            invalid = self.filtered(
                lambda request: request.state in TERMINAL_REQUEST_STATES
                and vals["state"] != request.state
            )
            if invalid:
                raise ValidationError(
                    self.env._("A terminal signature request cannot be reopened.")
                )
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda request: request.state != "draft"):
            raise ValidationError(
                self.env._("Only draft signature requests can be deleted.")
            )
        return super().unlink()


class SignRequestSigner(models.Model):
    _inherit = "sign.oca.request.signer"
    _order = "sequence, id"

    state = fields.Selection(
        SIGNER_STATES, required=True, default="draft", copy=False, tracking=True
    )
    sequence = fields.Integer(string="Signing order", default=10)
    provider_signer_id = fields.Char(copy=False, readonly=True, index=True)
    provider_signature_link = fields.Char(
        copy=False, readonly=True, groups="usl_sign.group_sign_admin"
    )
    signature_link_expires_at = fields.Datetime(copy=False, readonly=True)
    access_revoked = fields.Boolean(copy=False, readonly=True)
    viewed_at = fields.Datetime(copy=False, readonly=True)
    declined_at = fields.Datetime(copy=False, readonly=True)
    invitation_sent_at = fields.Datetime(copy=False, readonly=True)
    invitation_count = fields.Integer(copy=False, readonly=True, default=0)
    reminder_sent_at = fields.Datetime(copy=False, readonly=True)
    reminder_count = fields.Integer(copy=False, readonly=True, default=0)
    achieved_assurance = fields.Selection(
        ASSURANCE_LEVELS, copy=False, readonly=True
    )
    authentication_method = fields.Selection(
        AUTHENTICATION_METHODS, copy=False, readonly=True
    )
    provider_last_event_at = fields.Datetime(copy=False, readonly=True)
    decline_reason = fields.Char(copy=False, readonly=True)

    _provider_signer_unique = models.Constraint(
        "UNIQUE(provider_signer_id)",
        "This provider signer is already linked to another recipient.",
    )

    @api.depends("signed_on", "partner_id", "state", "request_id.state")
    @api.depends_context("uid")
    def _compute_is_allow_signature(self):
        user_partner = self.env.user.partner_id.commercial_partner_id
        for signer in self:
            allowed_order = True
            if signer.request_id.signing_order:
                allowed_order = not signer.request_id.signer_ids.filtered(
                    lambda other: other.sequence < signer.sequence
                    and other.state != "signed"
                )
            signer.is_allow_signature = bool(
                not signer.signed_on
                and not signer.access_revoked
                and signer.partner_id.commercial_partner_id == user_partner
                and signer.request_id.state in ACTIVE_REQUEST_STATES
                and allowed_order
            )

    @api.depends("access_token")
    def _compute_access_url(self):
        for signer in self:
            signer.access_url = (
                f"/sign/document/{signer.id}/{signer.access_token}"
                if signer.access_token
                else False
            )

    def _check_secure_access(self, access_token):
        self.ensure_one()
        if (
            not access_token
            or not self.access_token
            or not secrets.compare_digest(access_token, self.access_token)
            or self.access_revoked
        ):
            raise AccessError(self.env._("This signing link is invalid or has been revoked."))
        if self.request_id.state not in ACTIVE_REQUEST_STATES:
            raise AccessError(
                self.env._("This signing request is no longer available for signature.")
            )
        if self.request_id.expires_at and self.request_id.expires_at <= fields.Datetime.now():
            raise AccessError(self.env._("This signing link has expired."))
        if self.signed_on:
            raise AccessError(self.env._("This signer has already completed the request."))
        if self.request_id.signing_order and self.request_id.signer_ids.filtered(
            lambda other: other.sequence < self.sequence and other.state != "signed"
        ):
            raise AccessError(
                self.env._("Another signer must complete the document before you can sign.")
            )
        return True

    def action_regenerate_link(self):
        for signer in self:
            if signer.request_id.state not in ACTIVE_REQUEST_STATES:
                raise ValidationError(
                    self.env._("A link can only be renewed for an active request.")
                )
            signer.write(
                {
                    "access_token": secrets.token_urlsafe(32),
                    "access_revoked": False,
                }
            )
            signer._send_signer_invitation(force=True)
        return True

    def action_revoke_link(self):
        self.write({"access_revoked": True})
        return True

    def _absolute_access_url(self):
        self.ensure_one()
        self._portal_ensure_token()
        return f"{self.get_base_url()}{self.access_url}"

    def _send_signer_invitation(self, force=False):
        template = self.env.ref("usl_sign.mail_template_sign_invitation")
        for signer in self:
            signer._portal_ensure_token()
            if signer.invitation_sent_at and not force:
                continue
            template.send_mail(signer.id, force_send=False)
            signer.write(
                {
                    "invitation_sent_at": fields.Datetime.now(),
                    "invitation_count": signer.invitation_count + 1,
                }
            )
        return True

    def _mark_viewed(self):
        for signer in self.filtered(lambda row: not row.viewed_at):
            signer.write({"viewed_at": fields.Datetime.now(), "state": "viewed"})
            if signer.request_id.state == "sent":
                signer.request_id.with_context(usl_sign_transition=True).write(
                    {"state": "viewed"}
                )

    def action_sign(self, *args, **kwargs):
        del args, kwargs
        raise UserError(
            self.env._(
                "This request must be completed through its secure provider-backed signing link."
            )
        )

    def get_info(self, access_token=False):
        self.ensure_one()
        if access_token:
            self._check_secure_access(access_token)
        return {
            "role_id": self.role_id.id if not self.signed_on else False,
            "name": self.request_id.name,
            "items": self.request_id.frozen_layout
            or self.request_id.signatory_data
            or {},
            "to_sign": not self.signed_on and not self.access_revoked,
            "partner": {"name": self.partner_id.name},
        }


class SignProviderEvent(models.Model):
    _name = "usl.sign.provider.event"
    _description = "Signature Provider Event"
    _order = "event_time desc, id desc"

    provider_code = fields.Selection([("yousign", "Yousign")], required=True)
    event_id = fields.Char(required=True, index=True)
    event_name = fields.Char(required=True)
    event_time = fields.Datetime(required=True)
    request_id = fields.Many2one(
        "sign.oca.request", required=True, ondelete="cascade", index=True
    )
    payload_sha256 = fields.Char(required=True)
    status = fields.Selection(
        [
            ("processed", "Processed"),
            ("ignored", "Ignored"),
            ("error", "Action required"),
        ],
        required=True,
    )
    explanation = fields.Char()

    _provider_event_unique = models.Constraint(
        "UNIQUE(provider_code, event_id)", "This provider event was already received."
    )

    @api.model
    def _event_datetime(self, payload):
        event_epoch = payload.get("event_time")
        if isinstance(event_epoch, str) and event_epoch.isdigit():
            event_epoch = int(event_epoch)
        return (
            datetime.fromtimestamp(event_epoch, tz=timezone.utc).replace(tzinfo=None)
            if isinstance(event_epoch, (int, float))
            else fields.Datetime.to_datetime(event_epoch) or fields.Datetime.now()
        )

    @api.model
    def record_event(self, request, payload, status, explanation=False):
        event_id = payload.get("event_id")
        if not event_id:
            raise ValidationError(self.env._("Provider event is missing its identity."))
        existing = self.search(
            [("provider_code", "=", "yousign"), ("event_id", "=", event_id)],
            limit=1,
        )
        if existing:
            return existing, False
        event_time = self._event_datetime(payload)
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return (
            self.create(
                {
                    "provider_code": "yousign",
                    "event_id": event_id,
                    "event_name": payload.get("event_name") or "unknown",
                    "event_time": event_time,
                    "request_id": request.id,
                    "payload_sha256": hashlib.sha256(raw).hexdigest(),
                    "status": status,
                    "explanation": explanation,
                }
            ),
            True,
        )
