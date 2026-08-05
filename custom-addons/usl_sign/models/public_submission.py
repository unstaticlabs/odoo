import hashlib
from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import api, fields, models


class SignPublicSubmission(models.Model):
    _name = "usl.sign.public.submission"
    _description = "Reusable Signature Link Submission"
    _order = "create_date desc, id desc"

    template_id = fields.Many2one(
        "sign.oca.template", required=True, ondelete="restrict", index=True
    )
    company_id = fields.Many2one(
        related="template_id.company_id", store=True, index=True
    )
    request_id = fields.Many2one(
        "sign.oca.request", ondelete="restrict", readonly=True, index=True
    )
    partner_id = fields.Many2one(
        "res.partner", required=True, ondelete="restrict", readonly=True
    )
    token_sha256 = fields.Char(required=True, readonly=True, index=True)
    source_hash = fields.Char(required=True, readonly=True, index=True)
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("sent", "Sent"),
            ("action_required", "Action required"),
        ],
        required=True,
        default="pending",
        readonly=True,
        index=True,
    )
    attempt_count = fields.Integer(default=0, readonly=True)
    last_attempt_at = fields.Datetime(readonly=True)

    _token_unique = models.Constraint(
        "UNIQUE(token_sha256)", "This public-link submission was already received."
    )

    @api.model
    def _token_digest(self, token):
        return hashlib.sha256(token.encode()).hexdigest()

    @api.model
    def _create_submission(self, template, partner_vals, token, source_hash):
        """Create one durable request per browser submission token.

        Provider work is deliberately deferred to cron so an accepted remote
        transaction can never be orphaned by an HTTP transaction rollback.
        """
        digest = self._token_digest(token)
        existing = self.sudo().search([("token_sha256", "=", digest)], limit=1)
        if existing:
            return existing
        try:
            with self.env.cr.savepoint():
                partner = self.env["res.partner"].sudo().create(
                    {
                        **partner_vals,
                        "company_id": template.company_id.id,
                        "lang": self.env.context.get("lang") or "fr_FR",
                    }
                )
                request_vals = template.sudo()._prepare_public_request_vals(partner)
                sign_request = self.env["sign.oca.request"].sudo().create(request_vals)
                submission = self.sudo().create(
                    {
                        "template_id": template.id,
                        "partner_id": partner.id,
                        "request_id": sign_request.id,
                        "token_sha256": digest,
                        "source_hash": source_hash,
                    }
                )
        except IntegrityError:
            submission = self.sudo().search([("token_sha256", "=", digest)], limit=1)
            if not submission:
                raise
        cron = self.env.ref("usl_sign.ir_cron_sign_public_submissions", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return submission

    def _process_pending(self):
        submissions = self.sudo().search(
            [
                ("state", "in", ["pending", "action_required"]),
                ("attempt_count", "<", 5),
                "|",
                ("last_attempt_at", "=", False),
                ("last_attempt_at", "<", fields.Datetime.now() - timedelta(minutes=5)),
            ],
            limit=20,
        )
        for submission in submissions:
            sign_request = submission.request_id
            if sign_request.state in {"sent", "viewed", "partial", "completed"}:
                submission.write({"state": "sent"})
                continue
            if sign_request.state in {"declined", "expired", "cancelled"}:
                submission.write({"state": "action_required"})
                continue
            submission.write(
                {
                    "attempt_count": submission.attempt_count + 1,
                    "last_attempt_at": fields.Datetime.now(),
                }
            )
            try:
                with self.env.cr.savepoint():
                    sign_request.action_send()
            except Exception:
                # The request carries the actionable provider explanation. Do
                # not expose it to the anonymous submitter or duplicate work.
                submission.write({"state": "action_required"})
                continue
            submission.write(
                {
                    "state": "sent"
                    if sign_request.state in {"sent", "viewed", "partial"}
                    else "action_required"
                }
            )

    @api.model
    def _cron_process_public_submissions(self):
        self._process_pending()
