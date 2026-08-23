from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        result = super().session_info()
        company_payload = result.get("user_companies")
        if not company_payload:
            return result

        payloads = (
            company_payload.get("allowed_companies", {}),
            company_payload.get("disallowed_ancestor_companies", {}),
        )
        company_ids = {
            company_id
            for payload in payloads
            for company_id in payload
        }
        colors = {
            company.id: company._get_usl_ui_theme_color()
            for company in self.env["res.company"].sudo().browse(company_ids).exists()
        }
        for payload in payloads:
            for company_id, values in payload.items():
                values["usl_ui_theme_color"] = colors.get(company_id)
        return result
