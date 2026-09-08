"""Signer participation, access tokens, one-time codes, invitations and signature application."""

import hashlib
import json
import secrets
from datetime import timedelta
from io import BytesIO

from markupsafe import escape

from odoo import (
    SUPERUSER_ID,
    _,
    api,
    fields,
    models,
)
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.pdf import PdfReader, PdfWriter

from .constants import (
    AUTHENTICATION_METHODS,
    INTERNAL_OPERATION,
    MUTABLE_REQUEST_STATES,
    SIGNER_STATES,
    TRUST_LEVELS,
)
from .document import _add_page
from .template import _field_kind
from odoo.addons.usl_sign.services import field_content, field_value


class SignRequestSigner(models.Model):
    _inherit = "sign.oca.request.signer"
    _order = "sequence, id"

    state = fields.Selection(SIGNER_STATES, default="draft", required=True, copy=False)
    sequence = fields.Integer(string="Signing order", default=10)
    sender_id = fields.Many2one(
        related="request_id.user_id",
        string="Sender",
        readonly=True,
    )
    request_state = fields.Selection(
        related="request_id.state",
        string="Request status",
        readonly=True,
    )
    overall_status = fields.Char(
        related="request_id.lifecycle_stage_label",
        string="Overall status",
        readonly=True,
    )
    personal_status = fields.Char(
        compute="_compute_personal_presentation",
        string="Your status",
    )
    personal_next_step = fields.Char(
        compute="_compute_personal_presentation",
        string="What happens next",
    )
    can_open_signing_identity = fields.Boolean(
        compute="_compute_personal_presentation",
    )
    can_open_external_signing = fields.Boolean(
        compute="_compute_personal_presentation",
    )
    request_due_at = fields.Datetime(
        related="request_id.expires_at",
        string="Due",
        readonly=True,
    )
    requested_trust_short = fields.Char(
        related="request_id.requested_trust_short",
        string="Trust",
        readonly=True,
    )
    document_name = fields.Char(
        related="request_id.name",
        string="Document",
        readonly=True,
    )
    document_preview_url = fields.Char(
        related="request_id.document_preview_url",
        string="Document preview",
        readonly=True,
    )
    document_thumbnail_url = fields.Char(
        related="request_id.document_thumbnail_url",
        string="Document thumbnail",
        readonly=True,
    )

    @api.depends(
        "state",
        "request_id.state",
        "request_id.external_journey_id",
    )
    @api.depends_context("lang", "uid")
    def _compute_personal_presentation(self):
        partner_ids = self.mapped("partner_id").ids
        company_ids = self.mapped("request_id.company_id").ids
        enrollments = self.env["usl.sign.enrollment"].sudo().search(
            [
                ("partner_id", "in", partner_ids),
                ("company_id", "in", company_ids),
                ("state", "!=", "revoked"),
            ],
        )
        enrollment_by_identity = {
            (enrollment.partner_id.id, enrollment.company_id.id): enrollment
            for enrollment in enrollments
        }
        request_labels = {
            "completed": _("Signed"),
            "external_archived": _("Recorded externally"),
            "declined": _("Closed after a decline"),
            "expired": _("Expired"),
            "cancelled": _("Cancelled"),
            "validation_failed": _("Result needs review"),
        }
        signer_labels = {
            "signed": _("Signed"),
            "external_recorded": _("Recorded externally"),
            "declined": _("Declined"),
            "expired": _("Expired"),
            "cancelled": _("Cancelled"),
        }
        for signer in self:
            owns_assignment = signer.partner_id == self.env.user.partner_id
            enrollment = enrollment_by_identity.get(
                (signer.partner_id.id, signer.request_id.company_id.id),
                self.env["usl.sign.enrollment"],
            )
            signer.can_open_signing_identity = bool(
                owns_assignment
                and signer.request_id.state == "waiting_enrollment"
                and enrollment,
            )
            signer.can_open_external_signing = bool(
                owns_assignment
                and signer.request_id.state == "waiting_external"
                and signer.request_id.external_journey_id,
            )
            if signer.state in signer_labels:
                signer.personal_status = signer_labels[signer.state]
                signer.personal_next_step = (
                    _("Open the archived signed PDF and Odoo Online certificate.")
                    if signer.state == "external_recorded"
                    else _("Open the completed files.")
                    if signer.state == "signed"
                    else _("Nothing else is needed from you.")
                )
            elif signer.state in {"notified", "viewed", "authorized"}:
                signer.personal_status = _("Ready to sign")
                signer.personal_next_step = _("Review the document and sign it.")
            elif signer.request_id.state == "waiting_enrollment":
                if enrollment and enrollment.state == "pending_pocket":
                    signer.personal_status = _("Identity setup needed")
                    signer.personal_next_step = _(
                        "Use your setup invitation to connect Pocket ID.",
                    )
                elif enrollment and enrollment.state == "pending_review":
                    signer.personal_status = _("Identity review in progress")
                    signer.personal_next_step = _(
                        "Your identity is connected. An identity reviewer must approve it.",
                    )
                elif enrollment and enrollment.state == "active":
                    signer.personal_status = _("Waiting for sender")
                    signer.personal_next_step = _(
                        "Your identity is ready. The sender must continue the request.",
                    )
                else:
                    signer.personal_status = _("Waiting for sender")
                    signer.personal_next_step = _(
                        "The sender must arrange identity setup before you can sign.",
                    )
            elif signer.request_id.state == "waiting_external":
                signer.personal_status = _("External signing")
                signer.personal_next_step = (
                    _("Open the provider instructions to continue.")
                    if signer.request_id.external_journey_id
                    else _("The sender is preparing the external signing journey.")
                )
            elif signer.request_id.state in request_labels:
                signer.personal_status = request_labels[signer.request_id.state]
                signer.personal_next_step = (
                    _("Open the completed files.")
                    if signer.request_id.state == "completed"
                    else _("Open the archived signed PDF and Odoo Online certificate.")
                    if signer.request_id.state == "external_archived"
                    else _("Nothing else is needed from you.")
                )
            elif signer.request_id.state in {"sent", "viewed", "partial"}:
                signer.personal_status = _("Waiting for your turn")
                signer.personal_next_step = _(
                    "Another signer goes first. You will be notified when it is your turn.",
                )
            elif signer.request_id.state in {
                "signed_to_import",
                "validating",
                "evidence_incomplete",
                "action_required",
            }:
                signer.personal_status = _("Final checks")
                signer.personal_next_step = _(
                    "Nothing is needed from you while the sender completes the final checks.",
                )
            else:
                signer.personal_status = _("Not sent yet")
                signer.personal_next_step = _("The sender is still preparing the request.")

    def action_open_signing_identity(self):
        self.ensure_one()
        if self.partner_id != self.env.user.partner_id:
            msg = _("Only the assigned signer can open this signing identity.")
            raise AccessError(msg)
        enrollment = self.env["usl.sign.enrollment"].search(
            [
                ("partner_id", "=", self.partner_id.id),
                ("company_id", "=", self.request_id.company_id.id),
                ("state", "!=", "revoked"),
            ],
            limit=1,
        )
        if not enrollment:
            msg = _("The sender has not started identity setup yet.")
            raise UserError(msg)
        return {
            "type": "ir.actions.act_window",
            "name": _("My Signing Identity"),
            "res_model": "usl.sign.enrollment",
            "res_id": enrollment.id,
            "views": [
                (self.env.ref("usl_sign.sign_enrollment_my_form").id, "form"),
            ],
            "target": "current",
        }

    def action_open_external_signing(self):
        self.ensure_one()
        if self.partner_id != self.env.user.partner_id:
            msg = _("Only the assigned signer can open this signing journey.")
            raise AccessError(msg)
        journey = self.request_id.external_journey_id
        if self.request_id.state != "waiting_external" or not journey:
            msg = _("External signing instructions are not available yet.")
            raise UserError(msg)
        return journey.action_open_details()

    def action_open_request(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.request_id.name,
            "res_model": "sign.oca.request",
            "res_id": self.request_id.id,
            "views": [
                (self.env.ref("usl_sign.sign_request_signer_result_form").id, "form"),
            ],
            "target": "current",
        }

    def action_open_my_signature(self):
        """Open the useful destination directly from a My Signatures row."""
        self.ensure_one()
        if self.partner_id != self.env.user.partner_id:
            msg = _("Only the assigned signer can open this signing journey.")
            raise AccessError(msg)
        if self.state in {"signed", "external_recorded"} or self.request_id.state in {
            "completed",
            "external_archived",
            "evidence_incomplete",
            "validation_failed",
        }:
            return self.action_open_request()
        if self.can_open_signing_identity:
            return self.action_open_signing_identity()
        if self.can_open_external_signing:
            return self.action_open_external_signing()
        if self.is_allow_signature:
            return self.sign()
        return {
            "type": "ir.actions.act_window",
            "name": self.request_id.name,
            "res_model": self._name,
            "res_id": self.id,
            "views": [
                (self.env.ref("usl_sign.my_signature_form_usl").id, "form"),
            ],
            "target": "current",
        }

    def sign(self):
        self.ensure_one()
        if not self.is_allow_signature:
            msg = "You are not allowed to sign this document."
            raise ValidationError(msg)
        return {
            "type": "ir.actions.act_url",
            "url": self.access_url,
            "target": "new",
        }

    access_token_sha256 = fields.Char(readonly=True, copy=False, index=True)
    access_expires_at = fields.Datetime(readonly=True, copy=False)
    session_token_sha256 = fields.Char(readonly=True, copy=False, index=True)
    session_expires_at = fields.Datetime(readonly=True, copy=False)
    otp_exchange_token_sha256 = fields.Char(readonly=True, copy=False, index=True)
    otp_exchange_expires_at = fields.Datetime(readonly=True, copy=False)
    email_otp_salt = fields.Char(readonly=True, copy=False)
    email_otp_sha256 = fields.Char(readonly=True, copy=False)
    email_otp_expires_at = fields.Datetime(readonly=True, copy=False)
    email_otp_failure_count = fields.Integer(default=0, readonly=True, copy=False)
    email_otp_blocked_until = fields.Datetime(readonly=True, copy=False)
    email_otp_verified_at = fields.Datetime(readonly=True, copy=False)
    access_revoked = fields.Boolean(readonly=True, copy=False)
    viewed_at = fields.Datetime(readonly=True, copy=False)
    declined_at = fields.Datetime(readonly=True, copy=False)
    invitation_sent_at = fields.Datetime(readonly=True, copy=False)
    invitation_count = fields.Integer(default=0, readonly=True, copy=False)
    invitation_mail_id = fields.Many2one(
        "mail.mail", readonly=True, copy=False, ondelete="set null",
    )
    invitation_fallback_at = fields.Datetime(readonly=True, copy=False)
    invitation_delivery_state = fields.Selection(
        [
            ("not_queued", "Not queued"),
            ("queued", "Queued"),
            ("sent", "Sent"),
            ("failed", "Failed"),
            ("available_in_odoo", "Available in Odoo"),
            ("cancelled", "Cancelled"),
            ("resolved", "No longer needed"),
        ],
        compute="_compute_invitation_delivery_state",
    )
    reminder_sent_at = fields.Datetime(readonly=True, copy=False)
    reminder_count = fields.Integer(default=0, readonly=True, copy=False)
    authentication_method = fields.Selection(
        AUTHENTICATION_METHODS, readonly=True, copy=False,
    )
    consent_text = fields.Text(readonly=True, copy=False)
    consent_version = fields.Char(readonly=True, copy=False)
    consented_at = fields.Datetime(readonly=True, copy=False)
    signed_document_sha256 = fields.Char(readonly=True, copy=False)
    certificate_serial = fields.Char(readonly=True, copy=False)
    decline_reason = fields.Text(readonly=True, copy=False)
    access_failure_count = fields.Integer(default=0, readonly=True, copy=False)
    access_failure_window_at = fields.Datetime(readonly=True, copy=False)
    access_blocked_until = fields.Datetime(readonly=True, copy=False)
    last_access_failure_at = fields.Datetime(readonly=True, copy=False)

    @api.depends(
        "invitation_mail_id",
        "invitation_mail_id.state",
        "invitation_sent_at",
        "invitation_fallback_at",
        "signed_on",
    )
    def _compute_invitation_delivery_state(self):
        mapping = {
            "outgoing": "queued",
            "sent": "sent",
            "exception": "failed",
            "cancel": "cancelled",
        }
        for signer in self:
            if signer.signed_on:
                signer.invitation_delivery_state = "resolved"
                continue
            if signer.invitation_fallback_at:
                signer.invitation_delivery_state = "available_in_odoo"
                continue
            # Requesters may inspect delivery, but they must not gain access to the
            # internal outgoing-mail record.  Resolve it under sudo and expose only
            # the bounded product status below.
            mail_state = signer.sudo().invitation_mail_id.state
            signer.invitation_delivery_state = mapping.get(
                mail_state,
                "sent" if signer.invitation_sent_at else "not_queued",
            )

    @api.depends("signed_on", "partner_id", "state", "request_id.state")
    @api.depends_context("uid")
    def _compute_is_allow_signature(self):
        current_partner = self.env.user.partner_id
        for signer in self:
            order_ready = not signer.request_id.sudo().signer_ids.filtered(
                lambda other: signer.request_id.signing_order
                and other.sequence < signer.sequence
                and other.state != "signed",
            )
            signer.is_allow_signature = bool(
                signer.state in {"notified", "viewed", "authorized"}
                and not signer.access_revoked
                and signer.partner_id == current_partner
                and signer.request_id.state in {"sent", "viewed", "partial"}
                and order_ready,
            )

    @api.depends("access_token_sha256", "session_token_sha256")
    def _compute_access_url(self):
        for signer in self:
            signer.access_url = f"/sign/user/{signer.id}"

    @api.model_create_multi
    def create(self, vals_list):
        request_ids = {
            values.get("request_id") for values in vals_list if values.get("request_id")
        }
        if not self.env.su:
            for request in self.env["sign.oca.request"].browse(request_ids):
                if not request._user_can_coordinate():
                    msg = "Only the requester or a named coordinator may add signers."
                    raise AccessError(msg)
        for values in vals_list:
            values["access_token"] = False
        return super().create(vals_list)

    def _active_enrollment(self):
        self.ensure_one()
        return self.env["usl.sign.enrollment"].search(
            [
                ("partner_id", "=", self.partner_id.id),
                ("company_id", "=", self.request_id.company_id.id),
                ("state", "=", "active"),
            ],
            limit=1,
        )

    def _has_internal_signing_access(self):
        self.ensure_one()
        return bool(self._internal_signing_users())

    def _internal_signing_users(self):
        """Backend users who can open this exact signing assignment."""
        sign_users = self.env.ref("usl_sign.group_sign_user").sudo().all_user_ids
        users = self.sudo().partner_id.user_ids.filtered(
            lambda user: (
                user.active
                and not user.share
                and user in sign_users
                and self.request_id.company_id in user.company_ids
            ),
        )
        return users.sorted(lambda user: (user.name.casefold(), user.id))

    def _signing_activity_deadline(self):
        self.ensure_one()
        return (
            fields.Date.to_date(self.request_id.expires_at)
            if self.request_id.expires_at
            else fields.Date.context_today(self)
        )

    def _ensure_internal_signing_activities(self):
        """Create one current native activity per internal signer user.

        Invitations and reminders can be retried, so this intentionally updates
        an existing assignment instead of creating notification duplicates.
        """
        activity_type = self.env.ref("usl_sign.mail_activity_type_sign_document")
        # Sign-owned reminders are maintained by this controlled workflow.
        # UID 1 bypasses the distribution's user-facing permanent-deletion
        # guard without granting signers a general activity deletion right.
        activity_model = self.env["mail.activity"].with_user(SUPERUSER_ID)
        for signer in self.exists():
            if (
                signer.state not in {"notified", "viewed", "authorized"}
                or signer.access_revoked
                or signer.request_id.state not in {"sent", "viewed", "partial"}
            ):
                signer._close_internal_signing_activities()
                continue
            # Serialize invitation retries on the signer so duplicate HTTP jobs
            # cannot create duplicate activities.
            self.env.cr.execute(
                "SELECT id FROM sign_oca_request_signer WHERE id = %s FOR UPDATE",
                [signer.id],
            )
            users = signer._internal_signing_users()
            existing = activity_model.search(
                [
                    ("activity_type_id", "=", activity_type.id),
                    ("res_model", "=", signer._name),
                    ("res_id", "=", signer.id),
                    ("active", "=", True),
                ],
                order="id",
            )
            stale = existing.filtered(lambda activity: activity.user_id not in users)
            stale.unlink()
            for user in users:
                user_activities = (existing - stale).filtered(
                    lambda activity: activity.user_id == user,
                )
                summary = _(
                    "Review and sign: %(document)s",
                    document=signer.request_id.name,
                )
                note = _(
                    "<p><strong>%(sender)s</strong> asked you to review and sign "
                    "<strong>%(document)s</strong> as %(role)s.</p>"
                    "<p>Open this activity and choose <strong>Review and sign</strong>.</p>",
                    sender=escape(signer.request_id.user_id.name),
                    document=escape(signer.request_id.name),
                    role=escape(signer.role_id.name),
                )
                values = {
                    "summary": summary,
                    "note": note,
                    "date_deadline": signer._signing_activity_deadline(),
                }
                if user_activities:
                    user_activities[:1].write(values)
                    user_activities[1:].unlink()
                    continue
                # The signing invitation already owns email delivery. Quick
                # update keeps this as an Odoo inbox/activity notification and
                # avoids sending a second generic activity email.
                signer.sudo().with_context(
                    lang=user.lang,
                    mail_activity_quick_update=True,
                ).activity_schedule(
                    "usl_sign.mail_activity_type_sign_document",
                    user_id=user.id,
                    **values,
                )
        return True

    def _close_internal_signing_activities(self):
        if self:
            self.with_user(SUPERUSER_ID).activity_unlink(
                ["usl_sign.mail_activity_type_sign_document"],
            )
        return True

    def _issue_access_token(self):
        self.ensure_one()
        token = secrets.token_urlsafe(32)
        self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "access_token": False,
                "access_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "access_expires_at": min(
                    filter(
                        None,
                        [
                            self.request_id.expires_at,
                            fields.Datetime.now() + timedelta(days=7),
                        ],
                    ),
                ),
                "access_revoked": False,
            },
        )
        return token

    def _exchange_access_token(self, token):
        self.ensure_one()
        if self.signed_on or self.request_id.state not in {"sent", "viewed", "partial"}:
            # Terminal/revoked links are simply unavailable.  Do not mutate an
            # immutable signer or let repeated public replays grow evidence.
            msg = "This signing link is invalid, expired, or revoked."
            raise AccessError(msg)
        self._check_exchange_rate_limit()
        try:
            self._check_token(token, session=False)
        except AccessError:
            self._record_exchange_failure()
            raise
        if self.request_id.authentication_method == "email_otp":
            return self._issue_email_otp()
        session_token = secrets.token_urlsafe(32)
        self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "access_token_sha256": False,
                "access_expires_at": False,
                "session_token_sha256": hashlib.sha256(session_token.encode()).hexdigest(),
                "session_expires_at": fields.Datetime.now() + timedelta(minutes=30),
                "access_failure_count": 0,
                "access_failure_window_at": False,
                "access_blocked_until": False,
            },
        )
        return session_token

    @staticmethod
    def _email_otp_digest(code, salt):
        return hashlib.pbkdf2_hmac(
            "sha256", code.encode(), bytes.fromhex(salt), 200_000,
        ).hex()

    def _send_ephemeral_email(self, *, subject, body, queue=False):
        self.ensure_one()
        if not self.partner_id.email:
            msg = "The signer has no email address."
            raise UserError(msg)
        author = self.request_id.company_id.partner_id
        mail = self.env["mail.mail"].sudo().create(
            {
                "subject": subject,
                "body_html": body,
                "email_to": self.partner_id.email,
                "email_from": author.email_formatted,
                "author_id": author.id,
                "auto_delete": True,
                "model": self._name,
                "res_id": self.id,
            },
        )
        if queue:
            return mail
        try:
            mail.send(raise_exception=True)
        except Exception as error:
            mail.exists().unlink()
            msg = "The private signing email could not be delivered."
            raise UserError(msg) from error

    def _issue_email_otp(self):
        self.ensure_one()
        code = f"{secrets.randbelow(100_000_000):08d}"
        salt = secrets.token_hex(16)
        exchange_token = secrets.token_urlsafe(32)
        now = fields.Datetime.now()
        self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "access_token_sha256": False,
                "access_expires_at": False,
                "otp_exchange_token_sha256": hashlib.sha256(
                    exchange_token.encode(),
                ).hexdigest(),
                "otp_exchange_expires_at": now + timedelta(minutes=10),
                "email_otp_salt": salt,
                "email_otp_sha256": self._email_otp_digest(code, salt),
                "email_otp_expires_at": now + timedelta(minutes=10),
                "email_otp_failure_count": 0,
                "email_otp_blocked_until": False,
            },
        )
        body = (
            f"<p>Hello {escape(self.partner_id.name)},</p>"
            f"<p>Your one-time {escape(self.request_id.company_id.name)} document-signing "
            f"verification code is <strong>{code}</strong>.</p>"
            "<p>It expires in 10 minutes. Do not forward it.</p>"
        )
        self._send_ephemeral_email(
            subject=f"Your {self.request_id.company_id.name} signing code",
            body=body,
        )
        self.request_id._append_event(
            "email_otp_issued",
            signer=self,
            authentication_method="email_otp",
            payload={"expires_in_minutes": 10},
        )
        return {"otp_required": True, "exchange_token": exchange_token}

    def _verify_email_otp(self, exchange_token, code):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT id FROM sign_oca_request_signer WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset()
        now = fields.Datetime.now()
        exchange_digest = hashlib.sha256((exchange_token or "").encode()).hexdigest()
        if (
            not self.otp_exchange_token_sha256
            or not secrets.compare_digest(
                self.otp_exchange_token_sha256, exchange_digest,
            )
            or not self.otp_exchange_expires_at
            or self.otp_exchange_expires_at <= now
            or not self.email_otp_expires_at
            or self.email_otp_expires_at <= now
            or self.access_revoked
        ):
            msg = "The verification session is invalid or expired."
            raise AccessError(msg)
        if self.email_otp_blocked_until and self.email_otp_blocked_until > now:
            msg = "Too many incorrect verification codes were entered."
            raise AccessError(msg)
        candidate = self._email_otp_digest((code or "").strip(), self.email_otp_salt)
        if not secrets.compare_digest(self.email_otp_sha256 or "", candidate):
            failures = self.email_otp_failure_count + 1
            self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
                {
                    "email_otp_failure_count": failures,
                    "email_otp_blocked_until": now + timedelta(minutes=15)
                    if failures >= 5
                    else False,
                },
            )
            self.request_id._append_event(
                "email_otp_rejected",
                signer=self,
                authentication_method="email_otp",
                payload={"attempt_count": failures, "blocked": failures >= 5},
            )
            msg = "The verification code is incorrect."
            raise AccessError(msg)
        session_token = secrets.token_urlsafe(32)
        self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "session_token_sha256": hashlib.sha256(session_token.encode()).hexdigest(),
                "session_expires_at": now + timedelta(minutes=30),
                "otp_exchange_token_sha256": False,
                "otp_exchange_expires_at": False,
                "email_otp_salt": False,
                "email_otp_sha256": False,
                "email_otp_expires_at": False,
                "email_otp_failure_count": 0,
                "email_otp_blocked_until": False,
                "email_otp_verified_at": now,
            },
        )
        self.request_id._append_event(
            "email_otp_verified",
            signer=self,
            authentication_method="email_otp",
        )
        return session_token

    def _check_exchange_rate_limit(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT id FROM sign_oca_request_signer WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset(
            ["access_failure_count", "access_failure_window_at", "access_blocked_until"],
        )
        now = fields.Datetime.now()
        if self.access_blocked_until and self.access_blocked_until > now:
            msg = "This signing link is temporarily unavailable."
            raise AccessError(msg)
        if self.access_failure_window_at and self.access_failure_window_at < now - timedelta(
            minutes=15,
        ):
            self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
                {
                    "access_failure_count": 0,
                    "access_failure_window_at": False,
                    "access_blocked_until": False,
                },
            )

    def _record_exchange_failure(self):
        self.ensure_one()
        now = fields.Datetime.now()
        window = self.access_failure_window_at or now
        count = self.access_failure_count + 1
        values = {
            "access_failure_count": count,
            "access_failure_window_at": window,
            "last_access_failure_at": now,
        }
        if count >= 5:
            values["access_blocked_until"] = now + timedelta(minutes=15)
        self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(values)
        self.request_id._append_event(
            "signing_link_rejected",
            signer=self,
            payload={"attempt_count": count, "blocked": count >= 5},
        )

    def _check_token(self, token, *, session=True):
        self.ensure_one()
        digest = hashlib.sha256((token or "").encode()).hexdigest()
        expected = self.session_token_sha256 if session else self.access_token_sha256
        expiry = self.session_expires_at if session else self.access_expires_at
        if (
            not expected
            or not secrets.compare_digest(expected, digest)
            or not expiry
            or expiry <= fields.Datetime.now()
            or self.access_revoked
        ):
            msg = "This signing link is invalid, expired, or revoked."
            raise AccessError(msg)
        if self.request_id.state not in {"sent", "viewed", "partial"}:
            msg = "This request is not available for signature."
            raise AccessError(msg)
        if self.signed_on:
            msg = "This signer has already completed the request."
            raise AccessError(msg)
        if self.request_id.signing_order and self.request_id.signer_ids.filtered(
            lambda other: other.sequence < self.sequence and other.state != "signed",
        ):
            msg = "Another signer must complete the document first."
            raise AccessError(msg)
        return True

    def _send_signer_invitation(self, force=False, reminder=False):
        for signer in self:
            if signer.invitation_sent_at and not force:
                continue
            token = signer._issue_access_token()
            link = f"{signer.get_base_url()}/sign/document/{signer.id}/{token}"
            body = self.env["ir.qweb"]._render(
                "usl_sign.sign_invitation_body",
                {"signer": signer, "link": link, "reminder": reminder},
                engine="ir.qweb",
                minimal_qcontext=True,
            )
            queued_mail = signer._send_ephemeral_email(
                subject="Signature reminder" if reminder else "Document to review and sign",
                body=body,
                queue=True,
            )
            values = {
                "invitation_sent_at": fields.Datetime.now(),
                "invitation_count": signer.invitation_count + 1,
                "invitation_mail_id": queued_mail.id,
                "state": "notified",
            }
            if reminder:
                values.update(
                    {
                        "reminder_sent_at": fields.Datetime.now(),
                        "reminder_count": signer.reminder_count + 1,
                    },
                )
            signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(values)
            signer.request_id._append_event(
                "invitation_queued",
                signer=signer,
                payload={"reminder": reminder},
            )
            signer._ensure_internal_signing_activities()
        return True

    def _mark_viewed(self):
        for signer in self.filtered(lambda row: not row.viewed_at):
            signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
                {"viewed_at": fields.Datetime.now(), "state": "viewed"},
            )
            if signer.request_id.state == "sent":
                signer.request_id._transition("viewed", "document_viewed", signer=signer)

    def action_sign(
        self,
        items,
        access_token=False,
        document_sha256=False,
        latitude=False,
        longitude=False,
        consent=False,
        location=None,
        browser_context=None,
    ):
        self.ensure_one()
        # Serialize Standard submissions on the request. Without this lock, two
        # unordered signers can both render from the same revision and the last
        # transaction to commit can erase the first signer's fields.
        self.env.cr.execute(
            "SELECT id FROM sign_oca_request WHERE id = %s FOR UPDATE",
            [self.request_id.id],
        )
        self.request_id.invalidate_recordset(["data", "current_hash", "state"])
        self.invalidate_recordset(
            ["access_revoked", "session_token_sha256", "session_expires_at", "state"],
        )
        self._check_token(access_token, session=True)
        if self.request_id.requested_trust != "standard":
            msg = "This request requires the strong personal ceremony."
            raise ValidationError(msg)
        if not consent:
            msg = "Explicit electronic-signature consent is required."
            raise ValidationError(msg)
        context = self._normalized_signing_context(
            location=location,
            browser_context=browser_context,
            latitude=latitude,
            longitude=longitude,
        )
        return self._apply_standard_signature(
            items,
            reviewed_document_sha256=document_sha256,
            signing_context=context,
        )

    @api.model
    def _normalized_signing_context(
        self,
        *,
        location=None,
        browser_context=None,
        latitude=False,
        longitude=False,
    ):
        """Validate bounded browser evidence without claiming a fingerprint."""
        allowed_location_states = {
            "granted",
            "refused",
            "unavailable",
            "unsupported",
            "timeout",
        }
        raw_location = location if isinstance(location, dict) else {}
        status = raw_location.get("status")
        if not status and latitude is not False and longitude is not False:
            status = "granted"
            raw_location = {
                "status": status,
                "latitude": latitude,
                "longitude": longitude,
            }
        status = status if status in allowed_location_states else "unavailable"
        normalized_location = {"status": status}
        if status == "granted":
            try:
                normalized_latitude = float(raw_location["latitude"])
                normalized_longitude = float(raw_location["longitude"])
                accuracy = float(raw_location.get("accuracy") or 0)
            except (KeyError, TypeError, ValueError) as error:
                msg = "The browser location payload is invalid."
                raise ValidationError(msg) from error
            if (
                not -90 <= normalized_latitude <= 90
                or not -180 <= normalized_longitude <= 180
                or not 0 <= accuracy <= 10_000_000
            ):
                msg = "The browser location payload is outside accepted bounds."
                raise ValidationError(msg)
            normalized_location.update(
                {
                    "latitude": normalized_latitude,
                    "longitude": normalized_longitude,
                    "accuracy_metres": accuracy,
                },
            )

        if browser_context is None:
            browser_context = {}
        if not isinstance(browser_context, dict):
            msg = "The browser context payload is invalid."
            raise ValidationError(msg)
        allowed_string_keys = {
            "language",
            "platform",
            "timezone",
            "user_agent",
        }
        normalized_browser = {}
        for key in allowed_string_keys:
            value = browser_context.get(key)
            if value in (None, False, ""):
                continue
            if not isinstance(value, str) or len(value) > 512:
                msg = "The browser context payload is invalid."
                raise ValidationError(msg)
            normalized_browser[key] = value
        languages = browser_context.get("languages")
        if languages not in (None, False):
            if (
                not isinstance(languages, list)
                or len(languages) > 20
                or any(not isinstance(value, str) or len(value) > 64 for value in languages)
            ):
                msg = "The browser language payload is invalid."
                raise ValidationError(msg)
            normalized_browser["languages"] = languages
        for key in ("hardware_concurrency", "device_memory", "max_touch_points"):
            value = browser_context.get(key)
            if value in (None, False):
                continue
            if not isinstance(value, int | float) or not 0 <= value <= 1_000_000:
                msg = "The browser capability payload is invalid."
                raise ValidationError(msg)
            normalized_browser[key] = value
        for key in ("screen", "viewport", "client_hints"):
            value = browser_context.get(key)
            if value in (None, False):
                continue
            serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
            if not isinstance(value, dict) or len(serialized) > 4_096:
                msg = "The browser display payload is invalid."
                raise ValidationError(msg)
            normalized_browser[key] = value
        return {
            "format": "usl-sign-observed-context-v1",
            "browser": normalized_browser,
            "location": normalized_location,
            "network": self.env["sign.oca.request"]._server_network_context(),
        }

    def _prepare_signing_candidate(
        self,
        items,
        *,
        reviewed_document_sha256,
        preserve_pdf_signatures=False,
    ):
        """Render one signer's frozen fields without mutating the live request."""
        self.ensure_one()
        request = self.request_id
        current_document = field_content(request.data)
        current_sha256 = hashlib.sha256(current_document).hexdigest()
        if (
            not isinstance(reviewed_document_sha256, str)
            or len(reviewed_document_sha256) != 64
            or not secrets.compare_digest(reviewed_document_sha256, current_sha256)
        ):
            msg = _(
                "The document changed after you reviewed it. Reload it, review the "
                "latest revision, and sign again.",
            )
            raise ValidationError(msg)
        frozen_layout = json.loads(json.dumps(request.frozen_layout or {}))
        completed_layout = json.loads(
            json.dumps(request.signatory_data or request.frozen_layout or {}),
        )
        if not isinstance(items, dict):
            msg = "The signing payload is invalid."
            raise ValidationError(msg)
        reader = PdfReader(BytesIO(current_document))
        writer = PdfWriter()
        pages = dict(enumerate(reader.pages, start=1))
        incremental_fields = []
        pades_appearance_item_id = False
        primary_signature_ids = request._strong_primary_signature_item_ids(frozen_layout)
        visible_field_values = 0
        signer_fields = {}
        for key, configured in sorted(frozen_layout.items(), key=lambda row: int(row[0])):
            if int(configured["role_id"]) != self.role_id.id:
                continue
            submitted = items.get(str(key), items.get(key))
            if not isinstance(submitted, dict) or "value" not in submitted:
                msg = "The signing payload is incomplete."
                raise ValidationError(msg)
            item = json.loads(json.dumps(configured))
            value = submitted.get("value")
            if item.get("field_type") == "check":
                if not isinstance(value, bool):
                    msg = "A checkbox value must be true or false."
                    raise ValidationError(msg)
            elif value is not False and value is not None and not isinstance(value, str):
                msg = "A signing field contains an invalid value."
                raise ValidationError(msg)
            elif isinstance(value, str) and len(value) > 2_000_000:
                msg = "A signing field exceeds the accepted size."
                raise ValidationError(msg)
            item["value"] = value
            self._check_signable(item)
            page_number = int(item["page"])
            if page_number not in pages:
                msg = "A signing field references an invalid page."
                raise ValidationError(msg)
            page = pages[page_number]
            box = getattr(page, "mediabox", None) or page.mediaBox
            overlay = self._get_pdf_page(item, box)
            if item.get("required") and item.get("value") and not overlay:
                msg = "A required signing field could not be rendered."
                raise ValidationError(msg)
            if overlay:
                visible_field_values += 1
                if (
                    preserve_pdf_signatures
                    and item.get("field_type") == "signature"
                    and str(key) in primary_signature_ids
                ):
                    # DSS renders the primary adopted signature as the native
                    # PAdES field appearance in the same increment as the
                    # personal certificate signature. This avoids a separate
                    # page-content update that PDF validators must reject.
                    pades_appearance_item_id = str(key)
                elif preserve_pdf_signatures:
                    overlay_stream = BytesIO()
                    overlay_writer = PdfWriter()
                    _add_page(overlay_writer, overlay)
                    overlay_writer.write(overlay_stream)
                    incremental_fields.append(
                        {
                            "name": f"usl_sign_{int(key)}",
                            "page": page_number,
                            "position_x": float(item["position_x"]),
                            "position_y": float(item["position_y"]),
                            "width": float(item["width"]),
                            "height": float(item["height"]),
                            "document": overlay_stream.getvalue(),
                        },
                    )
                else:
                    merge = getattr(page, "merge_page", None) or getattr(page, "mergePage")
                    merge(overlay)
            pages[page_number] = page
            completed_layout[str(key)] = item
            signer_fields[str(key)] = item
        if preserve_pdf_signatures:
            if not visible_field_values:
                msg = "The signing payload contains no visible field values."
                raise ValidationError(msg)
            candidate = (
                request._sign_dss_client().fill_signing_fields(
                    current_document,
                    incremental_fields,
                )
                if incremental_fields
                else current_document
            )
        else:
            for page in pages.values():
                _add_page(writer, page)
            stream = BytesIO()
            writer.write(stream)
            candidate = stream.getvalue()
        serialized_fields = json.dumps(
            signer_fields,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return {
            "base_document_sha256": current_sha256,
            "candidate_data": candidate,
            "candidate_document_sha256": hashlib.sha256(candidate).hexdigest(),
            "completed_layout": completed_layout,
            "field_values_sha256": hashlib.sha256(serialized_fields).hexdigest(),
            "pades_appearance_item_id": pades_appearance_item_id,
        }

    def _store_signing_context_evidence(self, signing_context):
        self.ensure_one()
        raw = json.dumps(
            signing_context,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return self.request_id._create_evidence(
            "authentication",
            f"{self.request_id.name}-{self.id}-observed-context.json",
            raw,
            mimetype="application/json",
            signer=self,
            metadata={
                "format": signing_context["format"],
                "location_status": signing_context["location"]["status"],
            },
        )

    def _apply_standard_signature(
        self,
        items,
        *,
        reviewed_document_sha256,
        signing_context,
    ):
        request = self.request_id
        candidate = self._prepare_signing_candidate(
            items,
            reviewed_document_sha256=reviewed_document_sha256,
        )
        completed_layout = candidate["completed_layout"]
        signed_pdf = candidate["candidate_data"]
        digest = hashlib.sha256(signed_pdf).hexdigest()
        now = fields.Datetime.now()
        consent_text = request.consent_text_snapshot
        request.with_context(usl_sign_working_pdf=INTERNAL_OPERATION).write(
            {
                "data": field_value(signed_pdf),
                "current_hash": digest,
                "signatory_data": completed_layout,
            },
        )
        self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "signed_on": now,
                "signature_hash": digest,
                "signed_document_sha256": digest,
                "state": "signed",
                "latitude": signing_context["location"].get("latitude") or 0,
                "longitude": signing_context["location"].get("longitude") or 0,
                "authentication_method": request.authentication_method or "secure_link",
                "consent_text": consent_text,
                "consent_version": "1",
                "consented_at": now,
                "access_revoked": True,
                "session_token_sha256": False,
                "session_expires_at": False,
            },
        )
        consent_payload = {
            "signer_id": self.id,
            "partner_id": self.partner_id.id,
            "consent": consent_text,
            "version": "1",
            "consented_at": fields.Datetime.to_string(now),
            "reviewed_document_sha256": reviewed_document_sha256,
            "signed_document_sha256": digest,
            "authentication_method": self.authentication_method,
            "field_values_sha256": candidate["field_values_sha256"],
        }
        request._create_evidence(
            "consent",
            f"{request.name}-{self.id}-consent.json",
            json.dumps(consent_payload, sort_keys=True).encode(),
            mimetype="application/json",
            signer=self,
        )
        context_evidence = self._store_signing_context_evidence(signing_context)
        request._append_event(
            "standard_signature_applied",
            signer=self,
            authentication_method=self.authentication_method,
            payload={
                "reviewed_document_sha256": reviewed_document_sha256,
                "signed_document_sha256": digest,
                "consent_version": "1",
                "evidence_context_sha256": context_evidence.sha256,
                "location_status": signing_context["location"]["status"],
            },
        )
        self._close_internal_signing_activities()
        self._activate_next_signer_or_finish()
        return {"type": "ir.actions.act_url", "url": "/sign/result/success"}

    def _activate_next_signer_or_finish(self):
        self.ensure_one()
        request = self.request_id
        pending = request.signer_ids.filtered(lambda signer: signer.state != "signed").sorted(
            lambda row: (row.sequence, row.id),
        )
        if pending:
            if request.state in {"sent", "viewed"}:
                request._transition("partial", "request_partially_signed", signer=self)
            if request.signing_order:
                pending[0]._send_signer_invitation()
            return
        request._start_final_validation()

    def action_decline(self, reason, access_token=False):
        self.ensure_one()
        self._check_token(access_token, session=True)
        if not reason:
            msg = "Record why the document is declined."
            raise ValidationError(msg)
        now = fields.Datetime.now()
        self.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "state": "declined",
                "declined_at": now,
                "decline_reason": reason,
                "access_revoked": True,
            },
        )
        self.request_id._create_evidence(
            "decline",
            f"{self.request_id.name}-{self.id}-decline.json",
            json.dumps(
                {"signer_id": self.id, "reason": reason, "declined_at": fields.Datetime.to_string(now)},
                sort_keys=True,
            ).encode(),
            mimetype="application/json",
            signer=self,
        )
        self.request_id.signer_ids.filtered(
            lambda signer: signer != self and signer.state != "signed",
        ).with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {"state": "cancelled", "access_revoked": True},
        )
        self.request_id._close_outstanding_work("request_declined")
        self.request_id._transition("declined", "request_declined", signer=self)
        return True

    def get_info(self, access_token=False):
        self.ensure_one()
        if access_token:
            self._check_token(access_token, session=True)
        layout = json.loads(json.dumps(self.request_id.frozen_layout or {}))
        field_ids = {
            int(item["field_id"])
            for item in layout.values()
            if isinstance(item, dict) and item.get("field_id")
        }
        fields_by_id = {
            field.id: field
            for field in self.env["sign.oca.field"].sudo().browse(field_ids).exists()
        }
        for item in layout.values():
            if not isinstance(item, dict):
                continue
            field = fields_by_id.get(int(item.get("field_id") or 0))
            if not field:
                item.setdefault("kind", "text")
                item.setdefault("technical_type", item.get("field_type", "text"))
                continue
            item.update(
                {
                    "name": item.get("name") or field.name,
                    "kind": _field_kind(field),
                    "field_type": field.field_type,
                    "technical_type": field.field_type,
                    "default_value": item.get("default_value") or field.default_value or False,
                },
            )
        return {
            "role_id": self.role_id.id if not self.signed_on else False,
            "name": self.request_id.name,
            "company_name": self.request_id.company_id.name,
            "signer_role_name": self.role_id.name,
            "items": layout,
            "to_sign": not self.signed_on and not self.access_revoked,
            "ask_location": self.request_id.ask_location,
            "partner": {
                "id": self.partner_id.id,
                "name": self.partner_id.name,
                "email": self.partner_id.email,
                "phone": self.partner_id.phone,
            },
            "requested_trust": self.request_id.requested_trust,
            "trust_label": dict(TRUST_LEVELS)[self.request_id.requested_trust],
            "consent_text": self.request_id.consent_text_snapshot,
            "document_sha256": hashlib.sha256(
                field_content(self.request_id.data),
            ).hexdigest(),
        }

    def write(self, vals):
        protected = {
            "state",
            "signed_on",
            "signature_hash",
            "access_token_sha256",
            "access_token",
            "access_expires_at",
            "session_token_sha256",
            "session_expires_at",
            "otp_exchange_token_sha256",
            "otp_exchange_expires_at",
            "email_otp_salt",
            "email_otp_sha256",
            "email_otp_expires_at",
            "email_otp_failure_count",
            "email_otp_blocked_until",
            "email_otp_verified_at",
            "access_revoked",
            "viewed_at",
            "declined_at",
            "invitation_sent_at",
            "invitation_count",
            "invitation_mail_id",
            "invitation_fallback_at",
            "reminder_sent_at",
            "reminder_count",
            "authentication_method",
            "consent_text",
            "consent_version",
            "consented_at",
            "signed_document_sha256",
            "certificate_serial",
            "decline_reason",
            "access_failure_count",
            "access_failure_window_at",
            "access_blocked_until",
            "last_access_failure_at",
        }
        if protected.intersection(vals) and self.env.context.get(
            "usl_sign_signer_transition",
        ) is not INTERNAL_OPERATION:
            msg = "Use a controlled signer action to change signing evidence."
            raise ValidationError(msg)
        if (
            vals
            and self.env.context.get("usl_sign_signer_transition") is not INTERNAL_OPERATION
            and not self.env.su
        ):
            for signer in self:
                if not signer.request_id._user_can_coordinate():
                    msg = "Only the requester or a named coordinator may edit signers."
                    raise AccessError(
                        msg,
                    )
                if signer.request_id.state != "draft":
                    msg = "Signers can only be edited while the request is a draft."
                    raise ValidationError(msg)
        if self.filtered(lambda signer: signer.signed_on) and set(vals) - {
            "message_follower_ids",
            "activity_ids",
        }:
            msg = "A completed signer record is immutable."
            raise ValidationError(msg)
        if {"request_id", "partner_id", "role_id", "sequence"}.intersection(vals) and self.filtered(
            lambda signer: signer.request_id.state not in MUTABLE_REQUEST_STATES,
        ):
            msg = "Signer identities, roles and order are frozen after sending."
            raise ValidationError(msg)
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            for signer in self:
                if not signer.request_id._user_can_coordinate():
                    msg = "Only the requester or a named coordinator may remove signers."
                    raise AccessError(msg)
        if self.filtered(lambda signer: signer.request_id.state != "draft"):
            msg = "Signers can only be removed while the request is a draft."
            raise ValidationError(msg)
        requests = self.mapped("request_id")
        removed_roles_by_request = {request.id: set() for request in requests}
        for signer in self:
            removed_roles_by_request[signer.request_id.id].add(signer.role_id.id)
        result = super().unlink()
        requests.invalidate_recordset(
            ["signer_ids", "signatory_data", "editor_revision", "editor_operation_log"],
        )
        for request in requests:
            orphaned_role_ids = removed_roles_by_request[request.id] - set(
                self.search([("request_id", "=", request.id)]).mapped("role_id").ids,
            )
            if not orphaned_role_ids:
                continue
            existing_data = request.signatory_data or {}
            signatory_data = {
                str(key): value
                for key, value in existing_data.items()
                if int(value.get("role_id") or 0) not in orphaned_role_ids
            }
            removed_field_count = len(existing_data) - len(signatory_data)
            if not removed_field_count:
                continue
            request.write(
                {
                    "signatory_data": signatory_data,
                    "editor_revision": request.editor_revision + 1,
                    "editor_operation_log": {},
                },
            )
            request.invalidate_recordset(
                ["signatory_data", "editor_revision", "editor_operation_log"],
            )
            request.message_post(
                body=_(
                    "Removed %(count)s signing field(s) assigned only to the removed signer.",
                    count=removed_field_count,
                ),
            )
        self.env["sign.oca.request"].invalidate_model(
            ["signer_ids", "signatory_data", "editor_revision", "editor_operation_log"],
        )
        return result
