import base64
import hashlib
import re

from odoo import api, fields, models, _
from odoo.exceptions import RedirectWarning, ValidationError
from odoo.tools.mimetypes import guess_mimetype


class ResCompany(models.Model):
    _inherit = "res.company"

    usl_document_renderer_enabled = fields.Boolean(
        string="USL document renderer",
        default=True,
        help="Route governed official documents through the isolated LaTeX renderer.",
    )
    usl_document_legal_form = fields.Char(
        string="Legal form",
        help="Legal form printed on official documents, for example SAS or SARL.",
    )
    usl_document_share_capital = fields.Monetary(
        string="Share capital",
        currency_field="currency_id",
        help="Paid or stated share capital printed on official documents.",
    )
    usl_document_rcs_city = fields.Char(
        string="RCS city",
        help="Registry city printed as part of the French RCS mention.",
    )
    usl_document_identity_ready = fields.Boolean(
        string="Legal identity ready",
        compute="_compute_usl_document_identity_readiness",
    )
    usl_document_identity_message = fields.Char(
        string="Legal identity status",
        compute="_compute_usl_document_identity_readiness",
    )
    usl_document_renderer_status = fields.Selection(
        selection=[
            ("unknown", "Not checked"),
            ("healthy", "Healthy"),
            ("error", "Unavailable"),
        ],
        default="unknown",
        readonly=True,
        copy=False,
    )
    usl_document_renderer_checked_at = fields.Datetime(
        string="Renderer checked at",
        readonly=True,
        copy=False,
    )
    usl_document_renderer_revision = fields.Char(
        string="Renderer revision",
        readonly=True,
        copy=False,
    )
    usl_document_renderer_version = fields.Char(
        string="Renderer version",
        readonly=True,
        copy=False,
    )
    usl_document_renderer_message = fields.Char(
        string="Renderer message",
        readonly=True,
        copy=False,
    )

    @api.constrains(
        "usl_document_legal_form",
        "usl_document_share_capital",
        "usl_document_rcs_city",
    )
    def _check_usl_document_legal_identity_format(self):
        for company in self:
            if company.usl_document_share_capital < 0:
                raise ValidationError(_("Share capital cannot be negative."))
            if company.usl_document_legal_form and not re.fullmatch(
                r"[0-9A-Za-zÀ-ÖØ-öø-ÿ .&'’()/-]{2,60}",
                company.usl_document_legal_form,
            ):
                raise ValidationError(_("The legal form contains unsupported characters."))
            if company.usl_document_rcs_city and not re.fullmatch(
                r"[A-Za-zÀ-ÖØ-öø-ÿ .'\-]{2,80}",
                company.usl_document_rcs_city,
            ):
                raise ValidationError(_("The RCS city must contain a valid city name."))

    def _usl_document_identity_errors(self):
        self.ensure_one()
        errors = []
        for field_name, label in (
            ("name", _("company name")),
            ("street", _("registered address")),
            ("zip", _("postal code")),
            ("city", _("city")),
            ("country_id", _("country")),
        ):
            if not self[field_name]:
                errors.append(label)
        if (
            (not self.logo or self.uses_default_logo)
            and not self._usl_document_builtin_logo()
        ):
            errors.append(_("company logo"))
        if self.country_id.code == "FR":
            for field_name, label in (
                ("company_registry", _("SIREN / registry number")),
                ("vat", _("VAT number")),
                ("usl_document_legal_form", _("legal form")),
                ("usl_document_share_capital", _("share capital")),
                ("usl_document_rcs_city", _("RCS city")),
            ):
                if not self[field_name]:
                    errors.append(label)
            if "ape" in self._fields and not self.ape:
                errors.append(_("APE code"))
        return errors

    def _usl_document_builtin_logo(self):
        self.ensure_one()
        registry_digits = re.sub(r"\D", "", self.company_registry or "")
        return "unstatic" if registry_digits.startswith("983982950") else None

    @staticmethod
    def _usl_group_french_identifier(value, groups):
        digits = re.sub(r"\D", "", value or "")
        if sum(groups) != len(digits):
            return value or ""
        result = []
        offset = 0
        for size in groups:
            result.append(digits[offset:offset + size])
            offset += size
        return " ".join(result)

    @staticmethod
    def _usl_format_french_vat(value):
        compact = re.sub(r"\s", "", value or "").upper()
        match = re.fullmatch(r"FR(\d{2})(\d{3})(\d{3})(\d{3})", compact)
        return " ".join((f"FR{match[1]}", match[2], match[3], match[4])) if match else value

    @api.depends(
        "name",
        "street",
        "zip",
        "city",
        "country_id",
        "logo",
        "company_registry",
        "vat",
        "usl_document_legal_form",
        "usl_document_share_capital",
        "usl_document_rcs_city",
    )
    def _compute_usl_document_identity_readiness(self):
        for company in self:
            errors = company._usl_document_identity_errors()
            company.usl_document_identity_ready = not errors
            company.usl_document_identity_message = (
                _("Ready for official documents")
                if not errors
                else _("Complete: %s", ", ".join(errors))
            )

    def _usl_document_legal_lines(self, locale):
        self.ensure_one()
        document_env = self.with_context(lang=locale).env
        address = " · ".join(
            part
            for part in (
                self.street,
                self.street2,
                " ".join(part for part in (self.zip, self.city) if part),
                self.country_id.name,
            )
            if part
        )
        if locale == "fr_FR":
            localized_company = self.with_context(lang=locale)
            capital = localized_company.currency_id.format(
                localized_company.usl_document_share_capital
            )
            if localized_company.usl_document_share_capital == int(
                localized_company.usl_document_share_capital
            ):
                capital = (
                    f"{int(localized_company.usl_document_share_capital):,}"
                    .replace(",", " ")
                    + f" {localized_company.currency_id.symbol}"
                )
            registry_digits = re.sub(r"\D", "", self.company_registry or "")
            siren = registry_digits[:9]
            registry_line = document_env._(
                "RCS %(city)s %(siren)s",
                city=self.usl_document_rcs_city,
                siren=self._usl_group_french_identifier(siren, (3, 3, 3)),
            )
            if len(registry_digits) == 14:
                registry_line = document_env._(
                    "%(identity)s · SIRET %(siret)s",
                    identity=registry_line,
                    siret=self._usl_group_french_identifier(
                        registry_digits,
                        (3, 3, 3, 5),
                    ),
                )
            if "ape" in self._fields and self.ape:
                registry_line = document_env._(
                    "%(identity)s · APE %(ape)s",
                    identity=registry_line,
                    ape=self.ape,
                )
            return [
                document_env._(
                    "%(name)s — %(form)s au capital de %(capital)s",
                    name=self.name.upper(),
                    form=self.usl_document_legal_form,
                    capital=capital,
                ),
                registry_line,
                document_env._(
                    "TVA intracommunautaire %(vat)s",
                    vat=self._usl_format_french_vat(self.vat),
                ),
                document_env._("Siège social : %(address)s", address=address),
            ]
        return [
            document_env._(
                "%(name)s, %(form)s with share capital of %(capital)s",
                name=self.name,
                form=self.usl_document_legal_form,
                capital=self.with_context(lang=locale).currency_id.format(
                    self.usl_document_share_capital
                ),
            ),
            document_env._(
                "Registry %(registry)s · VAT %(vat)s",
                registry=self.company_registry,
                vat=self.vat,
            ),
            address,
        ]

    def _usl_document_renderer_company_payload(self, locale):
        self.ensure_one()
        errors = self._usl_document_identity_errors()
        if errors:
            self._usl_document_raise_configuration_error(
                _("The legal identity is incomplete: %s", ", ".join(errors))
            )
        assets = []
        logo_digest = None
        builtin_logo = self._usl_document_builtin_logo()
        if self.logo and not self.uses_default_logo:
            content = self.logo.content
            mimetype = guess_mimetype(content)
            if mimetype not in {"image/png", "image/jpeg"}:
                self._usl_document_raise_configuration_error(
                    _("The company logo must be a PNG or JPEG image.")
                )
            logo_digest = hashlib.sha256(content).hexdigest()
            assets.append(
                {
                    "sha256": logo_digest,
                    "mime_type": mimetype,
                    "data": base64.b64encode(content).decode(),
                }
            )
        primary_color = (self.primary_color or "#714B67").upper()
        return (
            {
                "name": self.name,
                "primary_color": primary_color,
                "footer_label": self.name,
                "legal_identity_lines": self._usl_document_legal_lines(locale),
                "logo_asset": logo_digest,
                "builtin_logo": builtin_logo if logo_digest is None else None,
            },
            assets,
        )

    def _usl_document_raise_configuration_error(self, message):
        self.ensure_one()
        action = self.env.ref("base_setup.action_general_configuration")
        raise RedirectWarning(
            message=_("%s\n\nOpen Settings to correct the company identity or renderer configuration.", message),
            action=action.id,
            button_text=_("Open Settings"),
        )

    def action_usl_document_check_renderer(self):
        self.ensure_one()
        try:
            health = self.env["usl.document.renderer"].health()
        except Exception as error:
            self.write(
                {
                    "usl_document_renderer_status": "error",
                    "usl_document_renderer_checked_at": fields.Datetime.now(),
                    "usl_document_renderer_message": str(error),
                }
            )
            return False
        self.write(
            {
                "usl_document_renderer_status": "healthy",
                "usl_document_renderer_checked_at": fields.Datetime.now(),
                "usl_document_renderer_revision": health["template_revision"],
                "usl_document_renderer_version": health["engine_version"],
                "usl_document_renderer_message": _("Renderer is healthy."),
            }
        )
        return True
