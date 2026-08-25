"""Grant governed B2C QA personas and verify source-backed tour data."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: EM101, F821, T201

import os

from odoo import Command


def enabled(name):
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


if env.cr.dbname != "odoo_dev":
    raise RuntimeError("B2C QA bootstrap is reserved for odoo_dev")
if enabled("USL_EINVOICE_LIVE_ENABLED") or enabled("USL_EREPORTING_LIVE_ENABLED"):
    raise RuntimeError("B2C QA bootstrap requires both regulatory live guards to be off")

users = env["res.users"].sudo().with_context(active_test=False)
valentin = users.search([("login", "=", "valentin")])
prosper = users.search([("login", "=", "prosper")])
if len(valentin) != 1 or not valentin.active or valentin.share:
    raise RuntimeError("Expected one active internal Valentin QA user")
if len(prosper) != 1 or not prosper.active or prosper.share:
    raise RuntimeError("Expected one active internal Prosper QA user")

manager_group = env.ref("usl_b2c.group_b2c_manager")
reader_group = env.ref("usl_b2c.group_b2c_reader")
valentin.write({"group_ids": [Command.link(manager_group.id)]})
prosper.write({"group_ids": [Command.link(reader_group.id)]})

if not valentin.has_group("usl_b2c.group_b2c_manager"):
    raise RuntimeError("Valentin did not receive the B2C manager role")
if not prosper.has_group("usl_b2c.group_b2c_reader"):
    raise RuntimeError("Prosper did not receive the B2C reviewer role")
if prosper.has_group("usl_b2c.group_b2c_operator"):
    raise RuntimeError("Prosper must remain a read-only B2C reviewer")

profile = os.getenv("USL_QA_DATA_PROFILE", "full")
company = env["res.company"].sudo().search([("name", "=", "Unstatic Labs")], limit=2)
if len(company) != 1:
    raise RuntimeError("B2C QA requires one Unstatic Labs company")

domain = [("company_id", "=", company.id)]
counts = {
    "orders": env["b2c.order"].sudo().search_count(domain),
    "order_lines": env["b2c.order.line"].sudo().search_count(domain),
    "payment_events": env["b2c.payment.event"].sudo().search_count(domain),
    "fulfilments": env["b2c.fulfilment.event"].sudo().search_count(domain),
    "sessions": env["b2c.accounting.session"].sudo().search_count(domain),
}
if profile != "clean-install":
    expected = {
        "orders": 304,
        "order_lines": 235,
        "payment_events": 1821,
        "fulfilments": 261,
        "sessions": 80,
    }
    if counts != expected:
        raise RuntimeError(f"B2C QA tour baseline changed: {counts!r} != {expected!r}")

enabled_providers = env["payment.provider"].sudo().search([("state", "!=", "disabled")])
if enabled_providers:
    raise RuntimeError(
        "B2C QA requires every payment provider to remain disabled: "
        f"{enabled_providers.mapped('display_name')!r}",
    )

reader_orders = (
    env["b2c.order"]
    .with_user(prosper)
    .with_context(allowed_company_ids=company.ids)
)
if not reader_orders.has_access("read"):
    raise RuntimeError("Prosper cannot read the B2C QA tour")
for operation in ("write", "create", "unlink"):
    if reader_orders.has_access(operation):
        raise RuntimeError(f"Prosper unexpectedly has B2C {operation} access")

env.cr.commit()
print(
    "B2C QA personas ready: "
    "valentin=manager, prosper=read-only, enabled_payment_providers=0, "
    f"profile={profile}, counts={counts}",
)
