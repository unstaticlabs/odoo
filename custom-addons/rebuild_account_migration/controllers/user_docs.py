"""Compatibility import: the user-guide viewer now lives in ``usl_docs``.

The route is unchanged. Restore-stage tests and any script written against the
old module path keep working through these names; new code imports
``odoo.addons.usl_docs.tools.guide`` directly.
"""

from odoo.addons.usl_docs.tools.guide import (  # noqa: F401
    DOCS_ENV_VAR,
    DOCS_ROUTE,
    docs_root as _docs_root,
    render_markdown,
    safe_doc_path as _safe_doc_path,
)
