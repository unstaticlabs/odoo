import hashlib
import hmac
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from markupsafe import Markup, escape

from odoo import _, api, fields, models, tools
from odoo.exceptions import AccessError, ValidationError


class UslMailSenderAlias(models.Model):
    _name = "usl.mail.sender.alias"
    _description = "Verified Personal Email Address"
    _order = "partner_id, email_normalized, id"

    partner_id = fields.Many2one(
        "res.partner",
        required=True,
        index=True,
        ondelete="cascade",
    )
    email = fields.Char(required=True)
    email_normalized = fields.Char(
        compute="_compute_email_normalized",
        store=True,
        index=True,
        readonly=True,
    )
    active = fields.Boolean(default=True)
    state = fields.Selection(
        [("pending", "Pending verification"), ("verified", "Verified")],
        default="pending",
        required=True,
        readonly=True,
        index=True,
    )
    verified_at = fields.Datetime(readonly=True, copy=False)
    verification_sent_at = fields.Datetime(readonly=True, copy=False)
    verification_token_digest = fields.Char(
        readonly=True,
        copy=False,
        groups="base.group_system",
    )
    verification_expires_at = fields.Datetime(
        readonly=True,
        copy=False,
        groups="base.group_system",
    )

    _email_unique = models.Constraint(
        "UNIQUE(email_normalized)",
        "This personal email address is already registered.",
    )

    @api.depends("email")
    def _compute_email_normalized(self):
        for alias in self:
            alias.email_normalized = tools.email_normalize(
                alias.email or "",
                strict=False,
            )

    def _actor_can_manage(self, partner):
        return (
            self.env.user.has_group("base.group_system")
            or self.env.user.has_group("hr.group_hr_user")
            or partner == self.env.user.partner_id
        )

    def _check_actor_can_manage(self, partner=None):
        target = partner or self.partner_id
        if not target or not self._actor_can_manage(target):
            raise AccessError(
                _("You can manage only your own personal email addresses."),
            )

    @api.constrains("email", "email_normalized", "partner_id")
    def _check_email_identity(self):
        for alias in self:
            if not alias.email_normalized:
                raise ValidationError(_("Enter a valid email address."))
            if alias.email_normalized == alias.partner_id.email_normalized:
                raise ValidationError(
                    _("This is already the contact's primary email address."),
                )
            recipient_alias = self.env["mail.alias"].sudo().search(
                [("alias_full_name", "=ilike", alias.email_normalized)],
                limit=1,
            )
            if recipient_alias:
                raise ValidationError(
                    _("An Odoo destination alias cannot be a personal sender address."),
                )
            conflicting_partner = self.env["res.partner"].sudo().search(
                [
                    ("id", "!=", alias.partner_id.id),
                    ("email_normalized", "=", alias.email_normalized),
                    "|",
                    ("user_ids", "!=", False),
                    ("employee_ids", "!=", False),
                ],
                limit=1,
            )
            if conflicting_partner:
                raise ValidationError(
                    _("This address belongs to another Odoo user or employee."),
                )

    @api.model_create_multi
    def create(self, values_list):
        protected = {
            "state",
            "verified_at",
            "verification_sent_at",
            "verification_token_digest",
            "verification_expires_at",
        }
        for values in values_list:
            if values.get("email"):
                values["email"] = tools.email_normalize(
                    values["email"],
                    strict=False,
                ) or values["email"].strip()
            partner = self.env["res.partner"].browse(values.get("partner_id"))
            self._check_actor_can_manage(partner)
            for field_name in protected:
                values.pop(field_name, None)
        aliases = super().create(values_list)
        if not self.env.context.get("usl_sender_alias_skip_automatic_verification"):
            aliases._send_verification_for_pending_addresses()
        return aliases

    def write(self, values):
        values = dict(values)
        if values.get("email"):
            values["email"] = tools.email_normalize(
                values["email"],
                strict=False,
            ) or values["email"].strip()
        protected = {
            "state",
            "verified_at",
            "verification_sent_at",
            "verification_token_digest",
            "verification_expires_at",
        }
        if protected & values.keys() and not self.env.context.get(
            "usl_sender_alias_internal",
        ):
            raise AccessError(_("Verification state is managed by Odoo."))
        for alias in self:
            partner = self.env["res.partner"].browse(
                values.get("partner_id", alias.partner_id.id),
            )
            alias._check_actor_can_manage(partner)
        aliases_to_reset = self.filtered(
            lambda alias: (
                "email" in values and values["email"] != alias.email
            )
            or (
                "partner_id" in values
                and values["partner_id"] != alias.partner_id.id
            ),
        )
        result = super(
            UslMailSenderAlias,
            self.with_context(usl_sender_alias_internal=True),
        ).write(values)
        if aliases_to_reset:
            aliases_to_reset.sudo().with_context(
                usl_sender_alias_internal=True,
            ).write(
                {
                    "state": "pending",
                    "verified_at": False,
                    "verification_sent_at": False,
                    "verification_token_digest": False,
                    "verification_expires_at": False,
                },
            )
            if not self.env.context.get(
                "usl_sender_alias_skip_automatic_verification",
            ):
                aliases_to_reset._send_verification_for_pending_addresses()
        return result

    def unlink(self):
        for alias in self:
            alias._check_actor_can_manage()
        return super().unlink()

    def _verification_base_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_str("web.base.url")
        parsed = urlsplit(base_url or "")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValidationError(
                _("The public Odoo URL is not configured safely."),
            )
        return base_url.rstrip("/")

    def _send_verification_for_pending_addresses(self):
        for alias in self.filtered(lambda item: item.active and item.state == "pending"):
            alias._issue_verification()

    def _issue_verification(self, *, send=True):
        self.ensure_one()
        self._check_actor_can_manage()
        if self.state == "verified":
            raise ValidationError(_("This email address is already verified."))
        raw_token = secrets.token_urlsafe(32)
        now = fields.Datetime.now()
        self.with_context(usl_sender_alias_internal=True).sudo().write(
            {
                "state": "pending",
                "verified_at": False,
                "verification_sent_at": now,
                "verification_token_digest": hashlib.sha256(
                    raw_token.encode("ascii"),
                ).hexdigest(),
                "verification_expires_at": now + timedelta(hours=24),
            },
        )
        link = (
            f"{self._verification_base_url()}/usl/mail/sender/verify/"
            f"{self.id}/{raw_token}"
        )
        body = Markup(
            "<p>Hello %(name)s,</p>"
            "<p>Confirm that <strong>%(email)s</strong> belongs to you. "
            "Odoo will then recognize messages sent from this address.</p>"
            "<p><a href=\"%(link)s\" style=\"background:#714b67;color:#fff;"
            "padding:10px 16px;text-decoration:none;border-radius:4px;"
            "display:inline-block;\">Verify email address</a></p>"
            "<p>This personal link expires after 24 hours.</p>"
        ) % {
            "name": escape(self.partner_id.name),
            "email": escape(self.email_normalized),
            "link": escape(link),
        }
        mail = self.env["mail.mail"].sudo().create(
            {
                "subject": _("Verify your personal email address"),
                "body_html": body,
                "email_to": self.email_normalized,
                "auto_delete": True,
            },
        )
        if send:
            mail.send()
        return raw_token, link, mail

    def action_send_verification(self):
        self._issue_verification()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Verification email sent"),
                "message": _(
                    "Open the message sent to %(email)s within 24 hours.",
                    email=self.email_normalized,
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def _verify_token(self, raw_token):
        self.ensure_one()
        alias = self.sudo()
        expected = alias.verification_token_digest or ""
        supplied = hashlib.sha256(str(raw_token or "").encode("utf-8")).hexdigest()
        if (
            not expected
            or not hmac.compare_digest(expected, supplied)
            or not alias.verification_expires_at
            or alias.verification_expires_at < fields.Datetime.now()
        ):
            return False
        alias.with_context(usl_sender_alias_internal=True).write(
            {
                "state": "verified",
                "verified_at": fields.Datetime.now(),
                "verification_token_digest": False,
                "verification_expires_at": False,
            },
        )
        return True


class ResPartner(models.Model):
    _inherit = "res.partner"

    usl_sender_alias_ids = fields.One2many(
        "usl.mail.sender.alias",
        "partner_id",
        string="Personal Sender Addresses",
    )

    @api.model
    def _usl_verified_sender_partner(self, email):
        normalized = tools.email_normalize(email or "", strict=False)
        if not normalized:
            return self.browse()
        alias = self.env["usl.mail.sender.alias"].sudo().search(
            [
                ("email_normalized", "=", normalized),
                ("state", "=", "verified"),
                ("active", "=", True),
            ],
            limit=1,
        )
        return self.browse(alias.partner_id.id)

    @api.model
    def find_or_create(self, email, assert_valid_email=False):
        partner = self._usl_verified_sender_partner(email)
        if partner:
            return partner
        return super().find_or_create(email, assert_valid_email=assert_valid_email)

    @api.model
    def _find_or_create_from_emails(
        self,
        emails,
        ban_emails=None,
        filter_found=None,
        additional_values=None,
        no_create=False,
        sort_key=None,
        sort_reverse=True,
    ):
        banned = {
            tools.email_normalize(email, strict=False) or email
            for email in (ban_emails or [])
        }
        results = [self.browse() for _email in emails]
        remaining_positions = []
        remaining_emails = []
        for position, email in enumerate(emails):
            normalized = tools.email_normalize(email, strict=False) or email
            partner = self._usl_verified_sender_partner(email)
            if (
                partner
                and normalized not in banned
                and (not filter_found or filter_found(partner))
            ):
                results[position] = partner
            else:
                remaining_positions.append(position)
                remaining_emails.append(email)
        if remaining_emails:
            remaining_results = super()._find_or_create_from_emails(
                remaining_emails,
                ban_emails=ban_emails,
                filter_found=filter_found,
                additional_values=additional_values,
                no_create=no_create,
                sort_key=sort_key,
                sort_reverse=sort_reverse,
            )
            for position, partner in zip(remaining_positions, remaining_results):
                results[position] = partner
        return results


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    @api.model
    def _mail_find_partner_from_emails(
        self,
        emails,
        records=None,
        force_create=False,
        extra_domain=False,
    ):
        results = super()._mail_find_partner_from_emails(
            emails,
            records=records,
            force_create=force_create,
            extra_domain=extra_domain,
        )
        for position, email in enumerate(emails):
            partner = self.env["res.partner"]._usl_verified_sender_partner(email)
            if partner and (not extra_domain or partner.filtered_domain(extra_domain)):
                results[position] = partner
        return results


class ResUsers(models.Model):
    _inherit = "res.users"

    usl_sender_alias_ids = fields.One2many(
        related="partner_id.usl_sender_alias_ids",
        readonly=False,
        user_writeable=True,
    )


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    usl_sender_alias_ids = fields.One2many(
        related="work_contact_id.usl_sender_alias_ids",
        readonly=False,
    )


class HrExpense(models.Model):
    _inherit = "hr.expense"

    @api.model
    def _get_employee_from_email(self, email_address):
        partner = self.env["res.partner"]._usl_verified_sender_partner(email_address)
        if not partner:
            return super()._get_employee_from_email(email_address)
        employees = self.env["hr.employee"].sudo().search(
            [
                "|",
                ("work_contact_id", "=", partner.id),
                ("user_id.partner_id", "=", partner.id),
            ],
        )
        if len(employees) > 1:
            user = partner.main_user_id
            preferred_company = user.company_id if user else self.env.company
            preferred = employees.filtered(
                lambda employee: employee.company_id == preferred_company,
            )
            if len(preferred) == 1:
                employees = preferred
        return self.env["hr.employee"].browse(employees[:1].id)


class Base(models.AbstractModel):
    _inherit = "base"

    def _alias_get_error(self, message, message_dict, alias):
        if alias.alias_contact == "employees" and message_dict.get("author_id"):
            partner = self.env["res.partner"].browse(message_dict["author_id"])
            employee = self.env["hr.employee"].sudo().search(
                [
                    "|",
                    ("work_contact_id", "=", partner.id),
                    ("user_id.partner_id", "=", partner.id),
                ],
                limit=1,
            )
            if employee:
                return False
        return super()._alias_get_error(message, message_dict, alias)
