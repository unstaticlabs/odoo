from odoo import http
from odoo.http import request

from ..models.api_doc_scope import api_doc_index_scope
from odoo.addons.api_doc.controllers.api_doc import DocController


class UslDocController(DocController):
    @http.route()
    def doc_bearer_index(self):
        # ``doc_index`` rebuilds the whole index when the client sends
        # ``Cache-Control: no-cache``. A rebuild takes minutes and hundreds of
        # thousands of queries, and four of them fill every web worker. Node's
        # fetch adds that directive to every conditional request, so each MCP
        # revalidation forced one. The stored index is keyed by registry
        # sequence, groups and Agent policy, so a bearer client never needs to
        # force a rebuild: drop the directive and let the ETag answer.
        request.httprequest.environ.pop("HTTP_CACHE_CONTROL", None)
        return super().doc_bearer_index()

    def _doc_index(self):
        with api_doc_index_scope():
            return super()._doc_index()
