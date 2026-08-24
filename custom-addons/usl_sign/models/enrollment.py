import hashlib
import secrets
from datetime import timedelta

from odoo import fields, models
from odoo.exceptions import AccessError, ValidationError

from .constants import INTERNAL_OPERATION


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
            ("pending_pocket", "Pocket ID connection required"),
            ("pending_review", "Identity review required"),
            ("active", "Active"),
            ("revoked", "Revoked"),
        ],
        required=True,
        default="pending_pocket",
    )
    relationship_basis = fields.Selection(
        [
            ("pocket_id", "Existing Pocket ID account"),
            ("employee", "Employee relationship"),
            ("contractor", "Contractor relationship"),
            ("recurring_partner", "Known recurring partner"),
        ],
        required=True,
    )
    relationship_reference = fields.Char(required=True)
    reviewer_id = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    reviewed_at = fields.Datetime(readonly=True)
    policy_version = fields.Char(required=True, default="1")
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
        if not self.env.user.has_group("usl_sign.group_sign_identity_reviewer"):
            msg = "Identity reviewer access is required."
            raise AccessError(msg)
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
        return True

    def action_create_invitation(self):
        self.ensure_one()
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
        return {
            "type": "ir.actions.act_url",
            "target": "new",
            "url": f"{base}/sign/enroll/{self.id}/{token}",
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
