"""Dry-run/apply the initial production cron and outbound-admission policy."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: EM101, F821, T201

import json
import os

if os.environ.get("USL_EINVOICE_LIVE_ENABLED", "") != "0" or os.environ.get(
    "USL_EREPORTING_LIVE_ENABLED",
    "",
) != "0":
    raise RuntimeError("Regulatory live flags must remain disabled at admission.")

try:
    allowlist = json.loads(os.environ["USL_PRODUCTION_CRON_ALLOWLIST_JSON"])
except (KeyError, json.JSONDecodeError) as error:
    raise RuntimeError("The production cron allowlist is missing or invalid.") from error
if not isinstance(allowlist, list) or any(
    not isinstance(value, str) or "." not in value for value in allowlist
):
    raise RuntimeError("The production cron allowlist must contain XML IDs only.")
if len(allowlist) != len(set(allowlist)):
    raise RuntimeError("The production cron allowlist contains duplicates.")

allowed = env["ir.cron"]  # noqa: F821
missing = []
for xmlid in allowlist:
    cron = env.ref(xmlid, raise_if_not_found=False)  # noqa: F821
    if not cron or cron._name != "ir.cron":
        missing.append(xmlid)
    else:
        allowed |= cron
if missing:
    raise RuntimeError(f"Approved cron XML IDs are missing: {sorted(missing)}")

Cron = env["ir.cron"].sudo()  # noqa: F821
currently_active = Cron.search([("active", "=", True)])
to_disable = currently_active - allowed
to_enable = allowed.filtered(lambda cron: not cron.active)
if os.environ.get("USL_PRODUCTION_ADMISSION_ASSERT_CONVERGED") == "1" and (
    to_disable or to_enable
):
    raise RuntimeError(
        "The applied production cron policy is not converged: "
        f"disable={to_disable.ids}, enable={to_enable.ids}",
    )
summary = {
    "approved": sorted(allowlist),
    "currently_active": sorted(currently_active.mapped("display_name")),
    "disable_count": len(to_disable),
    "enable_count": len(to_enable),
    "mode": "applied" if os.environ.get("USL_PRODUCTION_ADMISSION_APPLY") == "1" else "dry_run",
    "outbound_integrations": "disabled",
}
if os.environ.get("USL_PRODUCTION_ADMISSION_APPLY") == "1":
    to_disable.write({"active": False})
    to_enable.write({"active": True})
    env.cr.commit()  # noqa: F821
else:
    env.cr.rollback()  # noqa: F821
print(json.dumps(summary, indent=2, sort_keys=True))
