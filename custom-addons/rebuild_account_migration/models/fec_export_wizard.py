import csv
import io
import re

from odoo import Command, api, fields, models
from odoo.exceptions import UserError


class L10nFrFecExportWizard(models.TransientModel):
    _inherit = "l10n_fr.fec.export.wizard"

    _fec_account_number_pattern = re.compile(r"^\d{3}")

    rebuild_can_generate_official_fec = fields.Boolean(
        compute="_compute_rebuild_can_generate_official_fec",
    )

    def _rebuild_can_generate_official_fec(self):
        return self.env.user.has_group("account.group_account_manager")

    @api.depends_context("uid")
    def _compute_rebuild_can_generate_official_fec(self):
        allowed = self._rebuild_can_generate_official_fec()
        for wizard in self:
            wizard.rebuild_can_generate_official_fec = allowed

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        fiscal_dates = self.env.company.compute_fiscalyear_dates(
            fields.Date.context_today(self),
        )
        fiscal_start = fiscal_dates["date_from"]
        fiscal_end = fiscal_dates["date_to"]
        if "date_from" in fields_list and not values.get("date_from"):
            values["date_from"] = fiscal_start
        if "date_to" in fields_list and not values.get("date_to"):
            values["date_to"] = fiscal_end
        if not self._rebuild_can_generate_official_fec():
            values["test_file"] = True
            values["export_type"] = "official"
        return values

    @api.model_create_multi
    def create(self, vals_list):
        if not self._rebuild_can_generate_official_fec():
            for values in vals_list:
                values["test_file"] = True
                values["export_type"] = "official"
                values["excluded_journal_ids"] = [Command.clear()]
        else:
            for values in vals_list:
                if values.get("test_file") is False:
                    values["export_type"] = "official"
                    values["excluded_journal_ids"] = [Command.clear()]
        return super().create(vals_list)

    def write(self, values):
        if not self._rebuild_can_generate_official_fec():
            if values.get("test_file") is False:
                raise UserError(
                    self.env._(
                        "Only Accounting Managers can generate a final FEC "
                        "that may update lock dates.",
                    ),
                )
            values = dict(values)
            values["test_file"] = True
            values["export_type"] = "official"
            values["excluded_journal_ids"] = [Command.clear()]
        elif values.get("test_file") is False:
            values = dict(values)
            values["export_type"] = "official"
            values["excluded_journal_ids"] = [Command.clear()]
        return super().write(values)

    def generate_fec(self):
        for wizard in self:
            if (
                not wizard._rebuild_can_generate_official_fec()
                and (
                    not wizard.test_file
                    or wizard.export_type != "official"
                    or wizard.excluded_journal_ids
                )
            ):
                raise UserError(
                    self.env._(
                        "Accountant reviewers and finance operators must "
                        "generate a complete posted-entries FEC in test mode.",
                    ),
                )
            if (
                not wizard.test_file
                and (
                    wizard.export_type != "official"
                    or wizard.excluded_journal_ids
                )
            ):
                raise UserError(
                    self.env._(
                        "An official FEC must include all posted journals.",
                    ),
                )
        result = super().generate_fec()
        result["file_name"] = result["file_name"].removesuffix(".txt")
        return result

    def _get_fec_stream(self):
        """Repair Odoo's unscoped retained-earnings fallback, then validate."""
        self.ensure_one()
        company = self.env.company
        retained_earnings_account = (
            self._rebuild_company_retained_earnings_account()
        )
        retained_earnings_values = (
            (
                retained_earnings_account.code,
                re.sub(
                    r"[\t\r\n]",
                    " ",
                    retained_earnings_account.name.replace("|", "/"),
                ),
            )
            if retained_earnings_account
            else False
        )
        messages = {
            "malformed": self.env._(
                "The FEC generator emitted a malformed row at line %(line)s.",
            ),
            "invalid": self.env._(
                "The FEC account number at line %(line)s is invalid. "
                "Correct the account configuration before exporting.",
            ),
            "missing_retained_earnings": self.env._(
                "No valid retained-earnings account is configured for "
                "company %(company)s.",
            ) % {"company": company.display_name},
        }
        # ``super()`` opens independent cursors while the file is streamed.
        # Resolve all values used by our wrapper before the HTTP request cursor
        # closes, then keep the iterator database-independent.
        source_stream = super()._get_fec_stream()
        return self._rebuild_validated_fec_stream(
            source_stream,
            retained_earnings_values,
            self.date_from.year,
            messages,
        )

    def _rebuild_validated_fec_stream(
        self,
        source_stream,
        retained_earnings_values,
        opening_year,
        messages,
    ):
        line_number = 0
        for chunk in source_stream:
            line_number += 1
            decoded = chunk.decode("utf-8")
            parsed_rows = list(csv.reader(io.StringIO(decoded), delimiter="|"))
            if len(parsed_rows) != 1 or len(parsed_rows[0]) != 18:
                raise UserError(
                    messages["malformed"] % {"line": line_number},
                )
            row = parsed_rows[0]
            if line_number == 1:
                yield chunk
                continue
            account_number = row[4] or ""
            if self._fec_account_number_pattern.match(account_number):
                yield chunk
                continue
            is_retained_earnings_opening = (
                row[0] == "OUV"
                and row[2] == f"OUVERTURE/{opening_year}"
                and row[10] == "Balance initiale"
                and account_number == "False"
            )
            if not is_retained_earnings_opening:
                raise UserError(
                    messages["invalid"] % {"line": line_number},
                )
            if not retained_earnings_values:
                raise UserError(messages["missing_retained_earnings"])
            row[4], row[5] = retained_earnings_values
            output = io.StringIO()
            writer = csv.writer(
                output,
                delimiter="|",
                lineterminator="\r\n" if chunk.endswith(b"\r\n") else "",
            )
            writer.writerow(row)
            yield output.getvalue().encode("utf-8")

    def _rebuild_company_retained_earnings_account(self):
        company = self.env.company
        Account = self.env["account.account"].with_company(company)
        accounts = Account.search([
            *Account._check_company_domain(company),
            ("account_type", "=", "equity_unaffected"),
        ], order="code desc")
        account = next(
            (
                candidate
                for candidate in accounts
                if self._fec_account_number_pattern.match(candidate.code or "")
            ),
            False,
        )
        return account

    def create_fec_report_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/usl/accounting/fec/{self.id}",
            "target": "self",
        }
