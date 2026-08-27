# ruff: noqa: F821, T201
"""Remove temporary Sign source bindings without changing business records."""

import json

module = "usl_sign_restore"
requests_before = env["sign.oca.request"].sudo().search_count(  # noqa: F821
    [("record_kind", "=", "external_archive")],
)
signers_before = env["sign.oca.request.signer"].sudo().search_count(  # noqa: F821
    [("state", "=", "external_recorded")],
)
bindings = env["ir.model.data"].sudo().search([("module", "=", module)])  # noqa: F821
removed = len(bindings)
bindings.unlink()
env.cr.commit()  # noqa: F821
requests_after = env["sign.oca.request"].sudo().search_count(  # noqa: F821
    [("record_kind", "=", "external_archive")],
)
signers_after = env["sign.oca.request.signer"].sudo().search_count(  # noqa: F821
    [("state", "=", "external_recorded")],
)
assert requests_before == requests_after == 8
assert signers_before == signers_after == 11
assert not env["ir.model.data"].sudo().search_count([("module", "=", module)])  # noqa: F821
print(
    "SIGN_RESTORE_FINALIZED="
    + json.dumps(
        {
            "business_requests": requests_after,
            "business_signers": signers_after,
            "removed_temporary_bindings": removed,
            "status": "passed",
        },
        sort_keys=True,
    ),
)
