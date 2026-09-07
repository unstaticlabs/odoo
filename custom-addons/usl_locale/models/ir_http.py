from odoo import models

#: Where this Odoo's own user documentation is served, by the controller in
#: ``rebuild_account_migration``. Odoo points the user menu's Help entry at
#: ``session_info["support_url"]``, which upstream sets to its own pricing
#: page — an answer to a question nobody using this instance is asking.
USER_DOCS_URL = "/usl/user-docs"


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        result = super().session_info()
        result["support_url"] = USER_DOCS_URL
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
