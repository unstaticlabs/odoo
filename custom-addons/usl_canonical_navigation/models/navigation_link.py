import json
from urllib.parse import quote, urlencode

from odoo import _, api, models
from odoo.exceptions import AccessError, ValidationError

NAVIGATION_VERSION = 1
MAX_DIRECT_URL_LENGTH = 1800
MAX_DIRECT_SELECTION = 40
QUERY_ORDER = (
    "nv",
    "cids",
    "ws",
    "view_type",
    "domain",
    "groupBy",
    "orderBy",
    "favorite",
    "panel",
    "columns",
    "offset",
    "limit",
    "selection",
    "active",
    "date",
    "scale",
    "measures",
    "rows",
    "columnsBy",
    "pivot_order",
    "graph",
    "stacked",
    "cumulated",
    "tab",
    "parent_domain",
    "parent_groupBy",
    "parent_orderBy",
    "parent_favorite",
    "parent_panel",
    "parent_columns",
    "parent_offset",
    "parent_limit",
    "parent_selection",
    "report",
    "company",
    "period",
    "anchor",
    "date_from",
    "date_to",
    "moves",
    "comparison",
    "comparison_from",
    "comparison_to",
    "group",
    "journals",
    "accounts",
    "partners",
    "analytic_plans",
    "analytics",
    "search",
    "collapsed",
    "lang",
    "debug",
)
JSON_QUERY_KEYS = {
    "domain",
    "groupBy",
    "orderBy",
    "panel",
    "rows",
    "columnsBy",
    "pivot_order",
    "parent_domain",
    "parent_groupBy",
    "parent_orderBy",
}
CSV_QUERY_KEYS = {
    "columns",
    "selection",
    "measures",
    "journals",
    "accounts",
    "partners",
    "analytic_plans",
    "analytics",
    "collapsed",
}
BOOLEAN_QUERY_KEYS = {"stacked", "cumulated"}
ALLOWED_QUERY_KEYS = set(QUERY_ORDER)


def _stable_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class UslNavigationLink(models.AbstractModel):
    _name = "usl.navigation.link"
    _description = "Canonical Navigation Link Builder"

    @api.model
    def _allowed_company_ids(self, company_ids=None):
        allowed = set(self.env.user.company_ids.ids)
        if company_ids is not None and not isinstance(company_ids, (list, tuple, set)):
            raise ValidationError(_("Company scope must be a list of company identifiers."))
        requested = [
            int(company_id)
            for company_id in (company_ids or self.env.companies.ids)
        ]
        if not requested:
            requested = [self.env.company.id]
        if len(requested) != len(set(requested)) or not set(requested) <= allowed:
            raise AccessError(_("The requested company scope is not available."))
        return [requested[0], *sorted(set(requested[1:]))]

    @api.model
    def _check_target_access(self, model=None, res_id=None):
        if not model:
            return
        if model not in self.env:
            raise ValidationError(_("The requested model does not exist."))
        records = self.env[model].browse(int(res_id)) if res_id else self.env[model].browse()
        if res_id and not records.exists():
            raise AccessError(_("The requested record is not available."))
        records.check_access("read")

    @api.model
    def _check_action_access(self, action=None):
        if action in (None, False, "", "menu"):
            return self.env["ir.actions.actions"]
        action_value = str(action)
        action_record = self.env["ir.actions.actions"]
        if action_value.isdigit():
            action_record = action_record.sudo().browse(int(action_value)).exists()
        elif "." in action_value:
            action_record = self.env.ref(action_value, raise_if_not_found=False)
        else:
            action_record = action_record.sudo().search(
                [("path", "=", action_value)],
                limit=1,
            )
        if not action_record or not action_record._name.startswith("ir.actions."):
            raise AccessError(_("The requested action is not available."))
        if (
            action_record._name == "ir.actions.actions"
            and action_record.type in self.env
        ):
            action_record = self.env[action_record.type].sudo().browse(action_record.id)
        action_record = action_record.sudo()
        if (
            "group_ids" in action_record._fields
            and action_record.group_ids
            and not action_record.group_ids & self.env.user.all_group_ids
        ):
            raise AccessError(_("The requested action is not available."))
        if "res_model" in action_record._fields and action_record.res_model:
            self._check_target_access(action_record.res_model)
        return action_record

    @api.model
    def _action_segment(self, action):
        if action in (None, False, ""):
            return ""
        action_value = str(action)
        if action_value.isdigit() or "." in action_value:
            return f"action-{quote(action_value, safe='.')}"
        return quote(action_value, safe="-_")

    @api.model
    def _model_segment(self, model):
        if not model:
            return ""
        prefix = "" if "." in model else "m-"
        return f"{prefix}{quote(model, safe='.')}"

    @api.model
    def _normalize_query_value(self, key, value):
        if not value:
            return None
        if key in JSON_QUERY_KEYS:
            if key == "domain" and isinstance(value, str):
                return value
            return _stable_json(value)
        if key in CSV_QUERY_KEYS:
            values = value if isinstance(value, (list, tuple, set)) else str(value).split(",")
            values = [item for item in values if item not in (None, False, "")]
            if key not in {"collapsed"}:
                serialized = {str(item) for item in values}
                values = sorted(
                    serialized,
                    key=lambda item: (0, int(item)) if item.isdigit() else (1, item),
                )
            return ",".join(str(item) for item in values)
        if key in BOOLEAN_QUERY_KEYS:
            return "1" if value else "0"
        if isinstance(value, (dict, list, tuple)):
            return _stable_json(value)
        return str(value)

    @api.model
    def _canonical_query(self, state=None, company_ids=None, workspace=None):
        state = dict(state or {})
        unknown = set(state) - ALLOWED_QUERY_KEYS
        if unknown:
            raise ValidationError(
                _("Unsupported navigation state: %(keys)s", keys=", ".join(sorted(unknown))),
            )
        values = {
            "nv": NAVIGATION_VERSION,
            "cids": "-".join(
                str(company_id)
                for company_id in self._allowed_company_ids(company_ids)
            ),
        }
        if workspace:
            values["ws"] = str(workspace)
        else:
            for key in QUERY_ORDER:
                if key in {"nv", "cids", "ws"}:
                    continue
                normalized = self._normalize_query_value(key, state.get(key))
                if normalized is not None:
                    values[key] = normalized
        return [(key, values[key]) for key in QUERY_ORDER if key in values]

    @api.model
    def build_url(
        self,
        *,
        action=None,
        model=None,
        res_id=None,
        active_id=None,
        state=None,
        company_ids=None,
        workspace=None,
        absolute=True,
    ):
        """Build a deterministic backend URL without browser-session state."""
        self._check_action_access(action)
        self._check_target_access(model, res_id)
        segments = ["odoo"]
        if active_id:
            segments.append(str(int(active_id)))
        action_segment = self._action_segment(action)
        model_segment = self._model_segment(model)
        if action_segment:
            segments.append(action_segment)
        elif model_segment:
            segments.append(model_segment)
        if res_id:
            segments.append(str(int(res_id)))
        path = "/" + "/".join(segments)
        query = urlencode(
            self._canonical_query(state, company_ids, workspace),
            quote_via=quote,
        )
        relative = f"{path}?{query}" if query else path
        direct_selection = (state or {}).get("selection")
        if direct_selection:
            selection_count = len(
                direct_selection
                if isinstance(direct_selection, (list, tuple, set))
                else str(direct_selection).split(","),
            )
            if selection_count > MAX_DIRECT_SELECTION:
                raise ValidationError(
                    _(
                        "This selection requires a durable workspace instead of a direct URL.",
                    ),
                )
        if not workspace and len(relative.encode()) > MAX_DIRECT_URL_LENGTH:
            raise ValidationError(
                _("This navigation state requires a durable workspace instead of a direct URL."),
            )
        if not absolute:
            return relative
        base_url = self.env["ir.config_parameter"].get_str(
            "web.base.url",
            default="",
        ).rstrip("/")
        return f"{base_url}{relative}"

    @api.model
    def build_record_url(
        self,
        model,
        res_id,
        *,
        action=None,
        state=None,
        company_ids=None,
        absolute=True,
    ):
        self._check_target_access(model, res_id)
        return self.build_url(
            action=action,
            model=None if action else model,
            res_id=res_id,
            state=state,
            company_ids=company_ids,
            absolute=absolute,
        )

    @api.model
    def build_action_url(
        self,
        action,
        *,
        state=None,
        company_ids=None,
        absolute=True,
    ):
        return self.build_url(
            action=action,
            state=state,
            company_ids=company_ids,
            absolute=absolute,
        )

    @api.model
    def build_report_url(
        self,
        action,
        report_type,
        *,
        filters=None,
        company_ids=None,
        absolute=True,
    ):
        filters = dict(filters or {})
        normalized_companies = self._allowed_company_ids(company_ids)
        if filters.get("company") is not None:
            try:
                report_company_id = int(filters["company"])
            except (TypeError, ValueError) as error:
                raise ValidationError(_("The report company is malformed.")) from error
            if report_company_id != normalized_companies[0]:
                raise ValidationError(
                    _("The report company must match the canonical company scope."),
                )
        state = {"report": report_type, **filters}
        return self.build_action_url(
            action,
            state=state,
            company_ids=normalized_companies,
            absolute=absolute,
        )
