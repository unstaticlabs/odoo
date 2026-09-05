"""Record a clean development or QA worktree commit in Odoo."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: EM101, F821, T201

import os
import re

RELEASE_SHA_RE = re.compile(r"[0-9a-f]{40}")
SAFE_DEPLOYMENTS = {"development", "qa"}

deployment = os.environ.get("USL_DEPLOYMENT_ENV", "development").strip().lower()
if deployment not in SAFE_DEPLOYMENTS:
    raise RuntimeError("Worktree release identity is limited to development and QA.")
if os.environ.get("USL_EINVOICE_LIVE_ENABLED", "0") != "0" or os.environ.get(
    "USL_EREPORTING_LIVE_ENABLED",
    "0",
) != "0":
    raise RuntimeError("Worktree release identity requires both regulatory live flags to be 0.")

commit = os.environ.get("USL_WORKTREE_RELEASE_COMMIT", "").strip()
if commit and not RELEASE_SHA_RE.fullmatch(commit):
    raise RuntimeError("USL_WORKTREE_RELEASE_COMMIT must be an exact lowercase commit SHA.")

params = env["ir.config_parameter"].sudo()
params.set_str("usl.release.commit", commit or None)
env.cr.commit()
print(f"Worktree release identity: {commit or 'unverified'}")
