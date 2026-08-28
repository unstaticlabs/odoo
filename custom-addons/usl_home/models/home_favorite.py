from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

PROVIDER_KEYS = {"my_tasks", "accounting_hygiene", "ai_pipelines"}
TARGET_TYPES = {"provider", "action", "view", "record"}


class UslHomeFavorite(models.Model):
    _name = "usl.home.favorite"
    _description = "USL Home Favorite Destination"
    _order = "sequence, id"

    user_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
        ondelete="cascade",
        index=True,
    )
    name = fields.Char(required=True, translate=False)
    sequence = fields.Integer(default=10, index=True)
    target_type = fields.Selection(
        [
            ("provider", "Home destination"),
            ("action", "Odoo action"),
            ("view", "Saved view"),
            ("record", "Record"),
        ],
        required=True,
        default="action",
    )
    provider_key = fields.Char(export_string_translation=False)
    action_id = fields.Many2one("ir.actions.actions", ondelete="set null")
    action_xmlid = fields.Char(export_string_translation=False)
    menu_id = fields.Many2one("ir.ui.menu", ondelete="set null")
    filter_id = fields.Many2one("ir.filters", ondelete="set null")
    res_model = fields.Char(export_string_translation=False)
    res_id = fields.Integer(export_string_translation=False)
    view_mode = fields.Char(export_string_translation=False)
    domain_json = fields.Json(default=list, export_string_translation=False)
    context_json = fields.Json(default=dict, export_string_translation=False)
    group_by_json = fields.Json(default=list, export_string_translation=False)
    order_by_json = fields.Json(default=list, export_string_translation=False)
    company_id = fields.Many2one("res.company", ondelete="set null")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            requested_user = vals.get("user_id", self.env.uid)
            if requested_user != self.env.uid and not self.env.su:
                raise AccessError(self.env._("You can only create your own Home favorites."))
            vals["user_id"] = requested_user
        return super().create(vals_list)

    def write(self, vals):
        if "user_id" in vals and vals["user_id"] != self.env.uid and not self.env.su:
            raise AccessError(self.env._("You can only update your own Home favorites."))
        return super().write(vals)

    @api.constrains(
        "target_type",
        "provider_key",
        "action_id",
        "action_xmlid",
        "res_model",
        "res_id",
        "domain_json",
        "context_json",
        "group_by_json",
        "order_by_json",
    )
    def _check_target(self):
        for favorite in self:
            if favorite.target_type not in TARGET_TYPES:
                raise ValidationError(self.env._("Unsupported Home destination type."))
            if favorite.target_type == "provider" and favorite.provider_key not in PROVIDER_KEYS:
                raise ValidationError(self.env._("Unsupported Home destination."))
            if favorite.target_type in {"action", "view"} and not (
                favorite.action_id or favorite.action_xmlid
            ):
                raise ValidationError(self.env._("An Odoo action is required."))
            if favorite.target_type == "record" and not (
                favorite.res_model and favorite.res_id > 0
            ):
                raise ValidationError(self.env._("A valid record destination is required."))
            if not isinstance(favorite.domain_json or [], list):
                raise ValidationError(self.env._("The saved domain must be a list."))
            if not isinstance(favorite.context_json or {}, dict):
                raise ValidationError(self.env._("The saved context must be an object."))
            if not isinstance(favorite.group_by_json or [], list):
                raise ValidationError(self.env._("The saved grouping must be a list."))
            if not isinstance(favorite.order_by_json or [], list):
                raise ValidationError(self.env._("The saved ordering must be a list."))

    @api.model
    def add_current_destination(self, payload):
        if not isinstance(payload, dict):
            raise ValidationError(self.env._("Invalid Home destination."))
        action_id = int(payload.get("action_id") or 0)
        res_model = payload.get("res_model")
        res_id = int(payload.get("res_id") or 0)
        if not action_id:
            raise ValidationError(self.env._("This view does not expose a stable Odoo action."))
        # Odoo's action service reads this technical routing metadata with
        # elevated access; ordinary internal users intentionally have no raw
        # ``ir.actions.actions`` ACL. Mirror that narrow boundary, then validate
        # the destination model, record, menu, and company as the current user.
        action = self.env["ir.actions.actions"].sudo().browse(action_id).exists()
        if not action:
            raise ValidationError(self.env._("The Odoo action is no longer available."))
        if action.type != "ir.actions.act_window":
            raise ValidationError(self.env._("Only Odoo view actions can be added to Home."))
        window_action = self.env[action.type].sudo().browse(action.id).exists()
        if not window_action or not window_action.res_model:
            raise ValidationError(self.env._("The Odoo view is no longer available."))
        if not self.env[window_action.res_model].has_access("read"):
            raise AccessError(self.env._("You cannot add this view to Home."))
        target_type = "record" if res_model and res_id else "view"
        if target_type == "record":
            if res_model not in self.env.registry:
                raise ValidationError(self.env._("The record type is not available."))
            record = self.env[res_model].browse(res_id).exists()
            if not record or not record.has_access("read"):
                raise AccessError(self.env._("You cannot add this record to Home."))
        xmlid = action.get_external_id().get(action.id)
        menu_id = int(payload.get("menu_id") or 0)
        menu = self.env["ir.ui.menu"].browse(menu_id).exists() if menu_id else False
        if menu and not menu._filter_visible_menus():
            menu = False
        vals = {
            "name": (payload.get("name") or self.env._("Saved destination"))[:120],
            "target_type": target_type,
            "action_id": action.id,
            "action_xmlid": xmlid,
            "menu_id": menu.id if menu else False,
            "res_model": res_model if target_type == "record" else False,
            "res_id": res_id if target_type == "record" else 0,
            "view_mode": (payload.get("view_mode") or "")[:40],
            "domain_json": payload.get("domain") if isinstance(payload.get("domain"), list) else [],
            "context_json": payload.get("context") if isinstance(payload.get("context"), dict) else {},
            "group_by_json": payload.get("group_by") if isinstance(payload.get("group_by"), list) else [],
            "order_by_json": payload.get("order_by") if isinstance(payload.get("order_by"), list) else [],
            "sequence": (self.search_count([("user_id", "=", self.env.uid)]) + 1) * 10,
        }
        if target_type == "record" and "company_id" in record._fields:
            vals["company_id"] = record.company_id.id
        favorite = self.create(vals)
        return {"id": favorite.id, "name": favorite.name}

    @api.model
    def reorder(self, favorite_ids):
        favorites = self.search(
            [("user_id", "=", self.env.uid), ("id", "in", favorite_ids)],
        )
        by_id = {favorite.id: favorite for favorite in favorites}
        for sequence, favorite_id in enumerate(favorite_ids, start=1):
            if favorite_id in by_id:
                by_id[favorite_id].sequence = sequence * 10
        return True
