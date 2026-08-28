from odoo import api, fields, models
from odoo.exceptions import AccessError

WIDGET_KEYS = (
    "activities",
    "my_tasks",
    "favorites",
    "ai_pipelines",
    "accounting",
)


class ResUsersSettings(models.Model):
    _inherit = "res.users.settings"

    usl_home_layout = fields.Json(
        string="Home layout",
        default=lambda self: {"version": 1, "order": list(WIDGET_KEYS), "hidden": []},
        export_string_translation=False,
    )
    usl_home_favorites_initialized = fields.Boolean(
        string="Home favorites initialized",
        default=False,
        export_string_translation=False,
    )

    @api.model
    def _normalize_usl_home_layout(self, layout, available=None):
        available_keys = set(available or WIDGET_KEYS)
        source = layout if isinstance(layout, dict) else {}
        requested_order = source.get("order")
        requested_hidden = source.get("hidden")
        if not isinstance(requested_order, list):
            requested_order = []
        if not isinstance(requested_hidden, list):
            requested_hidden = []

        order = []
        for key in [*requested_order, *WIDGET_KEYS]:
            if key in available_keys and key not in order:
                order.append(key)
        hidden = [
            key
            for key in requested_hidden
            if key in available_keys and key in order
        ]
        return {"version": 1, "order": order, "hidden": list(dict.fromkeys(hidden))}

    def set_usl_home_layout(self, layout):
        self.ensure_one()
        if self.user_id != self.env.user and not self.env.su:
            raise AccessError(self.env._("You can only update your own Home layout."))
        normalized = self._normalize_usl_home_layout(layout)
        self.usl_home_layout = normalized
        return normalized
