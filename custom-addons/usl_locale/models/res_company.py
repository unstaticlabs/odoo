import hashlib
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_AUTOMATIC_THEME_COLORS = (
    "#714B67",
    "#2F6F8F",
    "#2D7D68",
    "#8A5A2B",
    "#6B5FA7",
    "#9A3E5E",
)


class ResCompany(models.Model):
    _inherit = "res.company"

    usl_ui_theme_color = fields.Char(
        string="Interface color",
        help=(
            "Colors the Odoo navigation bar while this company is primary. "
            "Leave empty to use an automatically assigned company color."
        ),
    )

    @api.constrains("usl_ui_theme_color")
    def _check_usl_ui_theme_color(self):
        for company in self:
            if company.usl_ui_theme_color and not _HEX_COLOR_RE.fullmatch(
                company.usl_ui_theme_color,
            ):
                raise ValidationError(
                    _("Interface color must use the #RRGGBB format."),
                )

    def _get_usl_ui_theme_color(self):
        self.ensure_one()
        if self.usl_ui_theme_color:
            return self.usl_ui_theme_color.upper()
        identity = (
            self.company_registry
            or self.vat
            or self.name
            or str(self.id)
        ).strip().casefold()
        digest = hashlib.sha256(identity.encode()).digest()
        return _AUTOMATIC_THEME_COLORS[digest[0] % len(_AUTOMATIC_THEME_COLORS)]

    def action_use_automatic_usl_ui_theme_color(self):
        self.write({"usl_ui_theme_color": False})
        return True
