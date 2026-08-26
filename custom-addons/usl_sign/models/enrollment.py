import hashlib
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .constants import INTERNAL_OPERATION

IDENTITY_REVIEW_STANDARD = "usl-identity-review-v1"


class SignEnrollment(models.Model):
    _name = "usl.sign.enrollment"
    _description = "Strong Signer Identity Enrolment"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "partner_id"
    _order = "create_date desc, id desc"

    partner_id = fields.Many2one(
        "res.partner", required=True, index=True, ondelete="restrict",
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True,
    )
    state = fields.Selection(
        [
            ("pending_pocket", "Waiting for account connection"),
            ("pending_review", "Ready for review"),
            ("active", "Ready"),
            ("revoked", "Revoked"),
        ],
        required=True,
        default="pending_pocket",
    )
    relationship_basis = fields.Selection(
        [
            ("pocket_id", "Existing Pocket ID user"),
            ("employee", "Employee"),
            ("contractor", "Contractor"),
            ("recurring_partner", "Recurring partner"),
        ],
        required=True,
    )
    relationship_reference = fields.Char(required=True)
    reviewer_id = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    reviewed_at = fields.Datetime(readonly=True)
    policy_version = fields.Char(required=True, default=IDENTITY_REVIEW_STANDARD)
    review_standard_label = fields.Char(compute="_compute_review_standard_label")
    review_note = fields.Text()
    pocket_issuer = fields.Char(readonly=True, copy=False, index=True)
    pocket_subject = fields.Char(readonly=True, copy=False, index=True)
    pocket_subject_fingerprint = fields.Char(readonly=True, copy=False, index=True)
    pocket_email = fields.Char(readonly=True, copy=False)
    pocket_display_name = fields.Char(readonly=True, copy=False)
    pocket_linked_at = fields.Datetime(readonly=True, copy=False)
    pocket_last_authorized_at = fields.Datetime(readonly=True, copy=False)
    pocket_authentication_method = fields.Char(readonly=True, copy=False)
    invitation_token_sha256 = fields.Char(readonly=True, copy=False, index=True)
    invitation_expires_at = fields.Datetime(readonly=True, copy=False)
    invitation_sent_at = fields.Datetime(readonly=True, copy=False)
    invitation_mail_id = fields.Many2one(
        "mail.mail",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    invitation_delivery_state = fields.Selection(
        [
            ("not_sent", "Not sent"),
            ("queued", "Queued"),
            ("sent", "Sent"),
            ("failed", "Delivery failed"),
        ],
        compute="_compute_invitation_delivery_state",
    )
    revoked_at = fields.Datetime(readonly=True)
    status_changed_at = fields.Datetime(readonly=True)
    status_changed_by_id = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    status_reason = fields.Text(readonly=True)
    revocation_reason = fields.Text(readonly=True)

    def init(self):
        """Enforce one current enrolment without requiring the btree_gist extension."""
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS usl_sign_enrollment_current_unique
                ON usl_sign_enrollment (partner_id, company_id)
             WHERE state <> 'revoked'
            """,
        )

    @api.constrains("relationship_reference")
    def _check_relationship_reference(self):
        if any(not (enrollment.relationship_reference or "").strip() for enrollment in self):
            msg = "Record the employee, contract, partner, or review reference used."
            raise ValidationError(msg)

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            if "relationship_reference" not in values:
                partner_id = int(values.get("partner_id") or 0)
                if partner_id:
                    values["relationship_reference"] = f"res.partner,{partner_id}"
        return super().create(values_list)

    @api.depends("policy_version")
    def _compute_review_standard_label(self):
        for enrollment in self:
            version = enrollment.policy_version or IDENTITY_REVIEW_STANDARD
            if version == IDENTITY_REVIEW_STANDARD:
                enrollment.review_standard_label = _(
                    "USL identity review checklist · version 1",
                )
            else:
                enrollment.review_standard_label = _(
                    "Identity review checklist · %(version)s",
                    version=version,
                )

    @api.depends(
        "invitation_mail_id",
        "invitation_mail_id.state",
        "invitation_sent_at",
    )
    def _compute_invitation_delivery_state(self):
        mapping = {
            "outgoing": "queued",
            "sent": "sent",
            "exception": "failed",
            "cancel": "failed",
        }
        for enrollment in self:
            mail_state = enrollment.sudo().invitation_mail_id.state
            enrollment.invitation_delivery_state = mapping.get(
                mail_state,
                "sent" if enrollment.invitation_sent_at else "not_sent",
            )

    def _check_reviewer_access(self):
        if not self.env.user.has_group("usl_sign.group_sign_identity_reviewer"):
            msg = "Identity reviewer access is required."
            raise AccessError(msg)

    @api.model
    def action_open_my_identity(self):
        """Open the current user's identity directly instead of a technical list."""
        enrollment = self.search(
            [
                ("partner_id", "=", self.env.user.partner_id.id),
                ("company_id", "=", self.env.company.id),
                ("state", "!=", "revoked"),
            ],
            limit=1,
        ) or self.search(
            [
                ("partner_id", "=", self.env.user.partner_id.id),
                ("company_id", "=", self.env.company.id),
            ],
            order="create_date desc, id desc",
            limit=1,
        )
        if not enrollment:
            msg = _(
                "No signing identity has been set up for you in this company. "
                "Ask an identity reviewer to start the setup."
            )
            raise UserError(msg)
        return {
            "type": "ir.actions.act_window",
            "name": _("My Signing Identity"),
            "res_model": self._name,
            "res_id": enrollment.id,
            "views": [
                (self.env.ref("usl_sign.sign_enrollment_my_form").id, "form"),
            ],
            "target": "current",
        }

    def _review_assignee(self):
        self.ensure_one()
        reviewers = self.env.ref(
            "usl_sign.group_sign_identity_reviewer",
        ).sudo().all_user_ids.filtered(
            lambda user: user.active and self.company_id in user.company_ids,
        )
        return self.create_uid if self.create_uid in reviewers else reviewers.sorted("id")[:1]

    def _schedule_identity_review(self):
        self.ensure_one()
        assignee = self._review_assignee()
        if not assignee:
            return
        self.sudo().activity_schedule(
            "mail.mail_activity_data_todo",
            user_id=assignee.id,
            summary=_("Review signing identity for %(name)s", name=self.partner_id.name),
            note=_(
                "Pocket ID is connected. Confirm that this account belongs to the known person, then approve or revoke the identity link.",
            ),
        )
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS usl_sign_enrollment_pocket_unique
                ON usl_sign_enrollment (company_id, pocket_issuer, pocket_subject)
             WHERE state <> 'revoked' AND pocket_subject IS NOT NULL
            """,
        )

    def action_confirm_identity(self):
        self.ensure_one()
        if self.state != "pending_review" or not self.pocket_subject:
            msg = "Only a pending identity review can be confirmed."
            raise ValidationError(msg)
        self._check_reviewer_access()
        self.with_context(usl_sign_enrollment_transition=INTERNAL_OPERATION).write(
            {
                "state": "active",
                "reviewer_id": self.env.user.id,
                "reviewed_at": fields.Datetime.now(),
                "status_changed_at": fields.Datetime.now(),
                "status_changed_by_id": self.env.user.id,
                "status_reason": "Pocket ID identity reviewed",
            },
        )
        self.sudo().activity_ids.unlink()
        waiting_requests = self.env["sign.oca.request"].sudo().search(
            [
                ("state", "=", "waiting_enrollment"),
                ("company_id", "=", self.company_id.id),
                ("signer_ids.partner_id", "=", self.partner_id.id),
            ],
        )
        for sign_request in waiting_requests:
            missing = sign_request.signer_ids.filtered(
                lambda signer: not signer._active_enrollment(),
            )
            if not missing:
                sign_request.action_resume_after_enrollment()
        return True

    def _create_invitation_link(self):
        self.ensure_one()
        self._check_reviewer_access()
        if self.state != "pending_pocket":
            msg = "Only an enrolment waiting for Pocket ID can be connected."
            raise ValidationError(msg)
        token = secrets.token_urlsafe(32)
        self.with_context(usl_sign_enrollment_transition=INTERNAL_OPERATION).write(
            {
                "invitation_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "invitation_expires_at": fields.Datetime.now() + timedelta(hours=24),
            },
        )
        base = self.env["ir.config_parameter"].sudo().get_str("web.base.url").rstrip("/")
        return f"{base}/sign/enroll/{self.id}/{token}"

    def action_send_invitation(self):
        self.ensure_one()
        if not self.partner_id.email:
            msg = "Add an email address to this person before sending the setup invitation."
            raise UserError(msg)
        link = self._create_invitation_link()
        body = self.env["ir.qweb"]._render(
            "usl_sign.strong_enrollment_invitation_body",
            {"enrollment": self, "link": link},
            engine="ir.qweb",
            minimal_qcontext=True,
        )
        author = self.company_id.partner_id
        mail = self.env["mail.mail"].sudo().create(
            {
                "subject": _("Set up strong signatures for %(company)s", company=self.company_id.name),
                "body_html": body,
                "email_to": self.partner_id.email,
                "email_from": author.email_formatted,
                "author_id": author.id,
                "auto_delete": True,
                "model": self._name,
                "res_id": self.id,
            },
        )
        self.with_context(usl_sign_enrollment_transition=INTERNAL_OPERATION).write(
            {
                "invitation_sent_at": fields.Datetime.now(),
                "invitation_mail_id": mail.id,
            },
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Setup invitation queued"),
                "message": _("The invitation will be sent to %(email)s.", email=self.partner_id.email),
                "type": "success",
                "sticky": False,
            },
        }

    def action_copy_invitation(self):
        self.ensure_one()
        link = self._create_invitation_link()
        return {
            "type": "ir.actions.client",
            "tag": "usl_sign.copy_setup_link",
            "params": {
                "url": link,
                "next": {
                    "type": "ir.actions.act_window",
                    "name": _("Copy setup link"),
                    "res_model": "usl.sign.enrollment.invitation",
                    "views": [
                        (
                            self.env.ref(
                                "usl_sign.sign_enrollment_invitation_form",
                            ).id,
                            "form",
                        ),
                    ],
                    "target": "new",
                    "context": {
                        "default_enrollment_id": self.id,
                        "default_invitation_url": link,
                    },
                },
            },
        }

    def _check_invitation(self, token):
        self.ensure_one()
        valid = self.invitation_token_sha256 and secrets.compare_digest(
            self.invitation_token_sha256,
            hashlib.sha256((token or "").encode()).hexdigest(),
        )
        if (
            not valid
            or not self.invitation_expires_at
            or self.invitation_expires_at < fields.Datetime.now()
            or self.state != "pending_pocket"
        ):
            msg = "This enrolment invitation is invalid or expired."
            raise AccessError(msg)
        return True

    def _bind_pocket_identity(self, *, issuer, claims):
        self.ensure_one()
        if self.state != "pending_pocket":
            msg = "This Pocket ID enrolment is no longer available."
            raise ValidationError(msg)
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            msg = "Pocket ID did not return a stable identity subject."
            raise ValidationError(msg)
        fingerprint = hashlib.sha256(f"{issuer}\0{subject}".encode()).hexdigest()[:16]
        display_name = claims.get("name") or claims.get("preferred_username") or subject
        self.with_context(usl_sign_enrollment_transition=INTERNAL_OPERATION).write(
            {
                "state": "pending_review",
                "pocket_issuer": issuer,
                "pocket_subject": subject,
                "pocket_subject_fingerprint": fingerprint,
                "pocket_email": claims.get("email"),
                "pocket_display_name": display_name,
                "pocket_linked_at": fields.Datetime.now(),
                "pocket_last_authorized_at": fields.Datetime.now(),
                "pocket_authentication_method": "phr",
                "invitation_token_sha256": False,
                "invitation_expires_at": False,
                "status_changed_at": fields.Datetime.now(),
                "status_reason": "Pocket ID identity connected; reviewer confirmation required",
            },
        )
        self._schedule_identity_review()
        return fingerprint

    def action_revoke(self, reason=None):
        for enrollment in self:
            if enrollment.state == "revoked":
                continue
            if not reason:
                msg = "Record why the enrolment is being revoked."
                raise ValidationError(msg)
            if not self.env.user.has_group("usl_sign.group_sign_identity_reviewer"):
                msg = "Identity reviewer access is required."
                raise AccessError(msg)
            now = fields.Datetime.now()
            enrollment.with_context(usl_sign_enrollment_transition=INTERNAL_OPERATION).write(
                {
                    "state": "revoked",
                    "revoked_at": now,
                    "revocation_reason": reason,
                    "status_changed_at": now,
                    "status_changed_by_id": self.env.user.id,
                    "status_reason": reason,
                    "invitation_token_sha256": False,
                    "invitation_expires_at": False,
                },
            )
            ceremonies = self.env["usl.sign.ceremony"].sudo().search(
                [
                    ("enrollment_id", "=", enrollment.id),
                    ("state", "in", ["challenge", "authorizing", "authorized"]),
                ],
            )
            ceremonies.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
                {
                    "state": "failed",
                    "failure_code": "enrollment_revoked",
                    "data_to_sign": False,
                    "dss_signing_context": False,
                },
            )
            enrollment.flush_recordset(["state"])
        return True

    def action_open_revoke(self):
        self.ensure_one()
        if self.state == "revoked":
            msg = "This enrolment is already revoked."
            raise ValidationError(msg)
        return {
            "type": "ir.actions.act_window",
            "res_model": "usl.sign.enrollment.revoke.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_enrollment_id": self.id},
        }

    def write(self, values):
        protected = {
            "state",
            "reviewer_id",
            "reviewed_at",
            "invitation_token_sha256",
            "invitation_expires_at",
            "invitation_sent_at",
            "invitation_mail_id",
            "pocket_issuer",
            "pocket_subject",
            "pocket_subject_fingerprint",
            "pocket_email",
            "pocket_display_name",
            "pocket_linked_at",
            "pocket_last_authorized_at",
            "pocket_authentication_method",
            "revoked_at",
            "status_changed_at",
            "status_changed_by_id",
            "status_reason",
            "revocation_reason",
        }
        if protected.intersection(values) and self.env.context.get(
            "usl_sign_enrollment_transition",
        ) is not INTERNAL_OPERATION:
            msg = "Use a controlled identity-enrolment action."
            raise AccessError(msg)
        if self.filtered(lambda enrollment: enrollment.state == "revoked") and values:
            msg = "A revoked identity enrolment is immutable."
            raise ValidationError(msg)
        return super().write(values)

    def unlink(self):
        msg = "Identity enrolments cannot be deleted; revoke them instead."
        raise AccessError(msg)


class SignEnrollmentRevokeWizard(models.TransientModel):
    _name = "usl.sign.enrollment.revoke.wizard"
    _description = "Revoke a Strong Signer Enrolment"

    enrollment_id = fields.Many2one("usl.sign.enrollment", required=True, readonly=True)
    reason = fields.Text(required=True)

    def action_apply(self):
        self.ensure_one()
        self.enrollment_id.action_revoke(self.reason)
        return {"type": "ir.actions.act_window_close"}


class SignEnrollmentInvitation(models.TransientModel):
    _name = "usl.sign.enrollment.invitation"
    _description = "Copy Strong Signer Setup Link"

    enrollment_id = fields.Many2one(
        "usl.sign.enrollment",
        required=True,
        readonly=True,
    )
    invitation_url = fields.Char(required=True, readonly=True)


class SignCeremony(models.Model):
    _name = "usl.sign.ceremony"
    _description = "One-time Strong Signature Ceremony"
    _order = "create_date desc, id desc"

    request_id = fields.Many2one(
        "sign.oca.request", required=True, index=True, ondelete="restrict",
    )
    signer_id = fields.Many2one(
        "sign.oca.request.signer", required=True, index=True, ondelete="restrict",
    )
    enrollment_id = fields.Many2one(
        "usl.sign.enrollment", required=True, ondelete="restrict",
    )
    company_id = fields.Many2one(related="request_id.company_id", store=True, index=True)
    state = fields.Selection(
        [
            ("challenge", "Authorization challenge"),
            ("authorizing", "Pocket ID authorization"),
            ("authorized", "Authorized"),
            ("completed", "Completed"),
            ("expired", "Expired"),
            ("failed", "Failed"),
            ("revoked", "Cancelled"),
        ],
        default="challenge",
        required=True,
        readonly=True,
    )
    challenge = fields.Binary(required=True, readonly=True)
    challenge_sha256 = fields.Char(required=True, readonly=True, index=True)
    document_sha256 = fields.Char(required=True, readonly=True)
    consent_sha256 = fields.Char(required=True, readonly=True)
    csr_sha256 = fields.Char(required=True, readonly=True)
    public_key_sha256 = fields.Char(required=True, readonly=True)
    csr_pem = fields.Text(required=True, readonly=True)
    binding_payload = fields.Json(required=True, readonly=True)
    expires_at = fields.Datetime(required=True, readonly=True)
    authorized_at = fields.Datetime(readonly=True)
    completed_at = fields.Datetime(readonly=True)
    certificate_pem = fields.Text(readonly=True)
    certificate_chain = fields.Json(readonly=True)
    certificate_serial = fields.Char(readonly=True)
    certificate_issued_at = fields.Datetime(readonly=True)
    certificate_not_after = fields.Datetime(readonly=True)
    issuance_receipt = fields.Json(readonly=True)
    pades_level = fields.Char(readonly=True)
    data_to_sign_sha256 = fields.Char(readonly=True)
    data_to_sign = fields.Text(
        readonly=True,
        copy=False,
        groups="usl_sign.group_sign_evidence_reviewer",
    )
    dss_signing_context = fields.Char(readonly=True)
    failure_code = fields.Char(readonly=True)
    oidc_state_sha256 = fields.Char(readonly=True, copy=False, index=True)
    oidc_nonce = fields.Char(readonly=True, copy=False)
    oidc_issuer = fields.Char(readonly=True, copy=False)
    oidc_subject = fields.Char(readonly=True, copy=False)
    oidc_auth_time = fields.Datetime(readonly=True, copy=False)
    oidc_claims_summary = fields.Json(readonly=True, copy=False)
    oidc_discovery_snapshot = fields.Json(readonly=True, copy=False)
    oidc_jwks_snapshot = fields.Json(readonly=True, copy=False)
    oidc_validation_result = fields.Json(readonly=True, copy=False)
    oidc_id_token = fields.Text(
        readonly=True,
        copy=False,
        groups="usl_sign.group_sign_evidence_reviewer",
    )

    def init(self):
        """Keep one live document-key ceremony per signer at the database boundary."""
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS usl_sign_ceremony_active_signer_unique
                ON usl_sign_ceremony (signer_id)
             WHERE state IN ('challenge', 'authorizing', 'authorized')
            """,
        )

    def write(self, values):
        if self.env.context.get("usl_sign_ceremony_transition") is not INTERNAL_OPERATION:
            msg = "Ceremonies can only change through controlled transitions."
            raise AccessError(msg)
        return super().write(values)

    def unlink(self):
        msg = "Strong-signature ceremonies are evidence and cannot be deleted."
        raise AccessError(msg)
