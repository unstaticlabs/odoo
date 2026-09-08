import os
import re

from odoo import _, models

#: A release commit is a full git SHA; anything else is not trusted enough to
#: put on screen as an identifier.
RELEASE_COMMIT_RE = re.compile(r"[0-9a-f]{40}")

#: Where this Odoo's own user documentation is served, by the controller in
#: ``rebuild_account_migration``. Odoo points the user menu's Help entry at
#: ``session_info["support_url"]``, which upstream sets to its own pricing
#: page — an answer to a question nobody using this instance is asking.
USER_DOCS_URL = "/usl/user-docs"


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def webclient_rendering_context(self):
        result = super().webclient_rendering_context()
        # Read the flag here rather than in the helper: the sudo then sits in
        # the reviewed entry point, exactly as session_info's does, instead of
        # becoming a sink whose reachability has to be argued separately.
        neutralized = self.env["ir.config_parameter"].sudo().get_bool(
            "database.is_neutralized",
        )
        banner = self._usl_neutralize_banner_text(neutralized)
        if banner:
            result["neutralize_banner_text"] = banner
        return result

    def _usl_neutralize_banner_text(self, neutralized):
        """Name the running release on the neutralized-database banner.

        Every neutralized database carries the same red banner, so a tester
        looking at staging cannot tell which release they are looking at, and a
        bug report from one release reads exactly like a bug report from the
        next. The commit answers that without asking anyone to open a terminal.

        Production is not neutralized and disables this banner outright, so
        this can never put a release identifier in front of a customer.
        """
        if not neutralized:
            return ""
        commit = (os.getenv("USL_RELEASE_COMMIT") or "").strip().lower()
        if not RELEASE_COMMIT_RE.fullmatch(commit):
            # Say nothing rather than name a release we cannot vouch for; the
            # stock banner text still warns that the database is neutralized.
            return ""
        return _(
            "Database neutralized for testing: no emails sent, etc. — release %(commit)s",
            commit=commit[:12],
        )

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
