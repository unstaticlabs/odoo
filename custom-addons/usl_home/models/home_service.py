from datetime import date, timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .home_favorite import PROVIDER_KEYS
from .res_users_settings import WIDGET_KEYS

AI_DISCOVERY_TAGS = {
    "agent ready",
    "agent failed",
    "needs human",
    "human approved",
    "has pr",
    "pipeline",
}
AI_ATTENTION_TAGS = {"agent failed", "needs human", "blocked"}


class UslHomeService(models.AbstractModel):
    _name = "usl.home.service"
    _description = "USL Home Service"

    @api.model
    def _ensure_internal(self):
        if not self.env.user._is_internal():
            raise AccessError(self.env._("Home is available to internal users only."))

    @api.model
    def _model_is_readable(self, model_name):
        if model_name not in self.env.registry:
            return False
        try:
            return self.env[model_name].has_access("read")
        except AccessError:
            return False

    @api.model
    def _tag_map(self, names):
        tags = self.env["project.tags"].search([])
        wanted = {name.casefold() for name in names}
        return {
            (tag.name or "").strip().casefold(): tag.id
            for tag in tags
            if (tag.name or "").strip().casefold() in wanted
        }

    @api.model
    def _ai_workspace_ids(self):
        if not self._model_is_readable("project.task"):
            return []
        markers = self._tag_map(AI_DISCOVERY_TAGS)
        if not markers:
            return []
        grouped = self.env["project.task"]._read_group(
            [("tag_ids", "in", list(markers.values()))],
            ["project_id"],
            [],
        )
        return [project.id for (project,) in grouped if project]

    @api.model
    def _accounting_overview(self):
        if not self._model_is_readable("rebuild.account.overview"):
            return self.env["rebuild.account.overview"] if "rebuild.account.overview" in self.env.registry else False
        return self.env["rebuild.account.overview"].search(
            [("company_id", "=", self.env.company.id)],
            limit=1,
        )

    @api.model
    def _available_widgets(self):
        available = ["activities"]
        if self._model_is_readable("project.task"):
            available.append("my_tasks")
        available.append("favorites")
        if self._ai_workspace_ids():
            available.append("ai_pipelines")
        if self._accounting_overview():
            available.append("accounting")
        return [key for key in WIDGET_KEYS if key in available]

    @api.model
    def _seed_default_favorites(self, settings):
        if settings.usl_home_favorites_initialized:
            return
        Favorite = self.env["usl.home.favorite"]
        providers = []
        if self._model_is_readable("project.task"):
            providers.append(("my_tasks", self.env._("My Tasks")))
        if self._accounting_overview():
            providers.append(("accounting_hygiene", self.env._("Accounting Hygiene")))
        if self._ai_workspace_ids():
            providers.append(("ai_pipelines", self.env._("AI Pipelines")))
        for sequence, (key, name) in enumerate(providers, start=1):
            Favorite.create(
                {
                    "name": name,
                    "target_type": "provider",
                    "provider_key": key,
                    "sequence": sequence * 10,
                },
            )
        settings.usl_home_favorites_initialized = True

    @api.model
    def get_configuration(self):
        self._ensure_internal()
        settings = self.env["res.users.settings"]._find_or_create_for_user(self.env.user)
        self._seed_default_favorites(settings)
        available = self._available_widgets()
        layout = settings._normalize_usl_home_layout(settings.usl_home_layout, available)
        if layout != settings.usl_home_layout:
            settings.usl_home_layout = layout
        favorites = self.env["usl.home.favorite"].search([("user_id", "=", self.env.uid)])
        return {
            "layout": layout,
            "available_widgets": available,
            "active_company": {
                "id": self.env.company.id,
                "name": self.env.company.display_name,
            },
            "favorites": [self._favorite_summary(favorite) for favorite in favorites],
            "available_destinations": self._available_provider_choices(favorites),
        }

    @api.model
    def save_layout(self, layout):
        self._ensure_internal()
        settings = self.env["res.users.settings"]._find_or_create_for_user(self.env.user)
        normalized = settings._normalize_usl_home_layout(layout, self._available_widgets())
        settings.usl_home_layout = normalized
        return normalized

    @api.model
    def _available_provider_choices(self, favorites):
        used = set(favorites.filtered(lambda favorite: favorite.target_type == "provider").mapped("provider_key"))
        choices = []
        if "my_tasks" not in used and self._model_is_readable("project.task"):
            choices.append({"key": "my_tasks", "name": self.env._("My Tasks")})
        if "accounting_hygiene" not in used and self._accounting_overview():
            choices.append({"key": "accounting_hygiene", "name": self.env._("Accounting Hygiene")})
        if "ai_pipelines" not in used and self._ai_workspace_ids():
            choices.append({"key": "ai_pipelines", "name": self.env._("AI Pipelines")})
        return choices

    @api.model
    def add_provider_favorite(self, provider_key):
        self._ensure_internal()
        if provider_key not in PROVIDER_KEYS:
            raise UserError(self.env._("This Home destination is not available."))
        allowed = {choice["key"]: choice["name"] for choice in self._available_provider_choices(
            self.env["usl.home.favorite"].search([("user_id", "=", self.env.uid)]),
        )}
        if provider_key not in allowed:
            raise UserError(self.env._("This Home destination is already added or unavailable."))
        favorite = self.env["usl.home.favorite"].create(
            {
                "name": allowed[provider_key],
                "target_type": "provider",
                "provider_key": provider_key,
                "sequence": (self.env["usl.home.favorite"].search_count([("user_id", "=", self.env.uid)]) + 1) * 10,
            },
        )
        return self._favorite_summary(favorite)

    @api.model
    def _favorite_summary(self, favorite):
        available = self._favorite_is_available(favorite)
        kind_label = False
        icon = "destination"
        if available:
            if favorite.target_type == "provider":
                provider_metadata = {
                    "my_tasks": (self.env._("Project"), "tasks"),
                    "accounting_hygiene": (self.env._("Accounting"), "accounting"),
                    "ai_pipelines": (self.env._("AI workspace"), "ai"),
                }
                kind_label, icon = provider_metadata.get(
                    favorite.provider_key,
                    (self.env._("Workflow"), "destination"),
                )
            elif favorite.filter_id:
                kind_label, icon = self.env._("Saved view"), "view"
            elif (
                favorite.res_model == "project.task"
                and (favorite.context_json or {}).get("active_id")
            ):
                kind_label, icon = self.env._("Project"), "project"
            elif favorite.target_type == "record":
                kind_label, icon = self.env._("Record"), "record"
            else:
                kind_label, icon = self.env._("Workflow"), "destination"
        return {
            "id": favorite.id,
            "name": favorite.name if available else self.env._("Destination unavailable"),
            "available": available,
            "kind": favorite.target_type,
            "kind_label": kind_label,
            "icon": icon,
            "company_name": favorite.company_id.display_name if available and favorite.company_id else False,
        }

    @api.model
    def _favorite_is_available(self, favorite):
        if favorite.user_id != self.env.user:
            return False
        if favorite.company_id and favorite.company_id not in self.env.user.company_ids:
            return False
        if favorite.target_type == "provider":
            if favorite.provider_key == "my_tasks":
                return self._model_is_readable("project.task")
            if favorite.provider_key == "accounting_hygiene":
                return bool(self._accounting_overview())
            if favorite.provider_key == "ai_pipelines":
                return bool(self._ai_workspace_ids())
            return False
        action = self._favorite_action_record(favorite)
        if favorite.target_type in {"action", "view"} and not action:
            return False
        if favorite.menu_id and not favorite.menu_id.exists()._filter_visible_menus():
            return False
        model_name = favorite.res_model or (action.res_model if action and "res_model" in action._fields else False)
        if model_name and not self._model_is_readable(model_name):
            return False
        if favorite.target_type == "record":
            if favorite.res_model not in self.env.registry:
                return False
            record = self.env[favorite.res_model].browse(favorite.res_id).exists()
            return bool(record and record.has_access("read"))
        return True

    @api.model
    def _favorite_action_record(self, favorite):
        if favorite.action_id:
            action = favorite.action_id.sudo().exists()
            return self.env[action.type].sudo().browse(action.id).exists() if action else action
        if favorite.action_xmlid:
            action = self.env.ref(favorite.action_xmlid, raise_if_not_found=False)
            return action.sudo() if action else action
        return False

    @api.model
    def resolve_favorite(self, favorite_id):
        self._ensure_internal()
        favorite = self.env["usl.home.favorite"].search(
            [("id", "=", favorite_id), ("user_id", "=", self.env.uid)],
            limit=1,
        )
        if not favorite or not self._favorite_is_available(favorite):
            return {"available": False}
        if favorite.target_type == "provider":
            action = self._provider_action(favorite.provider_key)
        elif favorite.target_type == "record":
            action = {
                "type": "ir.actions.act_window",
                "name": favorite.name,
                "res_model": favorite.res_model,
                "res_id": favorite.res_id,
                "views": [(False, "form")],
                "view_mode": "form",
                "target": "current",
            }
        else:
            action = self._favorite_action_record(favorite)._get_action_dict()
            if favorite.target_type == "view":
                action["domain"] = favorite.domain_json or []
                context = dict(favorite.context_json or {})
                if favorite.group_by_json:
                    context["group_by"] = favorite.group_by_json
                if favorite.order_by_json:
                    context["orderedBy"] = favorite.order_by_json
                action["context"] = context
                if favorite.view_mode:
                    action["view_mode"] = favorite.view_mode
        return {
            "available": True,
            "action": action,
            "menu_id": favorite.menu_id.id if favorite.menu_id else False,
            "company_id": favorite.company_id.id if favorite.company_id else False,
        }

    @api.model
    def _provider_action(self, provider_key):
        if provider_key == "my_tasks":
            return self.env["ir.actions.actions"]._for_xml_id("project.action_view_my_task")
        if provider_key == "accounting_hygiene":
            overview = self._accounting_overview()
            if not overview:
                raise AccessError(self.env._("Accounting Hygiene is not available."))
            return overview.action_open_hygiene_issues()
        if provider_key == "ai_pipelines":
            return self.get_ai_workspace_action()
        raise UserError(self.env._("This Home destination is not available."))

    @api.model
    def get_activities(self):
        self._ensure_internal()
        # ``res_access_read`` is an intentionally hidden pseudo-field whose
        # search implementation asserts a superuser environment, then drops
        # back to the original user to evaluate the related record rules.
        # Keep sudo limited to that native access-domain computation and read
        # every returned field again as the current user.
        activities = self.env["mail.activity"].sudo().search(
            [
                ("user_id", "=", self.env.uid),
                ("active", "=", True),
                ("res_access_read", "=", True),
            ],
            order="date_deadline ASC, id ASC",
            limit=5,
        ).sudo(False)
        today = fields.Date.context_today(self)
        items = []
        for activity in activities:
            if activity.date_deadline < today:
                bucket = "overdue"
            elif activity.date_deadline == today:
                bucket = "today"
            else:
                bucket = "future"
            items.append(
                {
                    "id": activity.id,
                    "summary": activity.summary or activity.activity_type_id.display_name,
                    "activity_type": activity.activity_type_id.display_name,
                    "record_name": activity.res_name,
                    "model_name": activity.res_model_id.name,
                    "res_model": activity.res_model,
                    "res_id": activity.res_id,
                    "deadline": fields.Date.to_string(activity.date_deadline),
                    "bucket": bucket,
                },
            )
        return {"items": items, "today": fields.Date.to_string(today)}

    @api.model
    def _user_date_bounds(self):
        today = fields.Date.context_today(self)
        return today, today + timedelta(days=1), today + timedelta(days=8)

    @api.model
    def _my_tasks_domain(self):
        return [
            ("user_ids", "in", self.env.uid),
            ("has_template_ancestor", "=", False),
            ("has_project_template", "=", False),
        ]

    @api.model
    def get_my_tasks(self):
        self._ensure_internal()
        if not self._model_is_readable("project.task"):
            raise AccessError(self.env._("My Tasks is not available."))
        Task = self.env["project.task"]
        base_domain = self._my_tasks_domain()
        open_domain = [*base_domain, ("is_closed", "=", False)]
        grouped = Task._read_group(
            [*open_domain, ("stage_id.fold", "=", False)],
            ["stage_id"],
            ["__count"],
        )
        stages = sorted(
            (
                {"id": stage.id, "name": stage.display_name, "count": count}
                for stage, count in grouped
                if stage
            ),
            key=lambda item: (-item["count"], item["name"].casefold(), item["id"]),
        )[:4]
        start, tomorrow, due_soon = self._user_date_bounds()
        signals = {
            "overdue": Task.search_count(
                [*open_domain, ("date_deadline", "!=", False), ("date_deadline", "<", start)],
            ),
            "due_soon": Task.search_count(
                [*open_domain, ("date_deadline", ">=", start), ("date_deadline", "<", due_soon)],
            ),
            "waiting": Task.search_count([*open_domain, ("state", "=", "04_waiting_normal")]),
            "changes_requested": Task.search_count(
                [*open_domain, ("state", "=", "02_changes_requested")],
            ),
        }
        return {"stages": stages, "signals": signals, "today_end": tomorrow}

    @api.model
    def get_my_tasks_action(self, filter_type, filter_value):
        """Open the exact task population represented by a Home metric."""
        self._ensure_internal()
        if not self._model_is_readable("project.task"):
            raise AccessError(self.env._("My Tasks is not available."))

        open_domain = [*self._my_tasks_domain(), ("is_closed", "=", False)]
        start, _tomorrow, due_soon = self._user_date_bounds()
        if filter_type == "signal":
            filters = {
                "overdue": (
                    self.env._("Overdue"),
                    [
                        ("date_deadline", "!=", False),
                        ("date_deadline", "<", start),
                    ],
                ),
                "due_soon": (
                    self.env._("Due in 7 days"),
                    [
                        ("date_deadline", ">=", start),
                        ("date_deadline", "<", due_soon),
                    ],
                ),
                "waiting": (
                    self.env._("Waiting"),
                    [("state", "=", "04_waiting_normal")],
                ),
                "changes_requested": (
                    self.env._("Changes requested"),
                    [("state", "=", "02_changes_requested")],
                ),
            }
            if filter_value not in filters:
                raise UserError(self.env._("This task metric is not available."))
            label, metric_domain = filters[filter_value]
        elif filter_type == "stage":
            if isinstance(filter_value, bool) or not isinstance(filter_value, int):
                raise UserError(self.env._("This task metric is not available."))
            stage = self.env["project.task.type"].search(
                [("id", "=", filter_value)],
                limit=1,
            )
            if not stage:
                raise UserError(self.env._("This task metric is not available."))
            label = stage.display_name
            metric_domain = [
                ("stage_id", "=", stage.id),
                ("stage_id.fold", "=", False),
            ]
        else:
            raise UserError(self.env._("This task metric is not available."))

        action = self.env["ir.actions.actions"]._for_xml_id(
            "project.action_view_my_task"
        )
        action["name"] = self.env._("My Tasks — %s", label)
        action["domain"] = [*open_domain, *metric_domain]
        return action

    @api.model
    def get_ai_attention(self):
        self._ensure_internal()
        workspace_ids = self._ai_workspace_ids()
        if not workspace_ids:
            raise AccessError(self.env._("AI Pipelines is not available."))
        tags = self._tag_map(AI_ATTENTION_TAGS)
        Task = self.env["project.task"]
        base = [
            ("project_id", "in", workspace_ids),
            ("user_ids", "in", self.env.uid),
            ("is_closed", "=", False),
        ]
        review_stages = self.env["project.task.type"].search(
            [("project_ids", "in", workspace_ids), ("name", "=ilike", "Review")],
        )
        seen = set()
        ranked = []
        sources = [
            ("failed", [("tag_ids", "in", [tags.get("agent failed", 0)])]),
            ("blocked", [("tag_ids", "in", [tags.get("blocked", 0)])]),
            (
                "review",
                [
                    "|",
                    "|",
                    ("tag_ids", "in", [tags.get("needs human", 0)]),
                    ("state", "=", "02_changes_requested"),
                    ("stage_id", "in", review_stages.ids),
                ],
            ),
        ]
        for rank, (status, extra_domain) in enumerate(sources):
            for task in Task.search(
                [*base, *extra_domain],
                order="date_deadline ASC NULLS LAST, id ASC",
                limit=5,
            ):
                if task.id in seen:
                    continue
                seen.add(task.id)
                ranked.append((rank, task, status))
        ranked.sort(
            key=lambda row: (
                row[0],
                row[1].date_deadline or date.max,
                row[1].id,
            ),
        )
        return {
            "items": [
                {
                    "id": task.id,
                    "name": task.display_name,
                    "project": task.project_id.display_name,
                    "status": status,
                    "deadline": fields.Date.to_string(task.date_deadline) if task.date_deadline else False,
                }
                for _rank, task, status in ranked[:5]
            ],
        }

    @api.model
    def get_ai_workspace_action(self):
        self._ensure_internal()
        workspace_ids = self._ai_workspace_ids()
        if not workspace_ids:
            raise AccessError(self.env._("AI Pipelines is not available."))
        action = self.env["ir.actions.actions"]._for_xml_id("project.action_view_my_task")
        action["name"] = self.env._("AI Pipelines")
        action["domain"] = [
            ("project_id", "in", workspace_ids),
            ("user_ids", "in", self.env.uid),
            ("has_template_ancestor", "=", False),
            ("has_project_template", "=", False),
        ]
        return action

    @api.model
    def get_accounting_alerts(self):
        self._ensure_internal()
        overview = self._accounting_overview()
        if not overview:
            raise AccessError(self.env._("Accounting alerts are not available."))
        declaration_count = overview.overdue_declaration_count
        if overview.next_declaration_status == "blocked" and not declaration_count:
            declaration_count = 1
        candidates = [
            (0, "closing", self.env._("Closing blockers"), overview.latest_closing_blocking_count, "blocked"),
            (1, "declarations", self.env._("Declarations requiring attention"), declaration_count, "deadline"),
            (2, "reviews", self.env._("Accounting reviews pending"), overview.pending_review_decision_count, "review"),
            (3, "bank", self.env._("Bank items to review"), overview.unmatched_bank_transaction_count + overview.bank_review_count, "review"),
            (4, "evidence", self.env._("Supporting evidence missing"), overview.missing_vendor_attachment_count + overview.missing_expense_attachment_count, "evidence"),
            (5, "hygiene", self.env._("Accounting hygiene issues"), overview.hygiene_issue_count, "review"),
        ]
        alerts = [
            {"key": key, "label": label, "count": count, "status": status}
            for _rank, key, label, count, status in candidates
            if count
        ][:5]
        return {
            "company": {"id": overview.company_id.id, "name": overview.company_id.display_name},
            "alerts": alerts,
        }

    @api.model
    def get_accounting_alert_action(self, alert_key):
        self._ensure_internal()
        overview = self._accounting_overview()
        if not overview:
            raise AccessError(self.env._("Accounting alerts are not available."))
        methods = {
            "closing": "action_open_latest_closing_controls",
            "declarations": "action_open_declarations",
            "reviews": "action_open_review_decisions",
            "bank": "action_open_bank_review",
            "evidence": "action_open_hygiene_issues",
            "hygiene": "action_open_hygiene_issues",
        }
        if alert_key not in methods:
            raise UserError(self.env._("This accounting alert is not available."))
        return getattr(overview, methods[alert_key])()
