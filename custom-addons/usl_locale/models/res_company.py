import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_AUTOMATIC_THEME_COLORS = (
    "#4E5AA8",
    "#B85C38",
    "#4F7A3A",
    "#2F6F8F",
    "#B07A2A",
    "#536A7A",
    "#A33F46",
    "#7A5B3A",
    "#6B5FA7",
    "#2D7D68",
    "#B44F7A",
    "#7C7A32",
    "#2E8793",
    "#3F6FB5",
    "#9A3E5E",
)


class ResCompany(models.Model):
    _inherit = "res.company"

    color = fields.Integer(string="Technical color index")
    usl_ui_theme_color = fields.Char(
        string="Color",
        help=(
            "Identifies this company in the Odoo interface. Leave empty to "
            "inherit the parent company's color or use the approved automatic palette."
        ),
    )
    usl_resolved_ui_theme_color = fields.Char(
        string="Resolved color",
        compute="_compute_usl_resolved_ui_theme_color",
        recursive=True,
    )

    @api.constrains("usl_ui_theme_color")
    def _check_usl_ui_theme_color(self):
        for company in self:
            if company.usl_ui_theme_color and not _HEX_COLOR_RE.fullmatch(
                company.usl_ui_theme_color,
            ):
                raise ValidationError(
                    _("Color must use the #RRGGBB format."),
                )

    @api.depends(
        "usl_ui_theme_color",
        "parent_id",
        "parent_id.usl_resolved_ui_theme_color",
    )
    def _compute_usl_resolved_ui_theme_color(self):
        for company in self:
            if company.usl_ui_theme_color:
                color = company.usl_ui_theme_color.upper()
            elif company.parent_id:
                color = company.parent_id.usl_resolved_ui_theme_color
            elif isinstance(company.id, int):
                color = _AUTOMATIC_THEME_COLORS[
                    (company.id - 1) % len(_AUTOMATIC_THEME_COLORS)
                ]
            else:
                color = _AUTOMATIC_THEME_COLORS[0]
            company.usl_resolved_ui_theme_color = color

    def _get_usl_ui_theme_color(self):
        self.ensure_one()
        return self.usl_resolved_ui_theme_color

    def action_use_automatic_usl_ui_theme_color(self):
        self.write({"usl_ui_theme_color": False})
        return True
