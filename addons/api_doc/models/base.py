from odoo import api, models


class Base(models.AbstractModel):
    _inherit = "base"

    @api.model
    def _api_doc_access(self):
        """Return caller-specific model access advertised by API discovery."""
        return {
            operation: self.has_access(operation)
            for operation in ("read", "create", "write", "unlink")
        }

    @api.model
    def _api_doc_public_method_allowed(self, method_name):
        """Hook for distributions that constrain otherwise-public JSON-2 methods."""
        return True

    @api.model
    def _api_doc_cache_vary(self):
        """Return bounded caller policy state that must invalidate API documents."""
        return ()
