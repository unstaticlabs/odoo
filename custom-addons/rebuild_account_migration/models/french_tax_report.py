import ast
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


_ZERO = Decimal("0")
_WHOLE_EURO = Decimal("1")
_REFERENCE_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$",
)
_OTHER_EXPR_RE = re.compile(
    r"^if_other_expr_above\(([^,]+),\s*EUR\(0\)\)$",
)


def _decimal(value):
    return Decimal(str(value or 0))


def _amount_text(value):
    return f"{_decimal(value).quantize(Decimal('0.01')):.2f}"


class RebuildFrenchTaxReport(models.TransientModel):
    """Render the installed French CA3 definition in Community Odoo.

    Community ships the official ``account.report`` metadata but not the
    Enterprise report engine. This evaluator deliberately supports only the
    three engines used by ``l10n_fr_account.tax_report`` and fails closed if
    the localization starts using another construct.
    """

    _inherit = "rebuild.account.report.export.wizard"

    def _french_tax_report(self):
        report = self.env.ref(
            "l10n_fr_account.tax_report",
            raise_if_not_found=False,
        )
        if not report:
            raise UserError(
                "Le rapport fiscal français fourni par l10n_fr_account "
                "est introuvable."
            )
        return report

    def _tax_report_rows(self):
        self.ensure_one()
        report = self._french_tax_report()
        values = self._french_tax_expression_values(report)
        tag_ids_by_name = self._french_tax_tag_ids_by_name()
        rows = []

        def append_line(line, parent_key="", depth=0, section=""):
            localized_line = line.with_context(lang="fr_FR")
            label = localized_line.name
            current_section = section or label
            group_key = f"tax:{self.company_id.id}:{line.id}"
            expressions = line.expression_ids
            direct_tag_names = sorted({
                self._french_tax_formula_tag_name(expression.formula)
                for expression in expressions
                if expression.engine == "tax_tags"
            } - {""})
            source_tag_ids = sorted({
                tag_id
                for tag_name in direct_tag_names
                for tag_id in tag_ids_by_name.get(tag_name, [])
            })
            balance_expression = expressions.filtered(
                lambda expression: expression.label == "balance"
            )[:1]
            adjustment_expression = expressions.filtered(
                lambda expression: expression.label == "adjustment"
                and expression.engine == "external"
            )[:1]
            children = line.children_ids.sorted(
                lambda child: (child.sequence, child.id),
            )
            role = self._french_tax_presentation_role(
                line,
                depth=depth,
                has_children=bool(children),
            )
            row = {
                "label": label,
                "line_code": line.code or "",
                "report_section": current_section,
                "balance": (
                    _amount_text(values.get((line.code, "balance"), _ZERO))
                    if balance_expression
                    else ""
                ),
                "adjustment": (
                    _amount_text(
                        values.get((line.code, "adjustment"), _ZERO),
                    )
                    if adjustment_expression
                    else ""
                ),
                "adjustment_editable": bool(
                    adjustment_expression
                    and "editable" in (adjustment_expression.subformula or "")
                    and self.env.user.has_group("account.group_account_manager")
                ),
                "source_tax_tag_id": (
                    str(source_tag_ids[0]) if len(source_tag_ids) == 1 else ""
                ),
                "source_tax_tag_ids": source_tag_ids,
                "tax_tag_names": direct_tag_names,
                "can_drilldown": bool(source_tag_ids),
                "is_group": "true" if children else "false",
                "group_key": group_key if children else "",
                "parent_group_key": parent_key,
                "row_level": depth,
                "hierarchy_kind": "french_tax_report_line",
                "presentation_role": role,
            }
            rows.append(row)
            for child in children:
                append_line(
                    child,
                    parent_key=group_key,
                    depth=depth + 1,
                    section=current_section,
                )

        top_lines = report.line_ids.filtered(lambda line: not line.parent_id)
        for line in top_lines.sorted(lambda item: (item.sequence, item.id)):
            append_line(line)
        return rows

    @staticmethod
    def _french_tax_presentation_role(line, *, depth, has_children):
        if depth == 0:
            return "section"
        if has_children:
            return "group"
        code = (line.code or "").removeprefix("box_")
        if code in {"16", "23", "25", "TD", "28", "32", "TIC_total", "Z4"}:
            return "total" if code in {"28", "32"} else "subtotal"
        return "detail"

    def _french_tax_expression_values(
        self,
        report,
        *,
        tag_balances=None,
        external_values=None,
    ):
        """Evaluate the formula graph used by the installed French report."""
        self.ensure_one()
        expressions = report.line_ids.expression_ids
        unsupported = expressions.filtered(
            lambda expression: expression.engine not in {
                "tax_tags",
                "external",
                "aggregation",
            }
        )
        if unsupported:
            engines = ", ".join(sorted(set(unsupported.mapped("engine"))))
            raise UserError(
                "Le rapport fiscal français utilise un moteur non pris en "
                f"charge : {engines}."
            )
        tag_balances = (
            self._french_tax_tag_balances()
            if tag_balances is None
            else {name: _decimal(value) for name, value in tag_balances.items()}
        )
        external_values = (
            self._french_tax_external_values(expressions)
            if external_values is None
            else {
                key: _decimal(value)
                for key, value in external_values.items()
            }
        )
        by_key = {
            (expression.report_line_id.code, expression.label): expression
            for expression in expressions
        }
        cache = {}
        active = set()

        def resolve(key):
            if key in cache:
                return cache[key]
            expression = by_key.get(key)
            if not expression:
                raise UserError(
                    "La formule de TVA référence une expression inconnue : "
                    f"{key[0]}.{key[1]}."
                )
            if key in active:
                raise UserError(
                    "Une dépendance circulaire a été détectée dans le "
                    f"rapport de TVA : {key[0]}.{key[1]}."
                )
            active.add(key)
            if expression.engine == "tax_tags":
                value = self._french_tax_tag_formula_value(
                    expression.formula,
                    tag_balances,
                )
            elif expression.engine == "external":
                value = external_values.get(key, _ZERO)
            else:
                value = self._french_tax_arithmetic_value(
                    expression.formula,
                    resolve,
                )
            value = self._french_tax_apply_subformula(
                value,
                expression.subformula,
                resolve,
            )
            active.remove(key)
            cache[key] = value
            return value

        for key in by_key:
            resolve(key)
        return cache

    def _french_tax_tag_balances(self):
        self.ensure_one()
        domain = list(self._journal_item_domain(company_ids=[self.company_id.id]))
        domain += list(self.env["account.move.line"]._get_tax_exigible_domain())
        lines = self.env["account.move.line"].search(domain)
        balances = defaultdict(Decimal)
        for line in lines:
            for tag in line.tax_tag_ids:
                tag_name = tag.with_context(lang="en_US").name or ""
                balances[tag_name] += _decimal(line.balance)
        return dict(balances)

    def _french_tax_tag_ids_by_name(self):
        tag_ids = defaultdict(list)
        for tag in self.env["account.account.tag"].search([]):
            name = tag.with_context(lang="en_US").name or ""
            tag_ids[name].append(tag.id)
        return dict(tag_ids)

    @staticmethod
    def _french_tax_formula_tag_name(formula):
        formula = (formula or "").strip()
        return formula[1:] if formula.startswith("-") else formula

    def _french_tax_tag_formula_value(self, formula, tag_balances):
        tag_name = self._french_tax_formula_tag_name(formula)
        if not tag_name or any(character.isspace() for character in tag_name):
            raise UserError(
                f"Formule de grille de TVA non prise en charge : {formula}."
            )
        value = tag_balances.get(tag_name, _ZERO)
        return -value if (formula or "").strip().startswith("-") else value

    def _french_tax_external_values(self, expressions):
        expression_ids = expressions.filtered(
            lambda expression: expression.engine == "external"
        ).ids
        values = self.env["account.report.external.value"].search([
            ("target_report_expression_id", "in", expression_ids),
            ("company_id", "=", self.company_id.id),
            ("date", "<=", self.date_to),
        ], order="date, id")
        grouped = defaultdict(list)
        for value in values:
            grouped[value.target_report_expression_id.id].append(value)
        result = {}
        for expression in expressions.filtered(
            lambda item: item.engine == "external"
        ):
            candidates = grouped.get(expression.id, [])
            if expression.formula == "sum":
                selected = [
                    value
                    for value in candidates
                    if self.date_from <= value.date <= self.date_to
                ]
                result[(expression.report_line_id.code, expression.label)] = sum(
                    (_decimal(value.value) for value in selected),
                    _ZERO,
                )
            elif expression.formula == "most_recent":
                result[(expression.report_line_id.code, expression.label)] = (
                    _decimal(candidates[-1].value) if candidates else _ZERO
                )
            else:
                raise UserError(
                    "Formule de valeur externe TVA non prise en charge : "
                    f"{expression.formula}."
                )
        return result

    def _french_tax_arithmetic_value(self, formula, resolve):
        try:
            node = ast.parse((formula or "").strip(), mode="eval")
        except SyntaxError as error:
            raise UserError(
                f"Formule d'agrégation TVA invalide : {formula}."
            ) from error

        def evaluate(item):
            if isinstance(item, ast.Expression):
                return evaluate(item.body)
            if isinstance(item, ast.Constant) and isinstance(item.value, (int, float)):
                return _decimal(item.value)
            if isinstance(item, ast.UnaryOp) and isinstance(item.op, (ast.UAdd, ast.USub)):
                value = evaluate(item.operand)
                return value if isinstance(item.op, ast.UAdd) else -value
            if isinstance(item, ast.BinOp) and isinstance(
                item.op,
                (ast.Add, ast.Sub, ast.Mult, ast.Div),
            ):
                left = evaluate(item.left)
                right = evaluate(item.right)
                if isinstance(item.op, ast.Add):
                    return left + right
                if isinstance(item.op, ast.Sub):
                    return left - right
                if isinstance(item.op, ast.Mult):
                    return left * right
                if right == 0:
                    raise UserError(
                        f"Division par zéro dans la formule TVA : {formula}."
                    )
                return left / right
            if isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name):
                reference = f"{item.value.id}.{item.attr}"
                if not _REFERENCE_RE.fullmatch(reference):
                    raise UserError(
                        f"Référence TVA non prise en charge : {reference}."
                    )
                return resolve((item.value.id, item.attr))
            raise UserError(
                f"Construction non prise en charge dans la formule TVA : {formula}."
            )

        return evaluate(node)

    def _french_tax_apply_subformula(self, value, subformula, resolve):
        subformula = (subformula or "").strip()
        if not subformula:
            return value
        parts = [part.strip() for part in subformula.split(";") if part.strip()]
        for part in parts:
            if part in {"editable", "rounding=0"}:
                if part == "rounding=0":
                    value = value.quantize(_WHOLE_EURO, rounding=ROUND_HALF_UP)
                continue
            if part == "round(0)":
                value = value.quantize(_WHOLE_EURO, rounding=ROUND_HALF_UP)
                continue
            if part == "if_above(EUR(0))":
                value = max(value, _ZERO)
                continue
            match = _OTHER_EXPR_RE.fullmatch(part)
            if match:
                reference = match.group(1).strip()
                if not _REFERENCE_RE.fullmatch(reference):
                    raise UserError(
                        f"Condition TVA non prise en charge : {part}."
                    )
                line_code, label = reference.split(".", 1)
                value = value if resolve((line_code, label)) > 0 else _ZERO
                continue
            raise UserError(f"Sous-formule TVA non prise en charge : {part}.")
        return value

    def _group_report_rows(self, rows):
        if self.report_type == "tax_report":
            return rows
        return super()._group_report_rows(rows)

    def _visible_preview_rows(self, rows):
        if self.report_type != "tax_report":
            return super()._visible_preview_rows(rows)
        collapsed = self._collapsed_group_key_set()
        hidden_groups = set()
        visible = []
        for row in rows:
            parent_key = str(row.get("parent_group_key") or "")
            if parent_key in collapsed or parent_key in hidden_groups:
                if row.get("is_group") in (True, "true"):
                    hidden_groups.add(str(row.get("group_key") or ""))
                continue
            visible.append(row)
            if (
                row.get("is_group") in (True, "true")
                and str(row.get("group_key") or "") in collapsed
            ):
                hidden_groups.add(str(row.get("group_key") or ""))
        return visible

    def _report_client_columns(self):
        if self.report_type != "tax_report":
            return super()._report_client_columns()
        unit_label = self._display_unit_metadata()["short_label"]
        return [
            {"key": "balance", "label": f"Solde ({unit_label})", "type": "currency"},
            {
                "key": "adjustment",
                "label": f"Ajustement ({unit_label})",
                "type": "currency",
            },
        ]

    def _report_export_columns(self, rows):
        if self.report_type == "tax_report":
            return [
                ("label", "Rubrique fiscale"),
                ("balance", "Solde"),
                ("adjustment", "Ajustement"),
            ]
        return super()._report_export_columns(rows)

    def _report_client_capabilities(self):
        capabilities = super()._report_client_capabilities()
        if self.report_type == "tax_report":
            capabilities.update({
                "comparison": False,
                "group_by": False,
                "accounts": False,
                "partners": False,
                "analytics": False,
                "hide_zero_accounts": False,
            })
        return capabilities

    def _report_client_payload(self):
        payload = super()._report_client_payload()
        if self.report_type != "tax_report":
            return payload
        payload.update({
            "title": "Déclaration de TVA",
            "warning": self._french_tax_control_warning(),
            "warning_level": "danger",
            "notices": (
                [{"level": "info", "message": self.preview_warning}]
                if self.preview_warning
                else []
            ),
        })
        preview_by_id = {
            line.id: line._row_payload()
            for line in self.preview_line_ids
        }
        for line in payload["lines"]:
            row = preview_by_id.get(line["id"], {})
            line.update({
                "line_code": row.get("line_code") or "",
                "company_id": int(
                    row.get("report_company_id") or self.company_id.id,
                ),
                "adjustment_editable": bool(row.get("adjustment_editable")),
                "can_drilldown": bool(row.get("can_drilldown")),
            })
        return payload

    def _french_tax_control_warning(self):
        failed_companies = []
        warning = ""
        for company in self._selected_companies():
            clone = self.with_company(company).create(
                self._report_clone_values(
                    company,
                    self.date_from,
                    self.date_to,
                ),
            )
            try:
                expression_values = clone._french_tax_expression_values(
                    clone._french_tax_report(),
                )
            finally:
                clone.sudo().unlink()
            values = {
                code: value
                for (code, label), value in expression_values.items()
                if label == "balance"
            }
            company_warning = self._french_tax_control_warning_from_values(values)
            if company_warning:
                warning = company_warning
                failed_companies.append(company.display_name)
        if not failed_companies:
            return ""
        if len(self._selected_companies()) > 1:
            warning += " Sociétés concernées : " + ", ".join(failed_companies) + "."
        return warning

    @staticmethod
    def _french_tax_control_warning_from_values(values):
        left_codes = {
            "box_08_base",
            "box_09_base",
            "box_9B_base",
            "box_10_base",
            "box_11_base",
            *(f"box_T{index}_base" for index in range(1, 8)),
        }
        right_codes = {
            "box_A1",
            "box_A2",
            "box_A3",
            "box_B2",
            "box_B3",
            "box_B4",
        }
        left = sum((values.get(code, _ZERO) for code in left_codes), _ZERO)
        right = sum((values.get(code, _ZERO) for code in right_codes), _ZERO)
        if left == right:
            return ""
        return (
            "Les contrôles suivants ont échoué : la somme des champs "
            "08 + 09 + 9B + 10 + 11 + T1 à T7 n'est pas égale à la somme "
            "des champs A1 + A2 + A3 + B2 + B3 + B4."
        )

    def _preview_journal_item_domain(self, row):
        domain = super()._preview_journal_item_domain(row)
        if self.report_type != "tax_report":
            return domain
        tag_ids = [int(tag_id) for tag_id in row.get("source_tax_tag_ids") or []]
        if tag_ids and not row.get("source_tax_tag_id"):
            domain.append(("tax_tag_ids", "in", tag_ids))
        return domain

    def _hide_zero_account_rows(self, rows):
        if self.report_type == "tax_report":
            return rows
        return super()._hide_zero_account_rows(rows)

    @api.model
    def report_client_set_tax_adjustment(
        self,
        wizard_id,
        line_code,
        value,
        company_id=None,
    ):
        wizard = self.browse(wizard_id).exists()
        if not wizard or wizard.report_type != "tax_report":
            raise UserError("La session du rapport de TVA a expiré.")
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(
                "Seul un responsable de la comptabilité peut modifier "
                "un ajustement de TVA."
            )
        try:
            desired_total = Decimal(str(value).replace(",", "."))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise UserError("Saisissez un montant d'ajustement valide.") from error
        if not desired_total.is_finite():
            raise UserError("Saisissez un montant d'ajustement fini.")
        company = self.env["res.company"].browse(
            int(company_id or wizard.company_id.id),
        ).exists()
        if not company or company not in wizard._selected_companies():
            raise AccessError(
                "La société de cet ajustement n'appartient pas au périmètre "
                "du rapport."
            )
        report = wizard._french_tax_report()
        report_line = report.line_ids.filtered(
            lambda line: line.code == line_code
        )[:1]
        expression = report_line.expression_ids.filtered(
            lambda item: item.label == "adjustment"
            and item.engine == "external"
            and "editable" in (item.subformula or "")
        )[:1]
        if not expression:
            raise UserError("Cette ligne ne permet pas d'ajustement manuel.")
        external_values = self.env["account.report.external.value"].search([
            ("target_report_expression_id", "=", expression.id),
            ("company_id", "=", company.id),
            ("date", ">=", wizard.date_from),
            ("date", "<=", wizard.date_to),
        ], order="date, id")
        value_at_period_end = external_values.filtered(
            lambda item: item.date == wizard.date_to
        )[:1]
        other_total = sum(
            (
                _decimal(item.value)
                for item in external_values
                if item != value_at_period_end
            ),
            _ZERO,
        )
        entry_value = desired_total - other_total
        vals = {
            "name": f"Ajustement manuel TVA – {line_code.removeprefix('box_')}",
            "value": float(entry_value),
            "date": wizard.date_to,
            "target_report_expression_id": expression.id,
            "company_id": company.id,
        }
        if value_at_period_end:
            value_at_period_end.write(vals)
        else:
            self.env["account.report.external.value"].create(vals)
        wizard.action_preview_report()
        return wizard._report_client_payload()
