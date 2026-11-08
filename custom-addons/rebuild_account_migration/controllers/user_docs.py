"""Compatibility import: the user-guide viewer now lives in ``usl_docs``.

The route is unchanged. Restore-stage tests and any script written against the
old module path keep working through these names; new code imports
``odoo.addons.usl_docs.controllers.user_docs`` directly.
"""

from odoo.addons.usl_docs.controllers.user_docs import (  # noqa: F401
    DOCS_ENV_VAR,
    DOCS_ROUTE,
    _docs_root,
    _safe_doc_path,
    render_markdown,
)
