"""Compatibility bridge for the account hierarchy used by OCA reports.

Odoo saas~19.3 replaced ``account.group`` with parent accounts.  The pinned
OCA financial reports still consume the stable prefix-based model, as do
existing USL report definitions and reconstructed history.  Keep that model at
the distribution boundary until OCA adopts the native account hierarchy.
"""

import csv

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.fields import Domain
from odoo.tools import SQL, file_open


FRENCH_GROUPS_CSV = "usl_accounting/data/account_group_fr_compat.csv"


class AccountGroup(models.Model):
    _name = "account.group"
    _description = "Account Group"
    _order = "code_prefix_start"
    _check_company_auto = True
    _check_company_domain = models.check_company_domain_parent_of

    parent_id = fields.Many2one(
        "account.group",
        index=True,
        ondelete="cascade",
        readonly=True,
        check_company=True,
    )
    name = fields.Char(required=True, translate=True)
    code_prefix_start = fields.Char(
        compute="_compute_code_prefix_start",
        readonly=False,
        store=True,
        precompute=True,
    )
    code_prefix_end = fields.Char(
        compute="_compute_code_prefix_end",
        readonly=False,
        store=True,
        precompute=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        readonly=True,
        default=lambda self: self.env.company.root_id,
    )

    _check_length_prefix = models.Constraint(
        "CHECK(char_length(COALESCE(code_prefix_start, '')) = "
        "char_length(COALESCE(code_prefix_end, '')))",
        "The length of the starting and the ending code prefix must be the same",
    )

    @api.model
    def _ensure_french_compatibility_groups(self, companies=None):
        """Install the last official prefix taxonomy for French companies.

        saas~19.3 removed ``account.group`` and does not populate the native
        parent-account hierarchy for the French chart.  Pinned OCA reports
        still require the former prefix taxonomy.  Keep that compatibility
        data deterministic and company-scoped without presenting it as
        migrated source truth.
        """
        companies = companies or self.env["res.company"].sudo().search([])
        companies = companies.sudo().mapped("root_id").filtered(
            lambda company: (
                company.account_fiscal_country_id or company.country_id
            ).code == "FR"
        )
        if not companies:
            return self.browse()

        with file_open(FRENCH_GROUPS_CSV, "r") as source:
            definitions = list(csv.DictReader(source))

        Group = self.sudo().with_context(delay_account_group_sync=True)
        groups = self.browse()
        for company in companies:
            existing = {
                (group.code_prefix_start, group.code_prefix_end): group
                for group in Group.search([("company_id", "=", company.id)])
            }
            for definition in definitions:
                prefix = definition["code_prefix_start"]
                key = (prefix, prefix)
                group = existing.get(key)
                if not group:
                    group = Group.create({
                        "name": definition["name"],
                        "code_prefix_start": prefix,
                        "code_prefix_end": prefix,
                        "company_id": company.id,
                    })
                    french_name = definition.get("name@fr")
                    if french_name:
                        group.update_field_translations(
                            "name",
                            {"fr_FR": french_name},
                        )
                    existing[key] = group
                groups |= group

        groups.with_context(
            delay_account_group_sync=False,
        )._adapt_parent_account_group(companies)
        return groups

    @api.depends("code_prefix_start")
    def _compute_code_prefix_end(self):
        for group in self:
            if not group.code_prefix_end or (
                group.code_prefix_start
                and group.code_prefix_end < group.code_prefix_start
            ):
                group.code_prefix_end = group.code_prefix_start

    @api.depends("code_prefix_end")
    def _compute_code_prefix_start(self):
        for group in self:
            if not group.code_prefix_start or (
                group.code_prefix_end
                and group.code_prefix_start > group.code_prefix_end
            ):
                group.code_prefix_start = group.code_prefix_end

    @api.depends("code_prefix_start", "code_prefix_end", "name")
    def _compute_display_name(self):
        for group in self:
            prefix = group.code_prefix_start and str(group.code_prefix_start)
            if prefix and group.code_prefix_end != group.code_prefix_start:
                prefix += "-" + str(group.code_prefix_end)
            group.display_name = " ".join(filter(None, [prefix, group.name]))

    @api.model
    def _search_display_name(self, operator, value):
        if operator in Domain.NEGATIVE_OPERATORS:
            return NotImplemented
        if operator == "in":
            return [
                "|",
                ("code_prefix_start", "in", [(name or "").split(" ")[0] for name in value]),
                ("name", "in", value),
            ]
        if operator == "ilike" and isinstance(value, str):
            return [
                "|",
                ("code_prefix_start", "=ilike", value + "%"),
                ("name", operator, value),
            ]
        return [("name", operator, value)]

    @api.constrains("code_prefix_start", "code_prefix_end")
    def _constraint_prefix_overlap(self):
        self.flush_model()
        self.env.cr.execute(
            """
            SELECT other.id FROM account_group this
            JOIN account_group other
              ON char_length(other.code_prefix_start) = char_length(this.code_prefix_start)
             AND other.id != this.id
             AND other.company_id = this.company_id
             AND (
                other.code_prefix_start <= this.code_prefix_start
                AND this.code_prefix_start <= other.code_prefix_end
                OR other.code_prefix_start >= this.code_prefix_start
                AND this.code_prefix_end >= other.code_prefix_start
             )
            WHERE this.id IN %(ids)s
            """,
            {"ids": tuple(self.ids)},
        )
        if self.env.cr.fetchall():
            raise ValidationError(
                _("Account Groups with the same granularity can't overlap")
            )

    def _sanitize_vals(self, vals):
        vals = dict(vals)
        if vals.get("code_prefix_start") and "code_prefix_end" in vals and not vals["code_prefix_end"]:
            del vals["code_prefix_end"]
        if vals.get("code_prefix_end") and "code_prefix_start" in vals and not vals["code_prefix_start"]:
            del vals["code_prefix_start"]
        return vals

    @api.constrains("parent_id")
    def _check_parent_not_circular(self):
        if self._has_cycle():
            raise ValidationError(_("You cannot create recursive groups."))

    @api.model_create_multi
    def create(self, vals_list):
        groups = super().create([self._sanitize_vals(vals) for vals in vals_list])
        groups._adapt_parent_account_group()
        return groups

    def write(self, vals):
        result = super().write(self._sanitize_vals(vals))
        if "code_prefix_start" in vals or "code_prefix_end" in vals:
            self._adapt_parent_account_group()
        return result

    def unlink(self):
        for group in self:
            self.search([("parent_id", "=", group.id)]).write(
                {"parent_id": group.parent_id.id}
            )
        return super().unlink()

    def _adapt_parent_account_group(self, company=None):
        if self.env.context.get("delay_account_group_sync"):
            return
        company_ids = company.ids if company else self.company_id.ids
        if not company_ids:
            return
        self.flush_model()
        self.env.cr.execute(
            SQL(
                """
                WITH relation AS MATERIALIZED (
                    SELECT DISTINCT ON (child.id)
                           child.id AS child_id,
                           parent.id AS parent_id
                      FROM account_group parent
                RIGHT JOIN account_group child
                        ON char_length(parent.code_prefix_start) < char_length(child.code_prefix_start)
                       AND parent.code_prefix_start <= LEFT(child.code_prefix_start, char_length(parent.code_prefix_start))
                       AND parent.code_prefix_end >= LEFT(child.code_prefix_end, char_length(parent.code_prefix_end))
                       AND parent.id != child.id
                       AND parent.company_id = child.company_id
                     WHERE child.company_id IN %s
                  ORDER BY child.id, char_length(parent.code_prefix_start) DESC
                )
                UPDATE account_group child
                   SET parent_id = relation.parent_id
                  FROM relation
                 WHERE child.id = relation.child_id
                   AND child.parent_id IS DISTINCT FROM relation.parent_id
             RETURNING child.id
                """,
                tuple(company_ids),
            )
        )
        if self.env.cr.fetchall():
            self.invalidate_model(["parent_id"])


class AccountAccount(models.Model):
    _inherit = "account.account"

    group_id = fields.Many2one(
        "account.group",
        compute="_compute_account_group",
        help="Account prefixes can determine account groups.",
    )

    @api.depends_context("company")
    @api.depends("code")
    def _compute_account_group(self):
        accounts_with_code = self.filtered("code")
        (self - accounts_with_code).group_id = False
        if not accounts_with_code:
            return
        codes = accounts_with_code.mapped("code")
        values = SQL(",".join(["(%s)"] * len(codes)), *codes)
        group_by_code = dict(
            self.env.execute_query(
                SQL(
                    """
                    SELECT DISTINCT ON (account_code.code)
                           account_code.code,
                           agroup.id AS group_id
                      FROM (VALUES %(values)s) AS account_code (code)
                 LEFT JOIN account_group agroup
                        ON agroup.code_prefix_start <= LEFT(account_code.code, char_length(agroup.code_prefix_start))
                       AND agroup.code_prefix_end >= LEFT(account_code.code, char_length(agroup.code_prefix_end))
                       AND agroup.company_id = %(company_id)s
                  ORDER BY account_code.code, char_length(agroup.code_prefix_start) DESC, agroup.id
                    """,
                    values=values,
                    company_id=self.env.company.root_id.id,
                )
            )
        )
        for account in accounts_with_code:
            account.group_id = group_by_code.get(account.code)


class ResCompany(models.Model):
    _inherit = "res.company"

    @api.model_create_multi
    def create(self, vals_list):
        companies = super().create(vals_list)
        self.env["account.group"]._ensure_french_compatibility_groups(companies)
        return companies

    def write(self, vals):
        result = super().write(vals)
        if {"country_id", "account_fiscal_country_id"} & vals.keys():
            self.env["account.group"]._ensure_french_compatibility_groups(self)
        return result
