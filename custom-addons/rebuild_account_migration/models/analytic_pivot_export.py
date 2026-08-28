from datetime import date, datetime

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.models import BaseModel

PIVOT_AXIS_FIELDS = {
    "account_id",
    "date",
    "general_account_id",
    "rebuild_financial_account_group_id",
    "rebuild_financial_account_type",
    "partner_id",
    "product_id",
    "journal_id",
    "expense_batch_id",
    "expense_payment_mode",
    "company_id",
}
PIVOT_MEASURES = {
    "rebuild_net_contribution",
    "rebuild_revenue",
    "rebuild_spending",
    "amount",
    "unit_amount",
    "__count",
}
PIVOT_DATE_INTERVALS = {"day", "week", "month", "quarter", "year"}


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    @api.model
    def _usl_validate_pivot_axes(self, values, *, name, maximum):
        if not isinstance(values, list) or not 0 <= len(values) <= maximum:
            raise ValidationError(
                _("%(name)s must contain at most %(maximum)s axes.", name=name, maximum=maximum),
            )
        result = []
        for value in values:
            if not isinstance(value, str):
                raise ValidationError(_("Pivot axes must be strings."))
            field_name, separator, interval = value.partition(":")
            if field_name not in PIVOT_AXIS_FIELDS:
                raise ValidationError(_("The pivot axis %s is not allowed.", field_name))
            if separator and (
                field_name != "date" or interval not in PIVOT_DATE_INTERVALS
            ):
                raise ValidationError(_("The pivot interval is not allowed."))
            result.append(value)
        if len(result) != len(set(result)):
            raise ValidationError(_("Pivot axes must be unique."))
        return result

    @api.model
    def _usl_validate_pivot_request(self, payload):
        if not isinstance(payload, dict):
            raise ValidationError(_("The pivot export request must be an object."))
        allowed = {
            "row_axes", "column_axes", "measures", "domain", "context",
            "order", "company_id",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValidationError(_("The pivot export contains unknown fields."))
        row_axes = self._usl_validate_pivot_axes(
            payload.get("row_axes", []),
            name=_("Row axes"),
            maximum=3,
        )
        column_axes = self._usl_validate_pivot_axes(
            payload.get("column_axes", []),
            name=_("Column axes"),
            maximum=2,
        )
        if set(row_axes) & set(column_axes):
            raise ValidationError(_("A pivot axis cannot be used on both sides."))
        measures = payload.get("measures", [])
        if (
            not isinstance(measures, list)
            or not 1 <= len(measures) <= 5
            or any(measure not in PIVOT_MEASURES for measure in measures)
            or len(measures) != len(set(measures))
        ):
            raise ValidationError(_("The pivot measures are not allowed."))
        domain = payload.get("domain", [])
        if not isinstance(domain, list) or len(domain) > 200:
            raise ValidationError(_("The pivot domain is too large."))
        company = self.env["res.company"].browse(payload.get("company_id")).exists()
        active_company_ids = {
            int(company_id)
            for company_id in (
                self.env.context.get("allowed_company_ids")
                or self.env.companies.ids
            )
        }
        if (
            not company
            or company.id not in active_company_ids
            or company not in self.env.user.company_ids
        ):
            raise AccessError(_("You cannot export this company’s analytic data."))
        context = payload.get("context", {})
        if not isinstance(context, dict):
            raise ValidationError(_("The pivot context must be an object."))
        safe_context = {
            key: context[key]
            for key in ("lang", "tz")
            if isinstance(context.get(key), str)
        }
        order = payload.get("order") or {}
        if not isinstance(order, dict) or set(order) - {"measure", "direction"}:
            raise ValidationError(_("The pivot ordering is invalid."))
        if order and (
            order.get("measure") not in measures
            or order.get("direction") not in {"asc", "desc"}
        ):
            raise ValidationError(_("The pivot ordering is invalid."))
        return {
            "row_axes": row_axes,
            "column_axes": column_axes,
            "measures": measures,
            "domain": [*domain, ("company_id", "=", company.id)],
            "context": {**safe_context, "allowed_company_ids": [company.id]},
            "order": order,
            "company": company,
        }

    @staticmethod
    def _usl_pivot_identity(value):
        if isinstance(value, BaseModel):
            return (value._name, tuple(value.ids))
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, list):
            return tuple(value)
        return value

    @staticmethod
    def _usl_pivot_label(value):
        if isinstance(value, BaseModel):
            return value.display_name or _("Not specified")
        if isinstance(value, (date, datetime)):
            return value.strftime("%d/%m/%Y")
        if value in (None, False, ""):
            return _("Not specified")
        return str(value)

    @api.model
    def _usl_pivot_group_data(self, model, domain, groupbys, measures):
        aggregates = [
            "__count" if measure == "__count" else f"{measure}:sum"
            for measure in measures
        ]
        return model._read_group(
            domain=domain,
            groupby=groupbys,
            aggregates=aggregates,
        )

    @api.model
    def _usl_analytic_pivot_document(self, payload):
        request_values = self._usl_validate_pivot_request(payload)
        company = request_values["company"]
        model = self.with_context(**request_values["context"]).with_company(company)
        row_axes = request_values["row_axes"]
        column_axes = request_values["column_axes"]
        measures = request_values["measures"]
        groupbys = [*row_axes, *column_axes]
        grouped = self._usl_pivot_group_data(
            model,
            request_values["domain"],
            groupbys,
            measures,
        )
        row_total_groups = self._usl_pivot_group_data(
            model,
            request_values["domain"],
            row_axes,
            measures,
        )
        grand_total = self._usl_pivot_group_data(
            model,
            request_values["domain"],
            [],
            measures,
        )[0]

        row_count = len(row_axes)
        column_count = len(column_axes)
        matrix = {}
        row_labels = {}
        column_labels = {}
        for item in grouped:
            row_values = item[:row_count]
            column_values = item[row_count:row_count + column_count]
            amounts = item[row_count + column_count:]
            row_key = tuple(self._usl_pivot_identity(value) for value in row_values)
            column_key = tuple(
                self._usl_pivot_identity(value) for value in column_values
            )
            row_labels[row_key] = tuple(
                self._usl_pivot_label(value) for value in row_values
            )
            column_labels[column_key] = tuple(
                self._usl_pivot_label(value) for value in column_values
            )
            matrix[row_key, column_key] = amounts
        row_totals = {}
        for item in row_total_groups:
            row_values = item[:row_count]
            row_key = tuple(self._usl_pivot_identity(value) for value in row_values)
            row_labels.setdefault(row_key, tuple(
                self._usl_pivot_label(value) for value in row_values
            ))
            row_totals[row_key] = item[row_count:]
        if not row_axes:
            row_labels[()] = (_("Total"),)
            row_totals[()] = grand_total
        if not column_axes:
            column_labels[()] = ()

        measure_labels = {
            "__count": _("Count"),
            **{
                name: model._fields[name].string
                for name in measures
                if name != "__count"
            },
        }
        sorted_rows = sorted(row_labels, key=lambda key: row_labels[key])
        order = request_values["order"]
        if order:
            measure_index = measures.index(order["measure"])
            sorted_rows.sort(
                key=lambda key: row_totals.get(key, [0] * len(measures))[measure_index] or 0,
                reverse=order["direction"] == "desc",
            )
        sorted_columns = sorted(column_labels, key=lambda key: column_labels[key])

        semantic_columns = [{
            "key": "label",
            "label": " › ".join(
                model._fields[axis.split(":", 1)[0]].string
                for axis in row_axes
            ) or _("Analysis"),
            "kind": "label",
        }]
        column_specs = []
        sequence = 0
        for column_key in sorted_columns:
            column_title = " › ".join(column_labels[column_key])
            for measure_index, measure in enumerate(measures):
                sequence += 1
                key = f"value_{sequence}"
                label = " · ".join(
                    part for part in (column_title, measure_labels[measure]) if part
                )
                semantic_columns.append({
                    "key": key,
                    "label": label,
                    "kind": "quantity" if measure == "__count" else "amount",
                })
                column_specs.append((key, column_key, measure_index))
        for measure_index, measure in enumerate(measures):
            sequence += 1
            key = f"value_{sequence}"
            semantic_columns.append({
                "key": key,
                "label": _("Total · %s", measure_labels[measure]),
                "kind": "quantity" if measure == "__count" else "amount",
            })
            column_specs.append((key, None, measure_index))
        if len(semantic_columns) > 30:
            raise UserError(_(
                "This pivot is too wide for an official PDF. Reduce the periods or measures to 29 value columns.",
            ))

        def display(value, measure):
            if value in (None, False):
                return ""
            if measure == "__count":
                return str(int(value))
            return f"{value:,.2f}".replace(",", " ").replace(".", ",")

        rows = []
        for row_key in sorted_rows:
            values = {"label": " › ".join(row_labels[row_key])}
            for key, column_key, measure_index in column_specs:
                source = (
                    row_totals.get(row_key, ())
                    if column_key is None
                    else matrix.get((row_key, column_key), ())
                )
                measure = measures[measure_index]
                values[key] = display(
                    source[measure_index] if len(source) > measure_index else None,
                    measure,
                )
            rows.append({"role": "detail", "values": values})
        total_values = {"label": _("Grand total")}
        for key, column_key, measure_index in column_specs:
            if column_key is None:
                source_value = grand_total[measure_index]
            else:
                source_value = sum(
                    (
                        matrix.get((row_key, column_key), [0] * len(measures))[measure_index]
                        or 0
                    )
                    for row_key in sorted_rows
                )
            total_values[key] = display(source_value, measures[measure_index])
        rows.append({"role": "total", "values": total_values})

        locale = (
            "fr_FR"
            if company.country_code == "FR"
            or (company.partner_id.lang or "").startswith("fr")
            else "en_US"
        )
        company_payload, assets = company._usl_document_renderer_company_payload(locale)
        template = self.env.ref(
            "usl_document_templates.template_accounting_statement_v2",
        )
        return self.env["usl.document.renderer"].render(
            template,
            company_payload,
            {
                "title": _("Analytic analysis"),
                "reference": _("Current pivot state"),
                "date": _("Generated on demand"),
                "layout_variant": "pivot",
                "orientation": "landscape",
                "context": [
                    _("Company: %s", company.display_name),
                    _("Rows: %s", ", ".join(row_axes) or _("None")),
                    _("Columns: %s", ", ".join(column_axes) or _("None")),
                    _("Measures: %s", ", ".join(measure_labels[item] for item in measures)),
                ],
                "columns": semantic_columns,
                "sections": [{
                    "key": "pivot",
                    "title": _("Analytic matrix"),
                    "continuation_label": _("Analytic matrix — continued"),
                    "rows": rows or [{
                        "role": "empty",
                        "values": {
                            column["key"]: (
                                _("No data for the selected scope.")
                                if column["key"] == "label"
                                else ""
                            )
                            for column in semantic_columns
                        },
                    }],
                }],
                "controls": [],
                "basis_note": _(
                    "Snapshot recomputed by Odoo from the selected axes, measures, domain, and active company access rules.",
                ),
            },
            locale,
            assets,
        )
