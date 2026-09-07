"""French annual statements, statement account breakdowns, tax package and closing package row sources."""

from decimal import Decimal

from odoo import fields, models
from odoo.exceptions import UserError

from .report_export_wizard import (
    FRENCH_BALANCE_SUPPLIER_PREFIXES,
    FRENCH_BALANCE_SUSPENSE_ASSET_PREFIXES,
    FRENCH_PROFIT_LOSS_SECTIONS,
    FRENCH_PROFIT_LOSS_SUBTOTALS,
    _amount,
    _amount_text,
    _matches,
)


class RebuildAccountReportExportWizard(models.TransientModel):
    _inherit = "rebuild.account.report.export.wizard"

    def _closing_package_rows(self):
        closing = self.env["rebuild.account.closing.period"].search([
            ("company_id", "=", self.company_id.id),
            ("date_from", "=", self.date_from),
            ("date_to", "=", self.date_to),
        ], order="period_type desc, id desc", limit=1)
        if not closing:
            message = (
                "No closing workspace matches the selected company and exact dates. "
                "Open Closing Workspaces, synchronize the company profile and use "
                "Generate Package from that period."
            )
            raise UserError(message)
        rows = [{
            "section": "Closing overview",
            "line_code": "CLOSE_STATUS",
            "label": closing.name,
            "status": closing.state,
            "validation": closing.readiness_status,
            "record_count": str(len(closing.control_line_ids)),
            "amount": "0.00",
            "details": closing.readiness_summary or "",
            "next_action": closing.actions_awaiting_valentin or "",
            "evidence": closing.package_reference or "",
        }]
        rows.extend({
            "section": "Accepted closing snapshots",
            "line_code": f"SNAPSHOT_{snapshot.id}",
            "label": snapshot.name,
            "status": snapshot.decision_conclusion,
            "validation": "immutable_sha256",
            "record_count": "1",
            "amount": "0.00",
            "details": (
                f"{snapshot.file_size} byte(s); "
                f"captured={fields.Datetime.to_string(snapshot.captured_at)}"
            ),
            "next_action": (
                "Retain this immutable payload and recorded decision with "
                "the closing archive."
            ),
            "evidence": (
                f"sha256={snapshot.sha256}; "
                f"decision={snapshot.review_decision_id.display_name}; "
                f"reviewer={snapshot.reviewer_name}"
            ),
        } for snapshot in closing.snapshot_ids)
        rows.extend({
            "section": f"Closing control - {control.category}",
            "line_code": control.code,
            "label": control.name,
            "status": control.status,
            "validation": control.status,
            "record_count": str(control.record_count),
            "amount": _amount_text(control.amount),
            "details": control.summary or "",
            "next_action": control.next_action or "",
            "evidence": f"owner={control.owner}; accountant_visible={control.accountant_visible}",
        } for control in closing.control_line_ids.sorted(lambda line: (line.category, line.code)))
        declarations = self.env["rebuild.account.declaration"].search([
            ("company_id", "=", self.company_id.id),
            ("fiscalyear_start", "=", closing.fiscalyear_start),
            ("fiscalyear_end", "=", closing.fiscalyear_end),
        ])
        for declaration in declarations:
            rows.append({
                "section": "Declaration schedule",
                "line_code": declaration.form_code,
                "label": declaration.name,
                "status": declaration.status,
                "validation": declaration.validation_status,
                "record_count": str(declaration.prefilled_line_count),
                "amount": _amount_text(declaration.amount_due),
                "details": declaration.validation_summary or "",
                "next_action": declaration.unresolved_information or "",
                "evidence": (
                    f"rule={declaration.rule_id.code}/{declaration.rule_version}; "
                    f"deadline={fields.Date.to_string(declaration.deadline_date)}; "
                    f"official_source={declaration.official_url}; filing_reference={declaration.external_filing_reference or ''}"
                ),
            })
            rows.extend({
                "section": f"Declaration fields - {declaration.form_code}",
                "line_code": field.field_code,
                "label": field.field_label,
                "status": "unresolved" if field.is_unresolved else "prefilled",
                "validation": field.validation_status,
                "record_count": "1",
                "amount": _amount_text(field.amount),
                "details": field.value_text or field.source_formula or "",
                "next_action": field.unresolved_reason or "",
                "evidence": f"{field.source_kind}: {field.source_reference or ''}",
            } for field in declaration.field_line_ids)
        rows.append({
            "section": "Lock dates",
            "line_code": "LOCK_EVIDENCE",
            "label": "Standard Odoo lock-date evidence",
            "status": closing.state,
            "validation": "recorded" if closing.final_lock_dates else "pending",
            "record_count": "0",
            "amount": "0.00",
            "details": f"previous={closing.previous_lock_dates or ''}; final={closing.final_lock_dates or ''}",
            "next_action": "Final lock dates are applied only by an Accounting Manager after closing controls and reviewer approval.",
            "evidence": f"closed_by={closing.closed_by_id.name or ''}; closed_at={closing.closed_at or ''}",
        })
        return rows

    def _french_annual_rows(self, statement_keys=None, report_variant=""):
        tb = self._trial_balance_rows()

        def sum_bal(
            prefixes,
            account_types=None,
            positive=None,
            negative=None,
            excluded_prefixes=None,
            balance_field="balance",
        ):
            total = Decimal("0.00")
            count = 0
            for row in tb:
                balance = _amount(row[balance_field])
                if not _matches(row, prefixes):
                    continue
                if excluded_prefixes and _matches(
                    row,
                    excluded_prefixes,
                ):
                    continue
                if account_types and row["account_type"] not in account_types:
                    continue
                if positive and balance <= 0:
                    continue
                if negative and balance >= 0:
                    continue
                total += balance
                count += int(row.get("move_line_count") or 0)
            return total, count

        def sum_types(
            account_types,
            prefixes=None,
            excluded_prefixes=None,
            positive=None,
            negative=None,
            balance_field="balance",
        ):
            total = Decimal("0.00")
            count = 0
            for item in tb:
                code = item.get("account_code") or ""
                if item.get("account_type") not in account_types:
                    continue
                if prefixes and not any(code.startswith(prefix) for prefix in prefixes):
                    continue
                if excluded_prefixes and any(
                    code.startswith(prefix)
                    for prefix in excluded_prefixes
                ):
                    continue
                balance = _amount(item[balance_field])
                if positive and balance <= 0:
                    continue
                if negative and balance >= 0:
                    continue
                total += balance
                count += int(item.get("move_line_count") or 0)
            return total, count

        def row(
            statement_key,
            line_code,
            line_name,
            amount,
            formula,
            prefixes,
            count=0,
            gross=0,
            depreciation=0,
            presentation_role=None,
        ):
            return {
                "statement_key": statement_key,
                "line_code": line_code,
                "line_name": line_name,
                "source_formula": formula,
                "drilldown_account_prefixes": ",".join(prefixes),
                "move_line_count": str(count),
                "gross_amount": _amount_text(gross),
                "depreciation_amount": _amount_text(depreciation),
                "net_amount": _amount_text(amount),
                "amount": _amount_text(amount),
                "presentation_role": presentation_role,
            }

        fixed_types = {"asset_fixed", "asset_non_current"}
        fixed_net, fixed_count = sum_types(
            fixed_types,
            balance_field="closing_balance",
        )
        fixed_gross, fixed_gross_count = sum_types(
            fixed_types,
            excluded_prefixes=["28", "29"],
            balance_field="closing_balance",
        )
        fixed_depr_balance, fixed_depr_count = sum_types(
            fixed_types,
            prefixes=["28", "29"],
            balance_field="closing_balance",
        )
        current_asset_debits, current_asset_debit_count = sum_types(
            {"asset_current", "asset_receivable", "asset_prepayments"},
            excluded_prefixes=FRENCH_BALANCE_SUSPENSE_ASSET_PREFIXES,
            positive=True,
            balance_field="closing_balance",
        )
        liability_debits, liability_debit_count = sum_types(
            {
                "liability_payable",
                "liability_current",
                "liability_non_current",
            },
            excluded_prefixes=FRENCH_BALANCE_SUPPLIER_PREFIXES,
            positive=True,
            balance_field="closing_balance",
        )
        suspense_assets, suspense_asset_count = sum_bal(
            FRENCH_BALANCE_SUSPENSE_ASSET_PREFIXES,
            balance_field="closing_balance",
        )
        other_receivables = (
            current_asset_debits
            + liability_debits
            + suspense_assets
        )
        other_receivable_count = (
            current_asset_debit_count
            + liability_debit_count
            + suspense_asset_count
        )
        cash, cash_count = sum_types(
            {"asset_cash"},
            positive=True,
            balance_field="closing_balance",
        )
        total_assets = fixed_net + other_receivables + cash
        depreciation = -fixed_depr_balance

        capital, capital_count = sum_bal(
            ["101"],
            balance_field="closing_balance",
        )
        other_equity_balance, other_equity_count = sum_types(
            {"equity", "equity_unaffected"},
            excluded_prefixes=["101"],
            balance_field="closing_balance",
        )
        balance_result, balance_result_count = sum_bal(
            ["6", "7"],
            balance_field="closing_balance",
        )
        financial_debt_balance, financial_debt_count = sum_bal(
            ["16", "17", "455"],
            negative=True,
            balance_field="closing_balance",
        )
        trade_payables, trade_payable_count = sum_bal(
            FRENCH_BALANCE_SUPPLIER_PREFIXES,
            balance_field="closing_balance",
        )
        tax_social_debt, tax_social_count = sum_bal(
            ["42", "43", "44"],
            negative=True,
            balance_field="closing_balance",
        )
        other_liability_credits, other_liability_credit_count = sum_types(
            {
                "liability_payable",
                "liability_current",
                "liability_non_current",
                "liability_credit_card",
            },
            excluded_prefixes=[
                "16",
                "17",
                "401",
                "403",
                "4081",
                "4088",
                "42",
                "43",
                "44",
                "455",
            ],
            negative=True,
            balance_field="closing_balance",
        )
        asset_credits, asset_credit_count = sum_types(
            {
                "asset_current", "asset_receivable", "asset_cash",
                "asset_prepayments",
            },
            excluded_prefixes=[
                "16", "17", "401", "403", "4081", "4088",
                "42", "43", "44", "455",
                *FRENCH_BALANCE_SUSPENSE_ASSET_PREFIXES,
            ],
            negative=True,
            balance_field="closing_balance",
        )
        other_debt = other_liability_credits + asset_credits
        other_debt_count = (
            other_liability_credit_count + asset_credit_count
        )
        financial_debt = -financial_debt_balance
        current_result = -balance_result
        equity = -capital - other_equity_balance + current_result
        total_debt = (
            financial_debt
            - trade_payables
            - tax_social_debt
            - other_debt
        )
        total_passif = equity + total_debt

        result_balance, result_count = sum_bal(["6", "7"])
        goods_sales, goods_sales_count = sum_bal(["707", "7097"])
        goods_production_sales, goods_production_sales_count = sum_bal(
            ["701", "702", "703", "704", "705", "7091", "7092", "7094", "7095"],
        )
        service_sales, service_sales_count = sum_bal(["706"])
        turnover_balance, turnover_count = sum_bal(["70"])
        turnover = -turnover_balance
        operating_income_balance, operating_income_count = sum_bal(
            ["70", "71", "72", "74", "75"],
            excluded_prefixes=["755"],
        )
        operating_income = -operating_income_balance
        goods_purchases, goods_purchases_count = sum_bal(["607"])
        external_charge_prefixes = [
            "601",
            "602",
            "603",
            "604",
            "605",
            "606",
            "608",
            "609",
            "61",
            "62",
        ]
        external_charges, external_charges_count = sum_bal(
            external_charge_prefixes,
        )
        taxes, taxes_count = sum_bal(["631", "633"])
        salaries, salaries_count = sum_bal(["641"])
        social_charges, social_charges_count = sum_bal(["645"])
        depreciation_expense, depreciation_expense_count = sum_bal(["681"])
        other_operating_charge_prefixes = [
            "651",
            "652",
            "653",
            "654",
            "656",
            "657",
            "658",
            "659",
        ]
        other_expenses, other_expenses_count = sum_bal(
            other_operating_charge_prefixes,
        )
        personnel_charges, personnel_charges_count = sum_bal(["64"])
        production_stocked_balance, production_stocked_count = sum_bal(["71"])
        production_capitalized_balance, production_capitalized_count = sum_bal(
            ["72"],
        )
        operating_subsidies_balance, operating_subsidies_count = sum_bal(
            ["74"],
        )
        operating_other_income_balance, operating_other_income_count = sum_bal(
            ["75"],
            excluded_prefixes=["755"],
        )
        operating_reversals_balance, operating_reversals_count = sum_bal(
            ["781"],
        )
        investment_grant_transfer_balance, investment_grant_transfer_count = (
            sum_bal(["777"])
        )
        disposal_proceeds_balance, disposal_proceeds_count = sum_bal(["775"])
        disposal_carrying_value, disposal_carrying_count = sum_bal(["675"])
        operating_expenses, operating_expense_count = sum_bal(
            ["60", "61", "62", "63", "64", "65", "68"],
        )
        financial_income, financial_income_count = sum_bal(["76"])
        financial_charges, financial_charges_count = sum_bal(["66"])
        financial_result = -financial_income - financial_charges
        exceptional_income, exceptional_income_count = sum_bal(["77"])
        exceptional_charges, exceptional_charges_count = sum_bal(["67"])
        sig_exceptional_income, sig_exceptional_income_count = sum_bal(
            ["77"],
            excluded_prefixes=["775", "777"],
        )
        sig_exceptional_charges, sig_exceptional_charges_count = sum_bal(
            ["67"],
            excluded_prefixes=["675"],
        )
        joint_operation_income, joint_operation_income_count = sum_bal(["755"])
        joint_operation_charges, joint_operation_charges_count = sum_bal(
            ["655"],
        )
        employee_profit_sharing, employee_profit_sharing_count = sum_bal(
            ["691"],
        )
        exceptional_result = -exceptional_income - exceptional_charges
        sig_exceptional_result = (
            -sig_exceptional_income
            - sig_exceptional_charges
        )
        income_tax, income_tax_count = sum_bal(["695"])
        total_products_balance, total_products_count = sum_bal(["7"])
        total_charges, total_charges_count = sum_bal(["6"])
        total_products = -total_products_balance
        goods_sales_amount = -goods_sales
        production_sold = turnover - goods_sales_amount
        production_stocked = -production_stocked_balance
        production_capitalized = -production_capitalized_balance
        production_for_period = (
            production_sold
            + production_stocked
            + production_capitalized
        )
        operating_subsidies = -operating_subsidies_balance
        operating_other_income = -operating_other_income_balance
        operating_reversals = -operating_reversals_balance
        investment_grant_transfer = -investment_grant_transfer_balance
        disposal_proceeds = -disposal_proceeds_balance
        joint_operation_income_amount = -joint_operation_income
        joint_operation_charges_amount = joint_operation_charges
        commercial_margin = goods_sales_amount - goods_purchases
        value_added = (
            commercial_margin
            + production_for_period
            - external_charges
        )
        ebe = (
            value_added
            + operating_subsidies
            - taxes
            - personnel_charges
        )
        operating_result = (
            value_added
            - taxes
            - personnel_charges
            + operating_subsidies
            + operating_other_income
            + operating_reversals
            + investment_grant_transfer
            + disposal_proceeds
            - depreciation_expense
            - other_expenses
            - disposal_carrying_value
        )
        current_result_before_tax = (
            operating_result
            + joint_operation_income_amount
            - joint_operation_charges_amount
            + financial_result
        )
        net_result = -result_balance
        all_depreciation_expense, all_depreciation_expense_count = sum_bal(
            ["68"],
        )
        all_reversals_balance, all_reversals_count = sum_bal(["78"])
        all_reversals = -all_reversals_balance
        caf = (
            net_result
            + all_depreciation_expense
            - all_reversals
            + disposal_carrying_value
            - disposal_proceeds
            - investment_grant_transfer
        )

        rows = [
            row("bilan_actif", "ACTIF_IMMO_CORP", "Immobilisations", fixed_net, "Comptes d’actif immobilisé, nets des amortissements et provisions", ["2"], fixed_count, fixed_gross, depreciation),
            row("bilan_actif", "ACTIF_AUTRES_CREANCES", "Stocks, créances et autres actifs courants", other_receivables, "Soldes débiteurs des comptes d’actif courant, de créances et de dettes", ["3", "4"], other_receivable_count),
            row("bilan_actif", "ACTIF_DISPONIBILITES", "Disponibilités", cash, "Comptes classés en trésorerie", ["5"], cash_count),
            row("bilan_actif", "ACTIF_TOTAL", "Total actif", total_assets, "Tous les comptes d’actif selon leur type comptable", ["2", "3", "4", "5"], fixed_count + other_receivable_count + cash_count, fixed_gross + other_receivables + cash, depreciation),
            row("bilan_passif", "PASSIF_CAPITAL", "Capital social", -capital, "101", ["101"], capital_count),
            row("bilan_passif", "PASSIF_RESERVES_REPORT", "Réserves, report à nouveau et autres capitaux propres", -other_equity_balance, "Autres comptes classés en capitaux propres", ["10", "11", "12", "13", "14"], other_equity_count),
            row("bilan_passif", "PASSIF_RESULTAT", "Résultat de l’exercice", current_result, "6 et 7", ["6", "7"], balance_result_count),
            row("bilan_passif", "PASSIF_CAPITAUX_PROPRES", "Total des capitaux propres", equity, "Capitaux propres + résultat de l’exercice", ["1", "6", "7"], capital_count + other_equity_count + balance_result_count, presentation_role="subtotal"),
            row("bilan_passif", "PASSIF_DETTES_FINANCIERES", "Emprunts et dettes financières diverses", financial_debt, "Soldes créditeurs 16/17 et comptes courants d’associés 455", ["16", "17", "455"], financial_debt_count),
            row("bilan_passif", "PASSIF_DETTES_FOURNISSEURS", "Dettes fournisseurs et comptes rattachés", -trade_payables, "Solde net des comptes fournisseurs 400/401/402/403/408", FRENCH_BALANCE_SUPPLIER_PREFIXES, trade_payable_count),
            row("bilan_passif", "PASSIF_DETTES_FISCALES_SOCIALES", "Dettes fiscales et sociales", -tax_social_debt, "Soldes créditeurs 42/43/44", ["42", "43", "44"], tax_social_count),
            row("bilan_passif", "PASSIF_AUTRES_DETTES", "Autres dettes et découverts", -other_debt, "Autres soldes créditeurs classés en passif", ["1", "4", "5"], other_debt_count),
            row("bilan_passif", "PASSIF_TOTAL_DETTES", "Total des dettes", total_debt, "Dettes financières, fournisseurs, fiscales, sociales et autres", ["1", "4", "5"], financial_debt_count + trade_payable_count + tax_social_count + other_debt_count, presentation_role="subtotal"),
            row("bilan_passif", "PASSIF_TOTAL", "Total passif", total_passif, "Capitaux propres + résultat + dettes", ["1", "4", "5", "6", "7"], capital_count + other_equity_count + balance_result_count + financial_debt_count + trade_payable_count + tax_social_count + other_debt_count, presentation_role="total"),
            row("compte_resultat", "CR_VENTES_PRODUITS", "Production vendue — biens", -goods_production_sales, "701 à 705 nets des réductions correspondantes", ["701", "702", "703", "704", "705", "7091", "7092", "7094", "7095"], goods_production_sales_count),
            row("compte_resultat", "CR_SERVICES", "Prestations de services", -service_sales, "706", ["706"], service_sales_count),
            row("compte_resultat", "CR_CHIFFRE_AFFAIRES", "Chiffre d’affaires net", turnover, "70", ["70"], turnover_count),
            row("compte_resultat", "CR_AUTRES_PRODUITS_EXPLOITATION", "Autres produits d’exploitation", operating_other_income, "75 hors opérations en commun 755", ["75"], operating_other_income_count),
            row("compte_resultat", "CR_TOTAL_PRODUITS_EXPLOITATION", "Total des produits d’exploitation", operating_income, "70/71/72/74/75 hors opérations en commun", ["70", "71", "72", "74", "75"], operating_income_count),
            row("compte_resultat", "CR_ACHATS_MARCHANDISES", "Achats de marchandises", goods_purchases, "607", ["607"], goods_purchases_count),
            row("compte_resultat", "CR_CHARGES_EXTERNES", "Autres achats et charges externes", external_charges, "60 hors 607 + 61 + 62", external_charge_prefixes, external_charges_count),
            row("compte_resultat", "CR_IMPOTS_TAXES", "Impôts, taxes et versements assimilés", taxes, "631 + 633", ["631", "633"], taxes_count),
            row("compte_resultat", "CR_SALAIRES", "Salaires et traitements", salaries, "641", ["641"], salaries_count),
            row("compte_resultat", "CR_CHARGES_SOCIALES", "Charges sociales", social_charges, "645", ["645"], social_charges_count),
            row("compte_resultat", "CR_DOTATIONS_AMORTISSEMENTS", "Dotations aux amortissements", depreciation_expense, "681", ["681"], depreciation_expense_count),
            row("compte_resultat", "CR_AUTRES_CHARGES_EXPLOITATION", "Autres charges d’exploitation", other_expenses, "65 hors 655 et 675", other_operating_charge_prefixes, other_expenses_count),
            row("compte_resultat", "CR_TOTAL_CHARGES_EXPLOITATION", "Total charges d’exploitation", operating_expenses, "60 à 65 et 68", ["60", "61", "62", "63", "64", "65", "68"], operating_expense_count),
            row("compte_resultat", "CR_RESULTAT_EXPLOITATION", "Résultat d’exploitation", operating_result, "Produits d’exploitation - charges d’exploitation", ["70", "71", "72", "74", "75", "60", "61", "62", "63", "64", "65", "68"]),
            row("compte_resultat", "CR_PRODUITS_FINANCIERS", "Produits financiers", -financial_income, "76", ["76"], financial_income_count),
            row("compte_resultat", "CR_CHARGES_FINANCIERES", "Charges financières", financial_charges, "66", ["66"], financial_charges_count),
            row("compte_resultat", "CR_RESULTAT_FINANCIER", "Résultat financier", financial_result, "76 - 66", ["76", "66"], financial_income_count + financial_charges_count),
            row("compte_resultat", "CR_RESULTAT_COURANT_AVANT_IMPOT", "Résultat courant avant impôts", current_result_before_tax, "Résultat exploitation + résultat financier", ["70", "758", "60", "61", "62", "63", "64", "658", "681", "76", "66"]),
            row("compte_resultat", "CR_RESULTAT_EXCEPTIONNEL", "Résultat exceptionnel", exceptional_result, "77 - 67", ["77", "67"], exceptional_income_count + exceptional_charges_count),
            row("compte_resultat", "CR_IMPOTS_BENEFICES", "Impôts sur les bénéfices", income_tax, "695", ["695"], income_tax_count),
            row("compte_resultat", "CR_TOTAL_PRODUITS", "Total des produits", total_products, "Total des comptes de classe 7", ["7"], total_products_count, presentation_role="subtotal"),
            row("compte_resultat", "CR_TOTAL_CHARGES", "Total des charges", total_charges, "Total des comptes de classe 6", ["6"], total_charges_count, presentation_role="subtotal"),
            row("compte_resultat", "CR_RESULTAT_NET", "Résultat net de l’exercice", net_result, "Total des produits - total des charges", ["6", "7"], result_count, presentation_role="total"),
            row("sig_caf", "SIG_VALEUR_AJOUTEE", "Valeur ajoutée (I + II - III)", value_added, "Marge commerciale + production de l’exercice - consommations externes", ["70", "607", "606", "61", "62"], turnover_count + goods_purchases_count + external_charges_count, presentation_role="subtotal"),
            row("sig_caf", "SIG_MARGE_COMMERCIALE", "Marge commerciale (I)", commercial_margin, "Ventes de marchandises - coût d’achat des marchandises vendues", ["707", "7097", "607"], goods_sales_count + goods_purchases_count, presentation_role="subtotal"),
            row("sig_caf", "SIG_VENTES_MARCHANDISES", "Ventes de marchandises", goods_sales_amount, "707 net de 7097", ["707", "7097"], goods_sales_count),
            row("sig_caf", "SIG_COUT_ACHAT_MARCHANDISES", "Coût d’achat des marchandises vendues", goods_purchases, "607", ["607"], goods_purchases_count),
            row("sig_caf", "SIG_PRODUCTION_EXERCICE", "Production de l’exercice (II)", production_for_period, "Production vendue + production stockée + production immobilisée", ["70", "71", "72"], turnover_count + production_stocked_count + production_capitalized_count, presentation_role="subtotal"),
            row("sig_caf", "SIG_PRODUCTION_VENDUE", "Production vendue", production_sold, "Chiffre d’affaires hors ventes de marchandises", ["70"], turnover_count),
            row("sig_caf", "SIG_CHIFFRE_AFFAIRES_NET", "Montant net du chiffre d’affaires", turnover, "70", ["70"], turnover_count),
            row("sig_caf", "SIG_PRODUCTION_STOCKEE", "Production (dé)stockée", production_stocked, "71", ["71"], production_stocked_count),
            row("sig_caf", "SIG_PRODUCTION_IMMOBILISEE", "Production immobilisée", production_capitalized, "72", ["72"], production_capitalized_count),
            row("sig_caf", "SIG_CONSOMMATIONS_TIERS", "Consommations de l’exercice en provenance de tiers (III)", external_charges, "60 hors 607 + 61 + 62", external_charge_prefixes, external_charges_count),
            row("sig_caf", "SIG_EBE", "Excédent brut d’exploitation", ebe, "Valeur ajoutée + subventions - impôts et taxes - charges de personnel", ["70", "71", "72", "74", "60", "61", "62", "63", "64"], turnover_count + external_charges_count + operating_subsidies_count + taxes_count + personnel_charges_count, presentation_role="subtotal"),
            row("sig_caf", "SIG_SUBVENTIONS_EXPLOITATION", "Subventions d’exploitation", operating_subsidies, "74", ["74"], operating_subsidies_count),
            row("sig_caf", "SIG_IMPOTS_TAXES", "Impôts, taxes et versements assimilés", taxes, "631 + 633", ["631", "633"], taxes_count),
            row("sig_caf", "SIG_CHARGES_PERSONNEL", "Charges de personnel", personnel_charges, "64", ["64"], personnel_charges_count),
            row("sig_caf", "SIG_RESULTAT_EXPLOITATION", "Résultat d’exploitation", operating_result, "EBE + autres produits et reprises - dotations et autres charges", ["70", "71", "72", "74", "75", "77", "78", "60", "61", "62", "63", "64", "65", "67", "68"], operating_income_count + operating_expense_count + operating_reversals_count + disposal_proceeds_count + disposal_carrying_count, presentation_role="subtotal"),
            row("sig_caf", "SIG_AUTRES_PRODUITS_EXPLOITATION", "Autres produits d’exploitation", operating_other_income, "758", ["758"], operating_other_income_count),
            row("sig_caf", "SIG_REPRISES_EXPLOITATION", "Reprises sur amortissements, dépréciations et provisions d’exploitation", operating_reversals, "781", ["781"], operating_reversals_count),
            row("sig_caf", "SIG_TRANSFERT_SUBVENTIONS", "Quote-part des subventions d’investissement transférée au résultat", investment_grant_transfer, "777", ["777"], investment_grant_transfer_count),
            row("sig_caf", "SIG_PRODUITS_CESSIONS", "Produits des cessions d’immobilisations", disposal_proceeds, "775", ["775"], disposal_proceeds_count),
            row("sig_caf", "SIG_DOTATIONS_EXPLOITATION", "Dotations aux amortissements, dépréciations et provisions d’exploitation", depreciation_expense, "681", ["681"], depreciation_expense_count),
            row("sig_caf", "SIG_AUTRES_CHARGES", "Autres charges d’exploitation", other_expenses, "65 hors 655 et 675", other_operating_charge_prefixes, other_expenses_count),
            row("sig_caf", "SIG_VNC_CESSIONS", "Valeur comptable des immobilisations cédées", disposal_carrying_value, "675", ["675"], disposal_carrying_count),
            row("sig_caf", "SIG_RESULTAT_COURANT_AVANT_IMPOT", "Résultat courant avant impôts", current_result_before_tax, "Résultat d’exploitation + résultat financier + opérations en commun", ["70", "60", "61", "62", "63", "64", "65", "66", "68", "75", "76"], operating_income_count + operating_expense_count + financial_income_count + financial_charges_count + joint_operation_income_count + joint_operation_charges_count, presentation_role="subtotal"),
            row("sig_caf", "SIG_QUOTE_PART_PRODUITS_COMMUN", "Quote-part de résultat sur opérations faites en commun — produits", joint_operation_income_amount, "755", ["755"], joint_operation_income_count),
            row("sig_caf", "SIG_PRODUITS_FINANCIERS", "Produits financiers", -financial_income, "76", ["76"], financial_income_count),
            row("sig_caf", "SIG_QUOTE_PART_CHARGES_COMMUN", "Quote-part de résultat sur opérations faites en commun — charges", joint_operation_charges_amount, "655", ["655"], joint_operation_charges_count),
            row("sig_caf", "SIG_CHARGES_FINANCIERES", "Charges financières", financial_charges, "66", ["66"], financial_charges_count),
            row("sig_caf", "SIG_RESULTAT_EXCEPTIONNEL", "Résultat exceptionnel", sig_exceptional_result, "Produits exceptionnels - charges exceptionnelles", ["77", "67"], sig_exceptional_income_count + sig_exceptional_charges_count, presentation_role="subtotal"),
            row("sig_caf", "SIG_PRODUITS_EXCEPTIONNELS", "Produits exceptionnels", -sig_exceptional_income, "77 hors 775 et 777", ["77"], sig_exceptional_income_count),
            row("sig_caf", "SIG_CHARGES_EXCEPTIONNELLES", "Charges exceptionnelles", sig_exceptional_charges, "67 hors 675", ["67"], sig_exceptional_charges_count),
            row("sig_caf", "SIG_RESULTAT_NET", "Résultat de l’exercice", net_result, "Solde des comptes 6 et 7", ["6", "7"], result_count, presentation_role="total"),
            row("sig_caf", "SIG_PARTICIPATION_SALARIES", "Participation des salariés", employee_profit_sharing, "691", ["691"], employee_profit_sharing_count),
            row("sig_caf", "SIG_IMPOT_BENEFICES", "Impôt sur les bénéfices", income_tax, "695", ["695"], income_tax_count),
            row("sig_caf", "CAF_RESULTAT_NET", "CAF — Résultat net comptable", net_result, "Point de départ de la CAF", ["6", "7"], result_count, presentation_role="subtotal"),
            row("sig_caf", "CAF_DOTATIONS", "CAF — (+) Dotations aux amortissements, dépréciations et provisions", all_depreciation_expense, "68", ["68"], all_depreciation_expense_count),
            row("sig_caf", "CAF_REPRISES", "CAF — (-) Reprises sur amortissements, dépréciations et provisions", all_reversals, "78", ["78"], all_reversals_count),
            row("sig_caf", "CAF_VNC_CESSIONS", "CAF — (+) Valeur comptable des immobilisations cédées", disposal_carrying_value, "675", ["675"], disposal_carrying_count),
            row("sig_caf", "CAF_PRODUITS_CESSIONS", "CAF — (-) Produits des cessions d’immobilisations", disposal_proceeds, "775", ["775"], disposal_proceeds_count),
            row("sig_caf", "CAF_TRANSFERT_SUBVENTIONS", "CAF — (-) Quote-part des subventions d’investissement transférée", investment_grant_transfer, "777", ["777"], investment_grant_transfer_count),
            row("sig_caf", "SIG_CAPACITE_AUTOFINANCEMENT", "Capacité d’autofinancement", caf, "Résultat net + charges non décaissées - produits non encaissés", ["6", "7", "68", "78", "675", "775", "777"], result_count + all_depreciation_expense_count + all_reversals_count + disposal_carrying_count + disposal_proceeds_count + investment_grant_transfer_count, presentation_role="total"),
        ]
        for item in rows:
            if item["statement_key"] != "compte_resultat":
                continue
            item["section"] = FRENCH_PROFIT_LOSS_SECTIONS.get(
                item["line_code"],
                "Compte de résultat",
            )
            if item["line_code"] == "CR_RESULTAT_NET":
                item["presentation_role"] = "total"
            elif item["line_code"] in FRENCH_PROFIT_LOSS_SUBTOTALS:
                item["presentation_role"] = "subtotal"
        self._attach_statement_account_breakdowns(rows, tb)
        if statement_keys:
            rows = [item for item in rows if item["statement_key"] in statement_keys]
        if report_variant:
            for item in rows:
                item["report_variant"] = report_variant
                item["applicability_basis"] = self._report_variant_basis()
        return rows

    def _attach_statement_account_breakdowns(self, rows, trial_balance_rows):
        """Attach only account contributions that reconcile to their line."""
        self.ensure_one()
        account_ids = [
            int(item["account_id"])
            for item in trial_balance_rows
            if item.get("account_id")
        ]
        accounts = self.env["account.account"].browse(account_ids)
        account_by_id = {
            account.id: account.with_company(self.company_id)
            for account in accounts
        }
        for row in rows:
            if row.get("presentation_role") in {"subtotal", "total"}:
                continue
            prefixes = [
                prefix.strip()
                for prefix in (
                    row.get("drilldown_account_prefixes") or ""
                ).split(",")
                if prefix.strip()
            ]
            if not prefixes:
                continue
            contributions = [
                item
                for item in trial_balance_rows
                if _matches(item, prefixes)
                and _amount(item.get("balance"))
                and int(item.get("move_line_count") or 0)
            ]
            if not contributions:
                continue
            raw_total = sum(
                (_amount(item.get("balance")) for item in contributions),
                Decimal("0.00"),
            )
            statement_amount = _amount(
                row.get("amount") or row.get("net_amount"),
            )
            if abs(statement_amount - raw_total) <= Decimal("0.01"):
                sign = Decimal("1.00")
            elif abs(statement_amount + raw_total) <= Decimal("0.01"):
                sign = Decimal("-1.00")
            else:
                # Derived totals and conditionally filtered balances remain
                # calculation rows until their exact source rule is available.
                continue
            breakdown = []
            for item in contributions:
                account = account_by_id.get(int(item["account_id"]))
                groups = []
                group = account.group_id if account else False
                while group:
                    group_code = str(group.code_prefix_start or "")
                    if (
                        group.code_prefix_end
                        and group.code_prefix_end != group.code_prefix_start
                    ):
                        group_code += f"-{group.code_prefix_end}"
                    groups.append({
                        "id": group.id,
                        "code": group_code,
                        "name": group.with_context(lang="fr_FR").name,
                    })
                    group = group.parent_id
                breakdown.append({
                    "account_id": int(item["account_id"]),
                    "source_account_id": item.get("source_account_id") or "",
                    "account_code": item.get("account_code") or "",
                    "account_name": item.get("account_name") or "",
                    "move_line_count": int(item.get("move_line_count") or 0),
                    "amount": _amount_text(
                        _amount(item.get("balance")) * sign,
                    ),
                    "group_chain": list(reversed(groups)),
                })
            row["account_breakdown"] = breakdown

    def _french_tax_package_rows(self):
        period_key = "Fiscal year 2024-01-10 to 2025-09-30"
        if fields.Date.to_string(self.date_from) != "2024-01-10" or fields.Date.to_string(self.date_to) != "2025-09-30":
            return []
        self.env.cr.execute(
            """
            SELECT form_code,
                   form_name,
                   field_code,
                   field_label,
                   source_kind,
                   source_formula,
                   COALESCE(source_report_line_code, '') AS source_report_line_code,
                   COALESCE(drilldown_account_prefixes, '') AS drilldown_account_prefixes,
                   move_line_count::text AS move_line_count,
                   quantity::text AS quantity,
                   round(amount::numeric, 2)::text AS amount,
                   round(rounded_amount::numeric, 2)::text AS rounded_amount,
                   COALESCE(round(benchmark_amount::numeric, 2)::text, '') AS benchmark_amount,
                   COALESCE(round(ledger_amount::numeric, 2)::text, '') AS ledger_amount,
                   COALESCE(round(difference_amount::numeric, 2)::text, '') AS difference_amount,
                   COALESCE(difference_classification, '') AS difference_classification,
                   COALESCE(value_text, '') AS value_text,
                   review_status
              FROM rebuild_account_french_tax_package_line
             WHERE company_id = %s
               AND period_key = %s
             ORDER BY form_code, line_sequence, field_code
            """,
            [self.company_id.id, period_key],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]
