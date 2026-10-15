"""Serialize rule authoring without acquiring journal-write authority.

A tuple update is deliberate: under REPEATABLE READ a concurrent author must
restart the entire native transaction, rather than validate an old rule graph.
The private row has no independent business meaning or public capability.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import SQL

_COORDINATION = object()


class ReconciliationRuleCoordination(models.Model):
    _name = "rebuild.reconcile.rule.coordination"
    _description = "Private reconciliation rule authoring coordination"
    _log_access = False

    company_id = fields.Many2one("res.company", required=True, ondelete="cascade")
    _company_unique = models.Constraint("UNIQUE(company_id)", "One coordination row per company.")


class AccountReconcileModel(models.Model):
    _inherit = "account.reconcile.model"

    def _rebuild_authoring_access(self, operation, values=None):
        self.check_access(operation)
        if operation == "create":
            for vals in values:
                candidate = self.new(vals)
                domain = self._access_domain("create")
                if not candidate.filtered_domain(domain):
                    raise self._make_access_error_message("create", domain)

    def _rebuild_authoring_companies(self, values=None):
        companies = self.company_id
        for vals in values or []:
            if "company_id" in vals or not self:
                companies |= self.env["res.company"].browse(
                    (vals.get("company_id") or self.env.company.id) if self else self.new(vals).company_id.id,
                )
            for command in vals.get("line_ids", []):
                if command[0] in (1, 2, 3, 4):
                    line = self.env["account.reconcile.model.line"].browse(command[1])
                    line.check_access("read")
                    companies |= line.company_id
                    if command[0] == 1 and command[2].get("model_id"):
                        parent = self.browse(command[2]["model_id"])
                        parent.check_access("read")
                        companies |= parent.company_id
                elif command[0] == 6:
                    lines = self.env["account.reconcile.model.line"].browse(command[2])
                    lines.check_access("read")
                    companies |= lines.company_id
        companies.check_access("read")
        return companies

    def _rebuild_coordinate_companies(self, companies):
        held = self.env.context.get("_rebuild_rule_coordination")
        if isinstance(held, tuple) and len(held) == 2 and held[0] is _COORDINATION:
            if not set(companies.ids) <= held[1]:
                raise AccessError(self.env._("The complete rule company scope must be resolved before editing."))
            return self
        for company_id in sorted(companies.ids):
            self.env.cr.execute(SQL(
                "INSERT INTO rebuild_reconcile_rule_coordination (company_id) VALUES (%s) "
                "ON CONFLICT (company_id) DO UPDATE SET company_id = EXCLUDED.company_id RETURNING id",
                company_id,
            ))
        return self.with_context(_rebuild_rule_coordination=(_COORDINATION, frozenset(companies.ids)))

    @api.model_create_multi
    def create(self, vals_list):
        self._rebuild_authoring_access("create", vals_list)
        companies = self._rebuild_authoring_companies(vals_list)
        scoped = self._rebuild_coordinate_companies(companies)
        records = super(AccountReconcileModel, scoped).create(vals_list)
        records._rebuild_validate_rule_graph(companies)
        # The held-company token belongs to this call, not later operations on
        # the returned recordset (which may legitimately involve another company).
        return records.with_env(self.env)

    def write(self, vals):
        self._rebuild_authoring_access("write")
        companies = self._rebuild_authoring_companies([vals])
        scoped = self._rebuild_coordinate_companies(companies)
        result = super(AccountReconcileModel, scoped).write(vals)
        scoped._rebuild_validate_rule_graph(companies)
        return result

    def unlink(self):
        self._rebuild_authoring_access("unlink")
        companies = self.company_id
        scoped = self._rebuild_coordinate_companies(companies)
        return super(AccountReconcileModel, scoped).unlink()

    def _rebuild_validate_rule_graph(self, companies):
        # The proof extension validates generated rules only. Native handwritten
        # rules remain usable without historical evidence or manager approval.
        return True


class AccountReconcileModelLine(models.Model):
    _inherit = "account.reconcile.model.line"

    def _rebuild_line_authoring_scope(self, operation, vals_list=None):
        self.check_access(operation)
        parents = self.model_id
        for vals in vals_list or []:
            parent_id = vals.get("model_id") or self.env.context.get("default_model_id")
            if parent_id:
                parents |= self.env["account.reconcile.model"].browse(parent_id)
            if operation == "create":
                candidate = self.new(vals)
                domain = self._access_domain("create")
                if not candidate.filtered_domain(domain):
                    raise self._make_access_error_message("create", domain)
        parents.check_access("read")
        generated = parents.filtered("rebuild_generated_proof")
        if generated:
            generated._rebuild_check_generated_actor()
        scoped = parents._rebuild_coordinate_companies(parents.company_id)
        return self.with_env(scoped.env), parents.with_env(scoped.env)

    @api.model_create_multi
    def create(self, vals_list):
        scoped, parents = self._rebuild_line_authoring_scope("create", vals_list)
        records = super(AccountReconcileModelLine, scoped).create(vals_list)
        parents._rebuild_invalidate_pending_line_proof()
        parents._rebuild_validate_rule_graph(parents.company_id)
        return records.with_env(self.env)

    def write(self, vals):
        scoped, parents = self._rebuild_line_authoring_scope("write", [vals])
        result = super(AccountReconcileModelLine, scoped).write(vals)
        parents._rebuild_invalidate_pending_line_proof()
        parents._rebuild_validate_rule_graph(parents.company_id)
        return result

    def unlink(self):
        scoped, parents = self._rebuild_line_authoring_scope("unlink")
        result = super(AccountReconcileModelLine, scoped).unlink()
        parents._rebuild_invalidate_pending_line_proof()
        parents._rebuild_validate_rule_graph(parents.company_id)
        return result
