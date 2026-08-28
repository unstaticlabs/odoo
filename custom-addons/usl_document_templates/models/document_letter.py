from lxml import html

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class UslDocumentLetter(models.Model):
    _name = "usl.document.letter"
    _description = "Official Letter"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"
    _rec_name = "reference"
    _check_company_auto = True

    reference = fields.Char(required=True, readonly=True, copy=False, index=True)
    version = fields.Integer(default=1, required=True, readonly=True, copy=False)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        tracking=True,
    )
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    recipient_id = fields.Many2one(
        "res.partner",
        string="Recipient",
        required=True,
        tracking=True,
    )
    subject = fields.Char(required=True, tracking=True)
    delivery_method = fields.Selection(
        selection=[
            ("email", "Email"),
            ("postal", "Postal mail"),
            ("hand", "Hand delivery"),
            ("other", "Other"),
        ],
        required=True,
        default="email",
        tracking=True,
    )
    signatory_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
    )
    signatory_title = fields.Char(required=True, tracking=True)
    body = fields.Html(required=True, tracking=True)
    attachment_ids = fields.Many2many(
        "ir.attachment",
        "usl_document_letter_attachment_rel",
        "letter_id",
        "attachment_id",
        string="Attachment names printed",
        help=(
            "Only the filenames are listed in the letter PDF; the files are "
            "neither merged into nor embedded in it."
        ),
        check_company=True,
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("finalized", "Finalized"),
            ("sent", "Sent"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        default="draft",
        readonly=True,
        copy=False,
        tracking=True,
        index=True,
    )
    finalized_at = fields.Datetime(readonly=True, copy=False)
    sent_at = fields.Datetime(readonly=True, copy=False)
    finalized_attachment_id = fields.Many2one(
        "ir.attachment",
        string="Official PDF",
        readonly=True,
        copy=False,
        ondelete="restrict",
    )
    finalized_snapshot = fields.Json(readonly=True, copy=False)
    supersedes_id = fields.Many2one(
        "usl.document.letter",
        string="Corrects",
        readonly=True,
        copy=False,
        check_company=True,
        ondelete="restrict",
    )
    superseded_by_ids = fields.One2many(
        "usl.document.letter",
        "supersedes_id",
        string="Corrections",
        readonly=True,
    )

    _reference_company_unique = models.Constraint(
        "UNIQUE(company_id, reference)",
        "The letter reference must be unique within a company.",
    )

    _SYSTEM_FIELDS = {
        "reference",
        "version",
        "state",
        "finalized_at",
        "sent_at",
        "finalized_attachment_id",
        "finalized_snapshot",
        "supersedes_id",
    }

    @api.model
    def _sequence_for_company(self, company):
        if company not in self.env.companies:
            raise UserError(_("You cannot issue correspondence for this company."))
        sequence = self.env["ir.sequence"].sudo().search(
            [
                ("code", "=", "usl.document.letter"),
                ("company_id", "=", company.id),
            ],
            limit=1,
        )
        if not sequence:
            sequence = self.env["ir.sequence"].sudo().create(
                {
                    "name": _("Official letters - %s", company.name),
                    "code": "usl.document.letter",
                    "company_id": company.id,
                    "prefix": "LET/%(year)s/",
                    "padding": 5,
                }
            )
        return sequence

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            if not self.env.context.get("usl_document_letter_system_transition"):
                forbidden = self._SYSTEM_FIELDS.intersection(values)
                if forbidden:
                    raise UserError(
                        _(
                            "Official-letter lifecycle fields are managed by the "
                            "correspondence workflow."
                        )
                    )
            company = self.env["res.company"].browse(
                values.get("company_id") or self.env.company.id
            )
            values.setdefault(
                "reference",
                self._sequence_for_company(company).with_company(company)._next(),
            )
        return super().create(values_list)

    def _body_blocks(self):
        self.ensure_one()
        root = html.fragment_fromstring(self.body or "", create_parent="div")
        blocks = []

        def add_node(node):
            tag = node.tag.lower() if isinstance(node.tag, str) else ""
            text = " ".join(node.text_content().split())
            if tag in {"div", "section"}:
                for child in node:
                    add_node(child)
            elif tag == "p":
                if text:
                    blocks.append({"type": "paragraph", "text": text})
            elif tag in {"h2", "h3"}:
                if text:
                    blocks.append(
                        {"type": "heading", "level": int(tag[1]), "text": text}
                    )
            elif tag in {"ul", "ol"}:
                items = [
                    " ".join(child.text_content().split())
                    for child in node
                    if isinstance(child.tag, str) and child.tag.lower() == "li"
                ]
                if items:
                    blocks.append(
                        {
                            "type": "bullet_list" if tag == "ul" else "numbered_list",
                            "items": items,
                        }
                    )
            elif tag == "table":
                parsed_rows = []
                header = []
                for row in node.xpath(".//tr"):
                    cells = [
                        " ".join(cell.text_content().split())
                        for cell in row.xpath("./th|./td")
                    ]
                    if not cells:
                        continue
                    if not header:
                        header = cells
                    else:
                        parsed_rows.append(cells)
                if header:
                    blocks.append(
                        {
                            "type": "table",
                            "headers": header,
                            "rows": parsed_rows,
                        }
                    )
            elif text:
                raise ValidationError(
                    self.env._(
                        "The letter body contains an unsupported %(tag)s block. "
                        "Use paragraphs, headings, lists, or tables.",
                        tag=tag or self.env._("unknown"),
                    )
                )

        for child in root:
            add_node(child)
        if not blocks:
            raise ValidationError(_("The letter body must contain printable text."))
        return blocks

    def _current_snapshot(self):
        self.ensure_one()
        recipient = self.recipient_id
        self.attachment_ids.check_access("read")
        if not recipient.name or not recipient.street or not recipient.zip or not recipient.city:
            raise ValidationError(
                _(
                    "The recipient needs a name and complete postal address before "
                    "an official letter can be finalized."
                )
            )
        address_lines = [
            line
            for line in (
                recipient.street,
                recipient.street2,
                " ".join(value for value in (recipient.zip, recipient.city) if value),
                recipient.state_id.name,
                recipient.country_id.name,
            )
            if line
        ]
        locale = "fr_FR" if (recipient.lang or "").startswith("fr") else "en_US"
        return {
            "locale": locale,
            "sender": {
                "company_id": self.company_id.id,
                "company_name": self.company_id.name,
                "legal_identity_lines": self.company_id._usl_document_legal_lines(locale),
            },
            "recipient": {
                "partner_id": recipient.id,
                "name": recipient.name,
                "address_lines": address_lines,
            },
            "reference": self.reference,
            "version": self.version,
            "date": fields.Date.to_string(self.date),
            "subject": self.subject,
            "delivery_method": self.delivery_method,
            "body": self._body_blocks(),
            "signatory_name": self.signatory_id.name,
            "signatory_title": self.signatory_title,
            "attachments": self.attachment_ids.mapped("name"),
        }

    def _document_payload_from_snapshot(self, snapshot):
        document_env = self.with_context(lang=snapshot["locale"]).env
        return {
            "reference": snapshot["reference"],
            "date": snapshot["date"],
            "recipient": snapshot["recipient"],
            "subject": snapshot["subject"],
            "body": snapshot["body"],
            "closing": (
                document_env._("Please accept our sincere regards.")
                if snapshot["locale"] == "en_US"
                else document_env._(
                    "Nous vous prions d’agréer l’expression de nos salutations distinguées."
                )
            ),
            "signatory_name": snapshot["signatory_name"],
            "signatory_title": snapshot["signatory_title"],
            "attachments": snapshot["attachments"],
        }

    def _usl_document_render_payload(self, _report, template, _data, _locale):
        self.ensure_one()
        if template.key != "official_letter.v1":
            raise UserError(_("This letter can only use official_letter.v1."))
        if not self.finalized_snapshot:
            raise UserError(_("Finalize the letter before printing its official PDF."))
        return self._document_payload_from_snapshot(self.finalized_snapshot)

    def _usl_document_report_attachment_name(self):
        self.ensure_one()
        return f"{self.reference.replace('/', '-')}-v{self.version}.pdf"

    def action_finalize(self):
        template = self.env.ref("usl_document_templates.template_official_letter_v1")
        for letter in self:
            if letter.state != "draft":
                raise UserError(_("Only a draft letter can be finalized."))
            letter.check_access("write")
            if not letter.company_id.usl_document_renderer_enabled:
                letter.company_id._usl_document_raise_configuration_error(
                    _("The governed document renderer is disabled for this company.")
                )
            snapshot = letter._current_snapshot()
            locale = snapshot["locale"]
            company_payload, assets = letter.company_id._usl_document_renderer_company_payload(
                locale
            )
            try:
                rendered = self.env["usl.document.renderer"].render(
                    template,
                    company_payload,
                    letter._document_payload_from_snapshot(snapshot),
                    locale,
                    assets=assets,
                )
            except UserError as error:
                letter.company_id._usl_document_raise_configuration_error(str(error))
            attachment = self.env["ir.attachment"].create(
                {
                    "name": letter._usl_document_report_attachment_name(),
                    "raw": rendered["pdf"],
                    "mimetype": "application/pdf",
                    "res_model": letter._name,
                    "res_id": letter.id,
                    "description": _("Immutable finalized official letter"),
                    "usl_document_template_id": template.id,
                    "usl_document_template_revision": rendered["template_revision"],
                    "usl_document_payload_sha256": rendered["payload_sha256"],
                    "usl_document_renderer_version": rendered["renderer_version"],
                    "usl_document_company_id": letter.company_id.id,
                    "usl_document_rendered_at": fields.Datetime.now(),
                }
            )
            letter.with_context(usl_document_letter_system_transition=True).write(
                {
                    "state": "finalized",
                    "finalized_at": fields.Datetime.now(),
                    "finalized_snapshot": snapshot,
                    "finalized_attachment_id": attachment.id,
                }
            )
            letter.message_post(
                body=_("Official version %(version)s finalized.", version=letter.version),
                attachment_ids=attachment.ids,
            )
        return True

    def action_mark_sent(self):
        for letter in self:
            if letter.state != "finalized":
                raise UserError(_("Only a finalized letter can be marked as sent."))
            letter.with_context(usl_document_letter_system_transition=True).write(
                {"state": "sent", "sent_at": fields.Datetime.now()}
            )
        return True

    def action_cancel(self):
        for letter in self:
            if letter.state not in {"draft", "finalized"}:
                raise UserError(_("Only a draft or finalized letter can be cancelled."))
            letter.with_context(usl_document_letter_system_transition=True).write(
                {"state": "cancelled"}
            )
        return True

    def action_create_correction(self):
        self.ensure_one()
        if self.state not in {"finalized", "sent"}:
            raise UserError(_("Only a finalized or sent letter can be corrected."))
        corrected = self.with_context(
            usl_document_letter_system_transition=True
        ).copy(
            {
                "version": self.version + 1,
                "supersedes_id": self.id,
                "state": "draft",
                "finalized_at": False,
                "sent_at": False,
                "finalized_attachment_id": False,
                "finalized_snapshot": False,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": corrected.id,
            "view_mode": "form",
        }

    def action_download_pdf(self):
        self.ensure_one()
        if not self.finalized_attachment_id:
            raise UserError(_("This letter has no finalized PDF."))
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self.finalized_attachment_id.id}?download=true",
            "target": "self",
        }

    def write(self, values):
        if (
            self._SYSTEM_FIELDS.intersection(values)
            and not self.env.context.get("usl_document_letter_system_transition")
        ):
            raise UserError(
                _(
                    "Official-letter lifecycle fields are managed by the "
                    "correspondence workflow."
                )
            )
        protected = {
            "company_id",
            "date",
            "recipient_id",
            "subject",
            "delivery_method",
            "signatory_id",
            "signatory_title",
            "body",
            "attachment_ids",
        }
        for letter in self:
            if letter.state != "draft" and protected.intersection(values):
                raise UserError(
                    _("Finalized official content is immutable. Create a correction instead.")
                )
            if "state" in values:
                allowed = {
                    "draft": {"draft", "finalized", "cancelled"},
                    "finalized": {"finalized", "sent", "cancelled"},
                    "sent": {"sent"},
                    "cancelled": {"cancelled"},
                }
                if values["state"] not in allowed[letter.state]:
                    raise UserError(_("This letter state transition is not allowed."))
        return super().write(values)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_drafts(self):
        if any(letter.state != "draft" for letter in self):
            raise UserError(_("Only draft letters can be deleted."))
