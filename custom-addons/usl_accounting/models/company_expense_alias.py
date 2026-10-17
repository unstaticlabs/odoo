"""Optional company inboxes use native aliases and native expense creation."""

from dataclasses import dataclass

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import email_normalize
from odoo.addons.mail.tools.alias_error import AliasError


_ROUTE_KEY = "_usl_company_expense_route"


@dataclass(frozen=True)
class _CompanyExpenseRoute:
    company_id: int
    alias_id: int


class ResCompany(models.Model):
    _inherit = "res.company"

    expense_alias_id = fields.Many2one("mail.alias", copy=False, ondelete="restrict", readonly=True)
    _expense_alias_unique = models.Constraint(
        "UNIQUE(expense_alias_id)",
        "A company expense inbox can only belong to one company.",
    )
    expense_alias_name = fields.Char(
        string="Company expense alias",
        compute="_compute_expense_alias_name",
        inverse="_inverse_expense_alias_name",
    )

    @api.depends("expense_alias_id.alias_name")
    def _compute_expense_alias_name(self):
        for company in self:
            company.expense_alias_name = company.expense_alias_id.alias_name

    def _inverse_expense_alias_name(self):
        for company in self:
            name = company.expense_alias_name
            if company.expense_alias_id:
                company.expense_alias_id.write({"alias_name": name})
            elif name:
                company.expense_alias_id = self.env["mail.alias"].create({
                    "alias_name": name,
                    "alias_domain_id": company.alias_domain_id.id,
                    "alias_model_id": self.env["ir.model"]._get_id("hr.expense"),
                    "alias_parent_model_id": self.env["ir.model"]._get_id("res.company"),
                    "alias_parent_thread_id": company.id,
                    "alias_contact": "employees",
                    "alias_defaults": repr({"company_id": company.id}),
                })

    def write(self, values):
        detached_aliases = self.env["mail.alias"]
        if "expense_alias_id" in values:
            # Configuring the pointer must retain the native alias ACL too.
            aliases = self.expense_alias_id | self.env["mail.alias"].browse(values["expense_alias_id"] or [])
            aliases.check_access("write")
            detached_aliases = self.expense_alias_id - self.env["mail.alias"].browse(values["expense_alias_id"] or [])
        result = super().write(values)
        if detached_aliases:
            detached_aliases.write({"alias_name": False})
        if "alias_domain_id" in values:
            for company in self.filtered("expense_alias_id"):
                company.expense_alias_id.alias_domain_id = company.alias_domain_id
        return result

    @api.constrains("expense_alias_id")
    def _check_expense_alias_owner(self):
        for company in self.filtered("expense_alias_id"):
            alias = company.expense_alias_id
            if (alias.alias_model_id.model != "hr.expense"
                    or alias.alias_parent_model_id.model != "res.company"
                    or alias.alias_parent_thread_id != company.id):
                raise ValidationError(_(
                    "The company expense inbox must use an expense alias owned by this company. "
                    "Configure its address in Expenses settings; keep the global alias unchanged.",
                ))

    def unlink(self):
        aliases = self.env["mail.alias"].sudo().search([
            ("alias_parent_model_id.model", "=", "res.company"),
            ("alias_parent_thread_id", "in", self.ids),
            ("alias_model_id.model", "=", "hr.expense"),
        ])
        result = super().unlink()
        # Match native optional-alias lifecycle cleanup after ordinary company
        # deletion authority has succeeded. These aliases belonged only to self.
        aliases.sudo().unlink()
        return result

    def _alias_get_error(self, message, message_dict, alias):
        error = super()._alias_get_error(message, message_dict, alias)
        if error or len(self) != 1 or alias != self.expense_alias_id:
            return error
        expense = self.env["hr.expense"].with_context(**{
            _ROUTE_KEY: _CompanyExpenseRoute(self.id, alias.id),
        })
        try:
            employee = expense._get_employee_from_email(
                email_normalize(message_dict.get("email_from")),
            )
        except UserError as error:
            return AliasError("company_expense_employee", str(error))
        if not employee:
            return AliasError("company_expense_employee", _(
                "No employee linked to this sender exists in %(company)s. "
                "Ask your HR administrator to check the existing employee's company "
                "and email address, or use the global expense address.",
                company=self.display_name,
            ))
        return False


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _message_route_process(self, message, message_dict, routes):
        routes = routes or []
        alias_ids = [alias.id for model, thread, values, user, alias in routes
                     if model == "hr.expense" and not thread and alias]
        # Native mail routing already elevates each alias route. This read-only
        # lookup identifies configured inboxes; it grants no new write authority.
        companies = self.env["res.company"].sudo().with_context(active_test=False).search([
            ("expense_alias_id", "in", alias_ids),
        ]) if alias_ids else self.env["res.company"]
        by_alias = {company.expense_alias_id.id: company for company in companies}
        scoped_routes = []
        for model, thread, values, user, alias in routes:
            if model == "hr.expense" and not thread and alias and alias.id in by_alias:
                company = by_alias[alias.id]
                values = dict(values or {}, **{
                    _ROUTE_KEY: _CompanyExpenseRoute(company.id, alias.id),
                })
            scoped_routes.append((model, thread, values, user, alias))
        return super()._message_route_process(message, message_dict, scoped_routes)


class HrExpense(models.Model):
    _inherit = "hr.expense"

    def _usl_select_sender_employee_candidates(self, employees):
        route = self.env.context.get(_ROUTE_KEY)
        if not isinstance(route, _CompanyExpenseRoute):
            return employees
        employees = employees.filtered(lambda employee: employee.company_id.id == route.company_id)
        if len(employees) > 1:
            raise UserError(_(
                "More than one employee in %(company)s is linked to this sender. "
                "Ask your HR administrator to check the existing employee records, "
                "or use the global expense address.",
                company=self.env["res.company"].browse(route.company_id).display_name,
            ))
        return employees

    @api.model
    def _get_employee_from_email(self, email_address):
        route = self.env.context.get(_ROUTE_KEY)
        if not isinstance(route, _CompanyExpenseRoute) or not email_address:
            return super()._get_employee_from_email(email_address)
        verified_sender = getattr(self.env["res.partner"], "_usl_verified_sender_partner", None)
        if verified_sender and verified_sender(email_address):
            # PocketID owns verified personal-address identity. Its unchanged
            # resolver calls the cooperative candidate selector above.
            return super()._get_employee_from_email(email_address)
        # Keep the native user-linked / employee-only precedence, within the
        # actual destination company. Native gateway execution supplies sudo.
        employees = self.env["hr.employee"].search([
            ("company_id", "=", route.company_id),
            ("user_id", "!=", False),
            "|", ("work_email", "ilike", email_address),
            ("user_id.email", "ilike", email_address),
        ])
        if not employees:
            employees = self.env["hr.employee"].search([
                ("company_id", "=", route.company_id),
                ("user_id", "=", False),
                ("work_email", "ilike", email_address),
            ])
        return self._usl_select_sender_employee_candidates(employees)

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        values = dict(custom_values or {})
        route = values.pop(_ROUTE_KEY, None)
        normal = self.with_context(**{_ROUTE_KEY: False})
        if not isinstance(route, _CompanyExpenseRoute):
            return super(HrExpense, normal).message_new(msg_dict, values)
        email = email_normalize(msg_dict.get("email_from"))
        sender_employee = normal._get_employee_from_email(email)
        company = self.env["res.company"].browse(route.company_id)
        scoped = self.with_company(company).with_context(**{_ROUTE_KEY: route})
        if not scoped._get_employee_from_email(email):
            raise UserError(_(
                "No employee linked to this sender exists in %(company)s. "
                "Ask your HR administrator to check the existing employee record, "
                "or use the global expense address.", company=company.display_name,
            ))
        expense = super(HrExpense, scoped).message_new(msg_dict, values)
        if sender_employee and sender_employee.company_id != company:
            if len(sender_employee.company_id) == 1:
                note = _(
                    "Created through the expense inbox for %(destination)s. "
                    "The sender's default employee company is %(sender)s; "
                    "the addressed company's existing employee profile was used.",
                    destination=company.display_name,
                    sender=sender_employee.company_id.display_name,
                )
            else:
                note = _(
                    "The sender has employee profiles in several companies. "
                    "The expense inbox selected the existing employee profile in %(company)s.",
                    company=company.display_name,
                )
            expense.message_post(body=note, subtype_xmlid="mail.mt_note")
        return expense


class MailAlias(models.Model):
    _inherit = "mail.alias"

    @api.constrains("alias_model_id", "alias_parent_model_id", "alias_parent_thread_id")
    def _check_company_expense_alias_owner(self):
        # Validate only aliases explicitly configured as company expense inboxes.
        self.env["res.company"].sudo().with_context(active_test=False).search([
            ("expense_alias_id", "in", self.ids),
        ])._check_expense_alias_owner()


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    company_expense_alias_name = fields.Char(
        related="company_id.expense_alias_name", readonly=False,
    )
    company_expense_alias_domain = fields.Char(related="company_id.alias_domain_id.name")
