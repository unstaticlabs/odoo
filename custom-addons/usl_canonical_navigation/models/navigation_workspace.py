import ast
import json
import re
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .navigation_link import ALLOWED_QUERY_KEYS, NAVIGATION_VERSION

MAX_WORKSPACE_BYTES = 256 * 1024
FORBIDDEN_KEY_PARTS = {
    "access_token",
    "api_key",
    "content",
    "globalstate",
    "password",
    "reasoning",
    "secret",
    "token",
    "unsaved",
}
WORKSPACE_ONLY_KEYS = {"selection_mode"}
TARGET_KEYS = {
    "action",
    "model",
    "res_id",
    "active_id",
    *ALLOWED_QUERY_KEYS,
    *WORKSPACE_ONLY_KEYS,
}
FILTER_ID_MODELS = {
    "favorite": "ir.filters",
    "journals": "account.journal",
    "accounts": "account.account",
    "partners": "res.partner",
    "analytic_plans": "account.analytic.plan",
    "analytics": "account.analytic.account",
}
PANEL_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UslNavigationWorkspace(models.Model):
    _name = "usl.navigation.workspace"
    _description = "Durable Navigation Workspace"
    _order = "write_date desc, id desc"

    name = fields.Char(required=True, default="Workspace")
    public_id = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        index=True,
        default=lambda self: str(uuid.uuid4()),
    )
    owner_id = fields.Many2one(
        "res.users",
        required=True,
        readonly=True,
        index=True,
        default=lambda self: self.env.user,
        ondelete="cascade",
    )
    permitted_user_ids = fields.Many2many(
        "res.users",
        "usl_navigation_workspace_user_rel",
        "workspace_id",
        "user_id",
        string="Permitted Users",
    )
    share_mode = fields.Selection(
        [
            ("owner", "Owner only"),
            ("users", "Permitted users"),
            ("internal", "All internal users"),
        ],
        required=True,
        default="owner",
    )
    company_ids = fields.Many2many(
        "res.company",
        "usl_navigation_workspace_company_rel",
        "workspace_id",
        "company_id",
        required=True,
        default=lambda self: self.env.companies,
    )
    action_ref = fields.Char(index=True)
    res_model = fields.Char(index=True)
    state_version = fields.Integer(required=True, default=NAVIGATION_VERSION)
    state_json = fields.Json(required=True, default=dict)
    automatic = fields.Boolean(default=False)
    active = fields.Boolean(default=True)
    last_used_at = fields.Datetime(readonly=True)

    _public_id_unique = models.Constraint(
        "UNIQUE(public_id)",
        "A navigation workspace identifier must be unique.",
    )

    @api.model
    def _validate_json_value(self, value, *, path="state", depth=0):
        if depth > 10:
            raise ValidationError(_("Navigation state is nested too deeply."))
        if value is None or isinstance(value, (bool, int, float, str)):
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                self._validate_json_value(item, path=f"{path}[{index}]", depth=depth + 1)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                normalized_key = str(key).replace("_", "").lower()
                if any(part.replace("_", "") in normalized_key for part in FORBIDDEN_KEY_PARTS):
                    raise ValidationError(
                        _("Navigation state contains a forbidden value at %(path)s.", path=path),
                    )
                self._validate_json_value(
                    item,
                    path=f"{path}.{key}",
                    depth=depth + 1,
                )
            return
        raise ValidationError(_("Navigation state contains an unsupported value."))

    @api.model
    def _normalize_state(self, state):
        if not isinstance(state, dict):
            raise ValidationError(_("Navigation state must be an object."))
        unknown = set(state) - TARGET_KEYS
        if unknown:
            raise ValidationError(
                _("Unsupported navigation state: %(keys)s", keys=", ".join(sorted(unknown))),
            )
        state = dict(state)
        if state.get("selection_mode") not in (None, "domain"):
            raise ValidationError(_("Saved selection state is malformed."))
        if state.get("selection_mode") == "domain" and not state.get("domain"):
            raise ValidationError(_("A domain selection requires a saved filter domain."))
        panel = state.get("panel")
        if isinstance(panel, str):
            try:
                panel = json.loads(panel)
            except json.JSONDecodeError as error:
                raise ValidationError(_("Search-panel state is malformed.")) from error
        if panel is not None:
            if (
                not isinstance(panel, dict)
                or len(panel) > 30
                or any(not PANEL_FIELD_RE.fullmatch(str(key)) for key in panel)
            ):
                raise ValidationError(_("Search-panel state is malformed."))
            normalized_panel = {}
            for field_name, raw_value in panel.items():
                is_collection = isinstance(raw_value, list)
                values = raw_value if is_collection else [raw_value]
                if len(values) > 200:
                    raise ValidationError(_("Search-panel state is too large."))
                normalized_values = []
                for value in values:
                    if isinstance(value, bool) or not isinstance(value, (int, str)):
                        raise ValidationError(_("Search-panel state is malformed."))
                    if isinstance(value, int) and value <= 0:
                        raise ValidationError(_("Search-panel state is malformed."))
                    if isinstance(value, str) and (
                        not value
                        or len(value) > 128
                        or any(ord(character) < 32 for character in value)
                    ):
                        raise ValidationError(_("Search-panel state is malformed."))
                    normalized_values.append(value)
                if len({str(value) for value in normalized_values}) != len(normalized_values):
                    raise ValidationError(_("Search-panel state contains duplicate values."))
                normalized_values.sort(key=lambda value: (str(value).zfill(20), str(value)))
                normalized_panel[field_name] = (
                    normalized_values if is_collection else normalized_values[0]
                )
            state["panel"] = normalized_panel
        self._validate_json_value(state)
        encoded = json.dumps(
            state,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        if len(encoded) > MAX_WORKSPACE_BYTES:
            raise ValidationError(_("This workspace is too large to save safely."))
        normalized = json.loads(encoded)
        normalized["nv"] = NAVIGATION_VERSION
        return normalized

    @api.model
    def _check_state_access(self, state, company_ids):
        allowed_companies = set(self.env.user.company_ids.ids)
        requested_companies = {int(company_id) for company_id in company_ids}
        if not requested_companies or not requested_companies <= allowed_companies:
            raise AccessError(_("The workspace company scope is not available."))
        if state.get("company") is not None:
            try:
                report_company_id = int(state["company"])
            except (TypeError, ValueError) as error:
                raise ValidationError(_("The report company is malformed.")) from error
            if report_company_id != int(company_ids[0]):
                raise AccessError(_("The report company does not match the workspace scope."))
        model_name = state.get("model")
        res_id = state.get("res_id")
        action_record = self.env["usl.navigation.link"]._check_action_access(
            state.get("action"),
        )
        if (
            not model_name
            and action_record
            and "res_model" in action_record._fields
            and action_record.res_model
        ):
            model_name = action_record.res_model
        if model_name:
            self.env["usl.navigation.link"]._check_target_access(model_name, res_id)
        if state.get("selection_mode") == "domain":
            if not model_name:
                raise AccessError(_("The saved domain selection has no target model."))
            domain = state.get("domain")
            if isinstance(domain, str):
                try:
                    domain = ast.literal_eval(domain)
                except (SyntaxError, ValueError) as error:
                    raise ValidationError(_("The saved selection filter is malformed.")) from error
            if not isinstance(domain, (list, tuple)):
                raise ValidationError(_("The saved selection filter is malformed."))
            self.env[model_name].search(domain, limit=1)
        selection = state.get("selection")
        if selection and model_name:
            selected_ids = {
                int(record_id)
                for record_id in (
                    selection
                    if isinstance(selection, list)
                    else str(selection).split(",")
                )
                if record_id not in (None, False, "")
            }
            accessible_ids = set(
                self.env[model_name].search([("id", "in", list(selected_ids))]).ids,
            )
            if selected_ids != accessible_ids:
                raise AccessError(_("The saved selection is not fully available."))
        for state_key, target_model in FILTER_ID_MODELS.items():
            values = state.get(state_key)
            if not values or target_model not in self.env:
                continue
            record_ids = {
                int(record_id)
                for record_id in (
                    values
                    if isinstance(values, list)
                    else str(values).split(",")
                )
                if record_id not in (None, False, "")
            }
            accessible_ids = set(
                self.env[target_model].search([("id", "in", list(record_ids))]).ids,
            )
            if record_ids != accessible_ids:
                raise AccessError(_("A saved workspace filter is not available."))

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for values in vals_list:
            values = dict(values)
            values["owner_id"] = self.env.user.id
            values["public_id"] = str(uuid.uuid4())
            state = self._normalize_state(values.get("state_json") or {})
            company_commands = values.get("company_ids")
            company_ids = self.env.companies.ids
            if company_commands:
                company_ids = next(
                    (
                        command[2]
                        for command in company_commands
                        if command[0] == fields.Command.SET
                    ),
                    company_ids,
                )
            self._check_state_access(state, company_ids)
            values["state_json"] = state
            prepared.append(values)
        return super().create(prepared)

    def write(self, values):
        if any(workspace.owner_id != self.env.user for workspace in self):
            raise AccessError(_("Only the workspace owner can modify it."))
        values = dict(values)
        if "owner_id" in values or "public_id" in values:
            raise AccessError(_("Workspace ownership and identifiers are immutable."))
        if "state_json" in values:
            values["state_json"] = self._normalize_state(values["state_json"])
        result = super().write(values)
        for workspace in self:
            workspace._check_state_access(
                workspace.state_json,
                workspace.company_ids.ids,
            )
        return result

    @api.model
    def create_workspace(
        self,
        state,
        *,
        name=None,
        share_mode="owner",
        permitted_user_ids=None,
        company_ids=None,
        automatic=False,
    ):
        company_ids = company_ids or self.env.companies.ids
        workspace = self.create({
            "name": name or _("Workspace"),
            "share_mode": share_mode,
            "permitted_user_ids": [
                fields.Command.set(permitted_user_ids or []),
            ],
            "company_ids": [fields.Command.set(company_ids)],
            "action_ref": str(state.get("action") or ""),
            "res_model": state.get("model") or "",
            "state_json": state,
            "automatic": automatic,
        })
        return {
            "public_id": workspace.public_id,
            "url": workspace.canonical_url(),
        }

    @api.model
    def validate_state(self, state, company_ids):
        try:
            normalized = self._normalize_state(state)
            self._check_state_access(normalized, company_ids)
        except (AccessError, ValidationError, TypeError, ValueError):
            return {"status": "unavailable"}
        return {"status": "ok"}

    @api.model
    def read_workspace(self, public_id):
        workspace = self.search([
            ("public_id", "=", str(public_id)),
            ("active", "=", True),
        ], limit=1)
        if not workspace:
            return {"status": "unavailable"}
        try:
            workspace._check_state_access(
                workspace.state_json,
                workspace.company_ids.ids,
            )
        except AccessError:
            return {"status": "unavailable"}
        workspace.with_user(workspace.owner_id).last_used_at = fields.Datetime.now()
        return {
            "status": "ok",
            "public_id": workspace.public_id,
            "state": workspace.state_json,
            "company_ids": workspace.company_ids.ids,
            "owner": workspace.owner_id == self.env.user,
        }

    def canonical_url(self):
        self.ensure_one()
        self.check_access("read")
        return self.env["usl.navigation.link"].build_url(
            action="usl-workspace",
            company_ids=self.company_ids.ids,
            workspace=self.public_id,
        )
