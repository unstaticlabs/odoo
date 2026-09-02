from lxml import etree

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

EXPENSE_BATCH_ELIGIBLE_STATES = ("draft", "approved", "posted")

CONTEXT_SOURCES = [
    ("product", "Category default"),
    ("batch", "Batch context"),
    ("explicit", "Expense exception"),
    ("inferred", "Suggested"),
    ("legacy", "Existing value"),
]


class HrExpense(models.Model):
    _inherit = "hr.expense"

    expense_batch_id = fields.Many2one(
        "usl.expense.batch",
        string="Expense Batch",
        check_company=True,
        copy=False,
        index=True,
        ondelete="set null",
        tracking=True,
    )
    batch_readiness = fields.Selection(
        selection=[
            ("ready", "Ready to submit"),
            ("incomplete", "Needs information"),
            ("batched", "Already in a batch"),
        ],
        compute="_compute_batch_readiness",
        search="_search_batch_readiness",
        string="Batch readiness",
    )
    batch_incomplete_reason = fields.Char(
        compute="_compute_batch_readiness",
        string="Missing information",
    )
    batch_attachment_status = fields.Selection(
        selection=[
            ("attached", "Attached"),
            ("missing", "Missing"),
            ("not_required", "Not required"),
        ],
        compute="_compute_batch_attachment_status",
        string="Attachment status",
    )
    account_context_source = fields.Selection(
        CONTEXT_SOURCES,
        default="product",
        required=True,
        copy=False,
        tracking=True,
        string="Account source",
    )
    analytic_context_source = fields.Selection(
        CONTEXT_SOURCES,
        default="product",
        required=True,
        copy=False,
        tracking=True,
        string="Analytic source",
    )
    pre_batch_account_id = fields.Many2one(
        "account.account",
        copy=False,
        check_company=True,
    )
    pre_batch_account_context_source = fields.Selection(CONTEXT_SOURCES, copy=False)
    pre_batch_analytic_distribution = fields.Json(copy=False)
    pre_batch_analytic_context_source = fields.Selection(CONTEXT_SOURCES, copy=False)
    batch_applied_account_id = fields.Many2one(
        "account.account",
        copy=False,
        check_company=True,
    )
    batch_applied_analytic_distribution = fields.Json(copy=False)
    batch_account_baseline_captured = fields.Boolean(copy=False)
    batch_analytic_baseline_captured = fields.Boolean(copy=False)
    batch_context_revision = fields.Integer(copy=False)
    batch_context_status = fields.Selection(
        selection=[
            ("inherited", "Uses Batch context"),
            ("exception", "Expense exception"),
            ("stale", "Batch context changed"),
            ("fixed", "Existing accounting preserved"),
        ],
        compute="_compute_batch_context_status",
        string="Context status",
    )
    batch_warning_reason = fields.Char(
        compute="_compute_batch_context_status",
        string="Review warning",
    )
    batch_attention_level = fields.Selection(
        selection=[
            ("info", "Information"),
            ("warning", "Needs attention"),
        ],
        compute="_compute_batch_attention",
        string="Attention",
    )
    batch_attention_message = fields.Char(
        compute="_compute_batch_attention",
        string="Attention details",
    )

    @api.model
    def get_view(self, view_id=None, view_type="form", **options):
        result = super().get_view(view_id, view_type, **options)
        if (
            view_type == "form"
            and self.env.user.has_group("account.group_account_readonly")
            and not self.env.user.has_group("account.group_account_user")
        ):
            arch = etree.fromstring(result["arch"])
            arch.set("create", "false")
            arch.set("edit", "false")
            arch.set("delete", "false")
            for control in arch.xpath("//header/button | //header/widget"):
                control.getparent().remove(control)
            result["arch"] = etree.tostring(arch, encoding="unicode")
        return result

    def _native_product_account(self):
        self.ensure_one()
        expense = self.with_company(self.company_id)
        if not expense.product_id:
            return expense.company_id.expense_account_id
        return expense.product_id.product_tmpl_id._get_product_accounts()["expense"]

    def _native_analytic_distribution(self):
        self.ensure_one()
        return self.env["account.analytic.distribution.model"]._get_distribution({
            "product_id": self.product_id.id,
            "product_categ_id": self.product_id.categ_id.id,
            "partner_id": self.employee_id.work_contact_id.id,
            "partner_category_id": self.employee_id.work_contact_id.category_id.ids,
            "account_prefix": self.account_id.code,
            "company_id": self.company_id.id,
        }) or {}

    @api.model
    def _canonical_analytic_distribution(self, distribution):
        """Return a stable representation of a native analytic distribution."""
        normalized = {}
        for account_keys, percentage in (distribution or {}).items():
            key = tuple(
                sorted(
                    int(account_id)
                    for account_id in str(account_keys).split(",")
                    if account_id.isdigit()
                ),
            )
            if key:
                normalized[key] = normalized.get(key, 0.0) + float(percentage)
        return tuple(
            sorted(
                (key, round(percentage, 6))
                for key, percentage in normalized.items()
                if round(percentage, 6)
            ),
        )

    @api.model
    def _analytic_distributions_equal(self, left, right):
        return self._canonical_analytic_distribution(
            left,
        ) == self._canonical_analytic_distribution(right)

    @api.model
    def _analytic_distribution_label(self, distribution):
        account_ids = {
            account_id
            for account_keys, _percentage in self._canonical_analytic_distribution(
                distribution,
            )
            for account_id in account_keys
        }
        if not account_ids:
            return _("none")
        # Submitters can review an expense without direct read access to the
        # analytic-account model. The distribution is already visible on the
        # line; sudo is limited to resolving those referenced IDs for a label.
        accounts = (
            self.env["account.analytic.account"].sudo().browse(account_ids).exists()
        )
        by_plan = {}
        for account in accounts.sorted(
            key=lambda item: (item.plan_id.display_name, item.display_name),
        ):
            by_plan.setdefault(account.plan_id.display_name, []).append(
                account.display_name,
            )
        return " · ".join(
            f"{plan}: {', '.join(names)}" for plan, names in by_plan.items()
        )

    def _is_batch_receipt_required(self):
        self.ensure_one()
        receipt_policy = self.product_id._fields.get("rebuild_receipt_required")
        if receipt_policy:
            return bool(self.product_id[receipt_policy.name])
        return True

    def _get_batch_incomplete_reasons(self):
        self.ensure_one()
        reasons = []
        if not self.name:
            reasons.append(_("description"))
        if not self.product_id:
            reasons.append(_("category"))
        if (
            self.company_currency_id.is_zero(self.total_amount)
            or self.currency_id.is_zero(self.total_amount_currency)
        ):
            reasons.append(_("non-zero amount"))
        if self.product_id and self.batch_attachment_status == "missing":
            reasons.append(_("receipt"))
        if not self.account_id:
            reasons.append(_("account"))
        return reasons

    @api.depends(
        "expense_batch_id",
        "expense_batch_id.context_revision",
        "expense_batch_id.context_date_from",
        "expense_batch_id.context_date_to",
        "expense_batch_id.account_override_id",
        "expense_batch_id.analytic_distribution",
        "account_context_source",
        "analytic_context_source",
        "batch_context_revision",
        "date",
        "same_receipt_expense_ids",
        "duplicate_expense_ids",
        "state",
    )
    def _compute_batch_context_status(self):
        for expense in self:
            batch = expense.expense_batch_id
            if not batch:
                expense.batch_context_status = False
                expense.batch_warning_reason = False
                continue
            if expense.state != "draft":
                expense.batch_context_status = "fixed"
            elif (
                (
                    batch.account_override_id
                    and expense.account_context_source == "explicit"
                    and expense.account_id != batch.account_override_id
                )
                or (
                    batch.analytic_distribution
                    and expense.analytic_context_source == "explicit"
                    and not expense._analytic_distributions_equal(
                        expense.analytic_distribution,
                        batch.analytic_distribution,
                    )
                )
            ):
                expense.batch_context_status = "exception"
            elif (
                expense.batch_context_revision
                and expense.batch_context_revision != batch.context_revision
                and (
                    expense.account_context_source == "batch"
                    or expense.analytic_context_source == "batch"
                )
            ):
                expense.batch_context_status = "stale"
            else:
                expense.batch_context_status = "inherited"

            warnings = []
            # Native duplicate receipt computation removes ``expense.id`` from
            # a set of stored integer IDs.  During a One2many onchange the row
            # has a NewId, so ask the stored origin instead of triggering that
            # unsafe native computation on the transient row.
            stored_expense = expense._origin
            if stored_expense and stored_expense.id:
                if stored_expense.same_receipt_expense_ids:
                    warnings.append(_("same receipt already used"))
                elif stored_expense.duplicate_expense_ids:
                    warnings.append(_("possible duplicate"))
            if (
                batch.context_date_from
                and expense.date
                and expense.date < batch.context_date_from
            ) or (
                batch.context_date_to
                and expense.date
                and expense.date > batch.context_date_to
            ):
                warnings.append(_("outside the Batch dates"))
            expense.batch_warning_reason = ", ".join(warnings) or False

    @api.depends(
        "batch_context_status",
        "batch_warning_reason",
        "batch_incomplete_reason",
        "account_id",
        "analytic_distribution",
        "expense_batch_id.account_override_id",
        "expense_batch_id.analytic_distribution",
    )
    def _compute_batch_attention(self):
        for expense in self:
            batch = expense.expense_batch_id
            if not batch:
                expense.batch_attention_level = False
                expense.batch_attention_message = False
                continue

            details = []
            level = False
            if expense.batch_context_status == "exception":
                level = "warning"
                if (
                    batch.account_override_id
                    and expense.account_context_source == "explicit"
                    and expense.account_id != batch.account_override_id
                ):
                    details.append(
                        _(
                            "Expense account %(expense)s differs from Batch account %(batch)s.",
                            expense=expense.account_id.display_name or _("none"),
                            batch=batch.account_override_id.display_name,
                        ),
                    )
                if (
                    batch.analytic_distribution
                    and expense.analytic_context_source == "explicit"
                    and not expense._analytic_distributions_equal(
                        expense.analytic_distribution,
                        batch.analytic_distribution,
                    )
                ):
                    details.append(
                        _(
                            "Expense analytics %(expense)s differ from Batch analytics %(batch)s.",
                            expense=expense._analytic_distribution_label(
                                expense.analytic_distribution,
                            ),
                            batch=expense._analytic_distribution_label(
                                batch.analytic_distribution,
                            ),
                        ),
                    )
            elif expense.batch_context_status == "stale":
                level = "warning"
                details.append(
                    _("Shared context changed after it was applied to this expense."),
                )
            elif expense.batch_context_status == "fixed":
                level = "info"
                details.append(
                    _("Accounting is preserved because this expense is no longer a draft."),
                )

            if expense.batch_incomplete_reason:
                level = "warning"
                details.append(expense.batch_incomplete_reason)
            if expense.batch_warning_reason:
                level = "warning"
                details.append(expense.batch_warning_reason)

            expense.batch_attention_level = level
            expense.batch_attention_message = " ".join(details) or False

    @api.depends("message_main_attachment_id", "nb_attachment", "product_id")
    def _compute_batch_attachment_status(self):
        for expense in self:
            if expense.message_main_attachment_id or expense.nb_attachment:
                expense.batch_attachment_status = "attached"
            elif expense.product_id and not expense._is_batch_receipt_required():
                expense.batch_attachment_status = "not_required"
            else:
                expense.batch_attachment_status = "missing"

    @api.depends(
        "expense_batch_id",
        "state",
        "name",
        "product_id",
        "total_amount",
        "total_amount_currency",
        "message_main_attachment_id",
    )
    def _compute_batch_readiness(self):
        for expense in self:
            reasons = (
                expense._get_batch_incomplete_reasons()
                if (
                    expense.state in EXPENSE_BATCH_ELIGIBLE_STATES
                    or expense.expense_batch_id
                )
                else []
            )
            if expense.expense_batch_id:
                expense.batch_readiness = "batched"
            elif expense.state not in EXPENSE_BATCH_ELIGIBLE_STATES:
                expense.batch_readiness = False
            else:
                expense.batch_readiness = "incomplete" if reasons else "ready"
            expense.batch_incomplete_reason = (
                _("Missing: %s", ", ".join(reasons)) if reasons else False
            )

    @api.model
    def _search_batch_readiness(self, operator, value):
        if operator not in ("=", "!=") or value not in (
            "ready",
            "incomplete",
            "batched",
        ):
            raise NotImplementedError
        if value == "batched":
            domain = [("expense_batch_id", "!=", False)]
        else:
            candidate_ids = self.search([
                ("state", "=", "draft"),
                ("expense_batch_id", "=", False),
            ]).filtered(
                lambda expense: expense.batch_readiness == value,
            ).ids
            domain = [("id", "in", candidate_ids)]
        if operator == "!=":
            return ["!"] + domain
        return domain

    @api.constrains("expense_batch_id", "employee_id", "company_id")
    def _check_expense_batch_compatibility(self):
        for expense in self.filtered("expense_batch_id"):
            if expense.state not in EXPENSE_BATCH_ELIGIBLE_STATES:
                raise ValidationError(
                    _(
                        "Only unbatched draft, approved, or posted expenses "
                        "can be added to an expense batch.",
                    ),
                )
            if expense.company_id != expense.expense_batch_id.company_id:
                raise ValidationError(
                    _("The expense and its batch must belong to the same company."),
                )
            if expense.employee_id != expense.expense_batch_id.employee_id:
                raise ValidationError(
                    _("The expense and its batch must belong to the same employee."),
                )

    @api.model_create_multi
    def create(self, values_list):
        records = super().create(values_list)
        for expense, incoming_values in zip(records, values_list):
            provenance = {}
            if (
                "account_id" in incoming_values
                and "account_context_source" not in incoming_values
            ):
                provenance["account_context_source"] = (
                    "product"
                    if expense.account_id == expense._native_product_account()
                    else "explicit"
                )
            if (
                "analytic_distribution" in incoming_values
                and "analytic_context_source" not in incoming_values
            ):
                provenance["analytic_context_source"] = (
                    "product"
                    if expense._analytic_distributions_equal(
                        expense.analytic_distribution,
                        expense._native_analytic_distribution(),
                    )
                    else "explicit"
                )
            if provenance:
                expense.with_context(usl_batch_context_internal=True).write(provenance)
        return records

    def write(self, values):
        if len(self) > 1 and values.get("expense_batch_id") is False:
            for expense in self:
                expense.write(values)
            return True
        new_batch_id = values.get("expense_batch_id")
        if new_batch_id and any(
            expense.expense_batch_id
            and expense.expense_batch_id.id != new_batch_id
            for expense in self
        ):
            raise ValidationError(
                _(
                    "Remove an expense from its current batch before adding "
                    "it to another one.",
                ),
            )
        values = dict(values)
        internal = self.env.context.get("usl_batch_context_internal")
        if not internal:
            if "account_id" in values:
                values.setdefault("account_context_source", "explicit")
            if "analytic_distribution" in values:
                values.setdefault("analytic_context_source", "explicit")

        removing_batch = "expense_batch_id" in values and not values["expense_batch_id"]
        if removing_batch and len(self) == 1 and self.expense_batch_id:
            if (
                self.account_context_source == "batch"
                and self.account_id == self.batch_applied_account_id
                and self.batch_account_baseline_captured
            ):
                values.update({
                    "account_id": self.pre_batch_account_id.id,
                    "account_context_source": (
                        self.pre_batch_account_context_source or "product"
                    ),
                })
            if (
                self.analytic_context_source == "batch"
                and self._analytic_distributions_equal(
                    self.analytic_distribution,
                    self.batch_applied_analytic_distribution,
                )
                and self.batch_analytic_baseline_captured
            ):
                values.update({
                    "analytic_distribution": self.pre_batch_analytic_distribution or {},
                    "analytic_context_source": (
                        self.pre_batch_analytic_context_source or "product"
                    ),
                })
            values.update({
                "pre_batch_account_id": False,
                "pre_batch_account_context_source": False,
                "pre_batch_analytic_distribution": False,
                "pre_batch_analytic_context_source": False,
                "batch_applied_account_id": False,
                "batch_applied_analytic_distribution": False,
                "batch_account_baseline_captured": False,
                "batch_analytic_baseline_captured": False,
                "batch_context_revision": 0,
            })

        result = super().write(values)
        if new_batch_id:
            batch = self.env["usl.expense.batch"].browse(new_batch_id)
            batch.apply_context(expense_ids=self.ids)
            batch._link_existing_moves()
        return result

    def _compute_account_id(self):
        protected = [
            (expense, expense.account_id)
            for expense in self
            if expense.account_context_source in ("explicit", "batch")
        ]
        super()._compute_account_id()
        for expense, previous_account in protected:
            if expense.account_context_source == "explicit":
                expense.account_id = previous_account
            elif expense.batch_applied_account_id:
                expense.account_id = expense.batch_applied_account_id

    def _compute_analytic_distribution(self):
        protected = [
            (expense, expense.analytic_distribution)
            for expense in self
            if expense.analytic_context_source in ("explicit", "batch")
        ]
        super()._compute_analytic_distribution()
        for expense, previous_distribution in protected:
            if expense.analytic_context_source == "explicit":
                expense.analytic_distribution = previous_distribution
            elif expense.batch_applied_analytic_distribution:
                expense.analytic_distribution = (
                    expense.batch_applied_analytic_distribution
                )

    @api.model
    def get_expense_batch_candidates(self, expense_ids):
        expenses = self.browse(expense_ids).exists()
        if not expenses:
            return []
        expenses.check_access("read")
        if len(expenses.company_id) != 1 or len(expenses.employee_id) != 1:
            raise ValidationError(
                _("Select expenses for one employee and one company."),
            )
        dates = expenses.mapped("date")
        date_from = min(dates) if dates else False
        date_to = max(dates) if dates else False
        selected_analytics = self._get_analytic_account_ids_from_distributions(
            expenses.mapped("analytic_distribution"),
        )
        candidates = self.env["usl.expense.batch"].search([
            ("employee_id", "=", expenses.employee_id.id),
            ("company_id", "=", expenses.company_id.id),
            ("active", "=", True),
        ])
        result = []
        for batch in candidates:
            batch_analytics = self._get_analytic_account_ids_from_distributions(
                batch.analytic_distribution,
            )
            analytic_overlap = len(selected_analytics.intersection(batch_analytics))
            date_overlap = bool(
                date_from
                and date_to
                and batch.context_date_from
                and batch.context_date_to
                and date_from <= batch.context_date_to
                and date_to >= batch.context_date_from,
            )
            score = analytic_overlap * 100 + (25 if date_overlap else 0)
            result.append({
                "id": batch.id,
                "name": batch.display_name,
                "purpose": batch.purpose,
                "date_from": batch.context_date_from or batch.date_from,
                "date_to": batch.context_date_to or batch.date_to,
                "expense_count": batch.expense_count,
                "total": batch.total_amount,
                "score": score,
                "date_overlap": date_overlap,
                "analytic_overlap": analytic_overlap,
            })
        return sorted(result, key=lambda item: (-item["score"], item["name"]))

    @api.model
    def action_open_expense_batch_wizard(self, expense_ids=None):
        expenses = self.browse(expense_ids or []).exists()
        if not expenses:
            raise UserError(_("Select at least one eligible expense."))
        invalid = expenses.filtered(
            lambda expense: (
                expense.state not in EXPENSE_BATCH_ELIGIBLE_STATES
                or expense.expense_batch_id
            ),
        )
        if invalid:
            raise UserError(
                _(
                    "Only unbatched draft, approved, or posted expenses "
                    "can be added to a new batch.",
                ),
            )
        if len(expenses.company_id) != 1 or len(expenses.employee_id) != 1:
            raise UserError(
                _(
                    "Create a separate expense batch for each employee and company.",
                ),
            )
        wizard = self.env["usl.expense.batch.create.wizard"].create({
            "expense_ids": [Command.set(expenses.ids)],
        })
        return {
            "name": _("Create expense batch"),
            "type": "ir.actions.act_window",
            "res_model": "usl.expense.batch.create.wizard",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": wizard.id,
            "target": "new",
        }

    def action_return_from_batch(self):
        for expense in self:
            batch = expense.expense_batch_id
            if not batch:
                continue
            if expense.state == "draft":
                expense.expense_batch_id = False
                continue
            if expense.state not in ("submitted", "approved"):
                raise UserError(
                    _("Only submitted or approved expenses can be returned."),
                )
            expense._check_can_reset_approval()
            batch.message_post(
                body=_(
                    "%(expense)s was returned for individual correction by %(user)s.",
                    expense=expense.display_name,
                    user=self.env.user.display_name,
                ),
            )
            expense.expense_batch_id = False
            expense._do_reset_approval()
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_open_expense_batch(self):
        self.ensure_one()
        if not self.expense_batch_id:
            return False
        return {
            "name": self.expense_batch_id.name,
            "type": "ir.actions.act_window",
            "res_model": "usl.expense.batch",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": self.expense_batch_id.id,
        }

    @staticmethod
    def _expense_ids_from_move_vals(move_vals):
        expense_ids = []
        for command in move_vals.get("expense_ids", []):
            if command[0] == Command.SET:
                expense_ids.extend(command[2])
            elif command[0] == Command.LINK:
                expense_ids.append(command[1])
        return expense_ids

    def _prepare_receipts_vals(self):
        values_list = super()._prepare_receipts_vals()
        for values in values_list:
            expenses = self.browse(self._expense_ids_from_move_vals(values))
            batches = expenses.expense_batch_id
            if len(batches) == 1 and len(expenses) == len(
                expenses.filtered(lambda expense: expense.expense_batch_id == batches),
            ):
                values.update({
                    "expense_batch_id": batches.id,
                    "ref": batches.name,
                })
        return values_list

    def _prepare_payments_vals(self):
        move_values, payment_values = super()._prepare_payments_vals()
        if self.expense_batch_id:
            move_values.update({
                "expense_batch_id": self.expense_batch_id.id,
                "ref": self.expense_batch_id.name,
            })
            payment_values["memo"] = self.expense_batch_id.name
        return move_values, payment_values
