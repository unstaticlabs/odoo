# ruff: noqa: F821, T201

import json

from odoo.addons.usl_identity_restore.models.restore import (
    IdentitySourceReader,
    source_options,
)

source = IdentitySourceReader(source_options()).read()
run = env["usl.identity.restore.run"].sudo().search([], order="id desc", limit=1)
assert run and run.status == "passed", (
    "Identity restoration must pass before saved preferences are finalized."
)
dispositions = run.restore_preferences(source)
env.cr.commit()
print(
    "IDENTITY_PREFERENCES_RESULT="
    + json.dumps(dispositions, sort_keys=True),
)
