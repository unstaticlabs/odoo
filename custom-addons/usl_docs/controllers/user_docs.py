"""Serve the user guide inside the product.

Three readers share these routes:

- a person, who gets a rendered page with the guide's navigation
  (``views/templates.xml``), opened from the Help menu with the screen they
  were on, so the guide lands on the page about that screen;
- an agent, who asks for ``text/markdown`` (or ``?format=md``) and gets the
  page exactly as committed, ``?format=json`` for its facts, ``llms.txt`` and
  ``llms-full.txt`` as indexes, ``index.json`` as data and ``search.json`` to
  find a page;
- the pill on each page, which reads the docs evidence shipped with the
  release to say when a page's journey last passed, and the test record
  behind it.

Everything the routes show is computed by ``tools/guide.py``; this module
only decides which representation a request gets.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from odoo import http
from odoo.http import request

from ..tools import guide

DOCS_ROUTE = guide.DOCS_ROUTE
DOCS_ENV_VAR = guide.DOCS_ENV_VAR


def _wants(fmt):
    """The representation a request asks for: ``html``, ``md`` or ``json``."""
    requested = request.params.get("format")
    if requested in {"md", "json"}:
        return requested == fmt
    accept = request.httprequest.headers.get("Accept", "")
    first = accept.split(",")[0].split(";")[0].strip().lower()
    return {"md": first == "text/markdown", "json": first == "application/json"}.get(fmt, False)


def _json_response(payload, status=200):
    return request.make_response(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        headers=[("Content-Type", "application/json; charset=utf-8")],
        status=status,
    )


def _text_response(text, content_type="text/markdown; charset=utf-8"):
    return request.make_response(text, headers=[("Content-Type", content_type)])


class UserDocsController(http.Controller):
    def _root(self):
        if not request.env.user.has_group(guide.READER_GROUP):
            return None, request.not_found()
        root = guide.docs_root()
        if not root:
            return None, request.make_response(
                "USL user documentation is not available. Configure USL_USER_DOCS_PATH or mount docs/users.",
                headers=[("Content-Type", "text/plain; charset=utf-8")],
                status=503,
            )
        return root, None

    def _page_values(self, root, page, page_records, state, commit, **extra):
        """The values every rendered page shares: navigation, pills, links."""
        values = {
            "docs_route": DOCS_ROUTE,
            "page": page,
            "nav": guide.navigation(page_records, page["path"]),
            "state": state,
            "release_commit": commit,
            "type_label": guide.TYPE_LABELS.get(page["type"], ""),
            "persona_label": guide.PERSONA_LABELS.get(page["persona"]) if page.get("persona") else "",
            "translations": guide.translations(page_records, page) if page["type"] != "home" else [],
            "sources": [
                {"path": path, "name": path.rsplit("/", 1)[-1], "href": guide.source_url(path, commit)}
                for path in page.get("source", [])
            ],
            "outline": [
                {"level": level, "id": slug, "title": title}
                for level, slug, title in guide.headings(page.get("body", ""))
                if level == 2
            ] if page["type"] != "home" else [],
            "markdown_href": f"{guide.page_url(page['path'])}?format=md" if page["type"] != "home" else f"{DOCS_ROUTE}/llms.txt",
            "json_href": f"{guide.page_url(page['path'])}?format=json" if page["type"] != "home" else f"{DOCS_ROUTE}/index.json",
            "notice": "",
        }
        values.update(extra)
        return values

    @http.route(DOCS_ROUTE + "/evidence/<string:journey>", type="http", auth="user")
    def user_docs_evidence(self, journey, **kwargs):
        root, error = self._root()
        if error:
            return error
        evidence = guide.load_evidence(request.env)
        journeys = evidence.get("journeys") if isinstance(evidence, dict) and isinstance(evidence.get("journeys"), dict) else {}
        entry = journeys.get(journey)
        if not isinstance(entry, dict):
            return request.not_found()
        page_records = guide.records(root)
        page_record = next((record for record in page_records if record["journey"] == journey), None)
        commit = guide.release_commit(request.env)
        state = guide.evidence_state({"type": "how-to", "journey": journey}, evidence, commit)
        record = guide.test_record(
            journey, entry, evidence, state, page_record, guide.load_sidecar(root, page_record), commit,
        )
        if _wants("json"):
            return _json_response({"schema": "usl-docs-test-record/v1", **{k: v for k, v in record.items() if k not in {"steps"}}, "steps": [
                {k: v for k, v in step.items() if k != "html"} for step in record["steps"]
            ]})
        page = {
            "path": f"evidence/{journey}",
            "type": "home",
            "title": f"Test record: {record['title']}",
            "description": "",
            "lang": page_record["lang"] if page_record else "en",
            "persona": None,
            "journey": journey,
            "generated": False,
            "source": [],
            "screen": None,
            "body": "",
        }
        values = self._page_values(root, page, page_records, None, commit, record=record)
        return request.render("usl_docs.test_record", values)

    @http.route([DOCS_ROUTE, DOCS_ROUTE + "/", DOCS_ROUTE + "/<path:doc_path>"], type="http", auth="user")
    def user_docs(self, doc_path=None, model=None, action=None, xmlid=None, q=None, **kwargs):
        root, error = self._root()
        if error:
            return error
        base_url = request.httprequest.host_url.rstrip("/")
        special = (doc_path or "").strip("/")
        if special in {"llms.txt", "llms-full.txt", "index.json", "search.json"}:
            page_records = guide.records(root)
            if special == "llms.txt":
                return _text_response(guide.render_llms_txt(page_records, base_url))
            if special == "llms-full.txt":
                return _text_response(guide.render_llms_full(root, page_records, base_url))
            commit = guide.release_commit(request.env)
            if special == "index.json":
                return _json_response(guide.index_json(page_records, guide.load_evidence(request.env), commit, base_url))
            return _json_response({
                "schema": "usl-docs-search/v1",
                "query": q or "",
                "results": guide.search_pages(root, q or "", lang=kwargs.get("lang"), base_url=base_url),
            })
        asset = guide.safe_asset_path(root, doc_path)
        if asset:
            return request.make_response(
                asset.read_bytes(),
                headers=[
                    ("Content-Type", guide.IMAGE_TYPES[asset.suffix.lower()]),
                    ("Cache-Control", "private, max-age=3600"),
                ],
            )
        notice = ""
        if not special and (model or action):
            # The Help menu sends the screen the reader was on; land on the
            # page about it, or say plainly that none exists yet.
            page_records = guide.records(root)
            lang = (request.env.user.lang or "en")[:2]
            match = guide.page_for_context(page_records, model=model, action=[action, xmlid], lang=lang)
            if match:
                return request.redirect(guide.page_url(match["path"]), code=303)
            what = xmlid or action or model
            notice = f"No guide page is written for this screen yet ({what}). The pages below cover the rest of the product."
        path = guide.safe_doc_path(root, doc_path)
        if not path:
            return request.not_found()
        page = guide.load_page(root, path)
        if _wants("md"):
            return _text_response(page["text"])
        commit = guide.release_commit(request.env)
        state = guide.evidence_state(page, guide.load_evidence(request.env), commit)
        page_records = guide.records(root)
        if _wants("json"):
            sidecar = guide.load_sidecar(root, page) if page.get("journey") else None
            return _json_response(guide.page_json(page, state, sidecar, base_url))
        values = self._page_values(
            root, page, page_records, state, commit,
            body_html=guide.render_markdown(page["body"], page["path"]),
            notice=notice,
            search_href=f"{DOCS_ROUTE}/search.json",
        )
        return request.render("usl_docs.page", values)
