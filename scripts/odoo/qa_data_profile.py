"""Persist whether this target contains complete source-derived QA data."""

import os

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: F821, T201

profile = os.environ.get("USL_QA_DATA_PROFILE", "full")
allowed = {"full", "no-documents", "documents-smoke", "clean-install", "home"}
if profile not in allowed:
    raise RuntimeError(f"Unsupported QA data profile: {profile}")

parameter = env["ir.config_parameter"].sudo()  # noqa: F821
key = "usl.qa.data_profile"
if profile == "full":
    parameter.search([("key", "=", key)]).unlink()
else:
    parameter.set_str(key, profile)
env.cr.commit()  # noqa: F821
print(f"QA data profile: {profile}")
