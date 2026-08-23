# ruff: noqa: EM101, F821, T201

import json
import os


def fail(message):
    raise RuntimeError(message)


def operational_snapshot():
    return {
        "employees": env["hr.employee"].sudo().with_context(
            active_test=False,
        ).search_count([]),
        "profiles": env["usl.tese.profile"].sudo().with_context(
            active_test=False,
        ).search_count([]),
        "payslips": env["usl.tese.payslip"].sudo().search_count([]),
        "moves": env["account.move"].sudo().search_count([]),
        "move_lines": env["account.move.line"].sudo().search_count([]),
        "attachments": env["ir.attachment"].sudo().search_count([]),
    }


if os.environ.get("USL_TESE_RECOVER_PARTIAL_RERUN") != "1":
    fail("USL_TESE_RECOVER_PARTIAL_RERUN=1 is required.")

Run = env["usl.tese.restore.run"].sudo()
run = Run.search([], order="id desc", limit=1)
if not run or run.status != "partial":
    fail("The latest TESE restoration is not a partial run.")

mappings = env["usl.tese.restore.mapping"].sudo().search([
    ("last_run_id", "=", run.id),
])
allowed_targets = {
    "hr.version",
    "mail.followers",
    "mail.message",
    "mail.tracking.value",
    "res.partner",
}
unexpected = mappings.filtered(
    lambda mapping: mapping.target_model not in allowed_targets,
)
if unexpected:
    fail(
        "The partial run touched unsupported targets: "
        f"{sorted(set(unexpected.mapped('target_model')))}.",
    )


def mapped(target_model):
    target_ids = mappings.filtered(
        lambda mapping: mapping.target_model == target_model,
    ).mapped("target_id")
    return env[target_model].sudo().browse(target_ids).exists()


versions = mapped("hr.version")
messages = mapped("mail.message")
tracking = mapped("mail.tracking.value")
followers = mapped("mail.followers")
partners = mapped("res.partner").filtered(
    lambda partner: partner.create_date >= run.started_at,
)

if versions.filtered("employee_id"):
    fail("The partial run mapped an operational employee version.")
if messages.filtered(
    lambda message: message.model != "hr.version" or message.res_id not in versions.ids,
):
    fail("The partial run mapped chatter outside its isolated HR versions.")
if tracking.filtered(lambda item: item.mail_message_id not in messages):
    fail("The partial run mapped tracking outside its isolated chatter.")
if followers.filtered(
    lambda follower: (
        follower.res_model != "hr.version"
        or follower.res_id not in versions.ids
    ),
):
    fail("The partial run mapped followers outside its isolated HR versions.")

protected_partner_ids = set(
    env["res.company"].sudo().with_context(active_test=False).search([])
    .mapped("partner_id").ids,
) | set(
    env["res.users"].sudo().with_context(active_test=False).search([])
    .mapped("partner_id").ids,
) | set(
    env["hr.employee"].sudo().with_context(active_test=False).search([])
    .mapped("work_contact_id").ids,
) | set(
    env["usl.tese.payslip"].sudo().search([])
    .mapped("collector_partner_id").ids,
)
if protected_partner_ids & set(partners.ids):
    fail("The partial run mapped a protected operational partner.")

migration_module_names = {"usl_accounting_restore", "usl_tese_restore"}
migration_modules = env["ir.module.module"].sudo().search([
    ("name", "in", sorted(migration_module_names)),
])
installed_module_names = set(
    migration_modules.filtered(lambda module: module.state == "installed").mapped("name"),
)
if installed_module_names != migration_module_names:
    fail(
        "The expected partial-run modules are not both installed: "
        f"{sorted(installed_module_names)}.",
    )

operational_before = operational_snapshot()
removed = {
    "versions": len(versions),
    "messages": len(messages),
    "tracking": len(tracking),
    "followers": len(followers),
    "partners": len(partners),
}

followers.unlink()
tracking.unlink()
messages.unlink()
versions.unlink()
partners.unlink()

operational_after_records = operational_snapshot()
if operational_after_records != operational_before:
    fail(
        "Operational facts changed during partial-run recovery: "
        f"{operational_before} -> {operational_after_records}.",
    )

migration_modules.button_immediate_uninstall()
active_migration_modules = env["ir.module.module"].sudo().search([
    ("name", "in", sorted(migration_module_names)),
    ("state", "not in", ["uninstalled", "uninstallable"]),
])
if active_migration_modules:
    fail(
        "Temporary migration modules remain active after recovery: "
        f"{sorted(active_migration_modules.mapped('name'))}.",
    )

operational_after = operational_snapshot()
if operational_after != operational_before:
    fail(
        "Operational facts changed while removing partial-run modules: "
        f"{operational_before} -> {operational_after}.",
    )

print(json.dumps({
    "migration_modules": "uninstalled",
    "operational_before": operational_before,
    "operational_after": operational_after,
    "removed_partial_records": removed,
}, indent=2, sort_keys=True))
