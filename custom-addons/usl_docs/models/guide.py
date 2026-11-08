"""The guide for agents: search and read pages through the JSON-2 API.

An agent connected through MCP reaches public model methods with the generic
``odoo_call_method`` tool, so these two methods make the guide of the running
release readable without a browser and without a second server. They read
the same committed pages the viewer renders and never write anything.
"""

from odoo import api, models
from odoo.exceptions import AccessError

from ..tools import guide


class UslDocsGuide(models.AbstractModel):
    _name = "usl.docs.guide"
    _description = "USL user guide"

    def _root(self):
        if not self.env.user.has_group(guide.READER_GROUP):
            raise AccessError(self.env._("The user guide is available to internal users only."))
        return guide.docs_root()

    @api.model
    def search_pages(self, query, lang=None, limit=10):
        """Rank the guide's pages for ``query``.

        Returns ``[{path, url, title, type, description, lang, persona, app,
        score, snippet}]``, best match first, at most ``limit`` entries.
        """
        root = self._root()
        if not root:
            return []
        return guide.search_pages(root, query or "", lang=lang, limit=max(1, min(int(limit or 10), 50)))

    @api.model
    def read_page(self, path):
        """Return one page as data (``usl-docs-page/v1``): facts, Markdown, steps.

        ``path`` is the page's path under ``docs/users`` as ``index.json``
        lists it, for example ``how-to/process-expense.md``.
        """
        root = self._root()
        if not root:
            return {}
        resolved = guide.safe_doc_path(root, path or "")
        if not resolved:
            return {}
        page = guide.load_page(root, resolved)
        state = guide.evidence_state(page, guide.load_evidence(self.env), guide.release_commit(self.env))
        sidecar = guide.load_sidecar(root, page) if page.get("journey") else None
        return guide.page_json(page, state, sidecar)

    @api.model
    def list_pages(self):
        """Return the guide's index (``usl-docs-index/v1``) for the running release."""
        root = self._root()
        if not root:
            return {}
        return guide.index_json(
            guide.records(root), guide.load_evidence(self.env), guide.release_commit(self.env),
        )
