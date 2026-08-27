# ruff: noqa: EM101, F821, T201
import json

TABLES = (
    "usl_collaboration_restore_mapping",
    "usl_collaboration_restore_run",
)


def business_counts():
    return {
        "messages": env["mail.message"].sudo().search_count([]),
        "tracking": env["mail.tracking.value"].sudo().search_count([]),
        "followers": env["mail.followers"].sudo().search_count([]),
        "activities": env["mail.activity"].sudo().with_context(active_test=False).search_count([]),
        "channels": env["discuss.channel"].sudo().with_context(active_test=False).search_count([]),
        "channel_members": env["discuss.channel.member"].sudo().search_count([]),
        "documents": env["usl.document"].sudo().with_context(active_test=False).search_count([]),
        "declarations": env["rebuild.account.declaration"].sudo().search_count([]),
        "declaration_rules": env["rebuild.account.declaration.rule"].sudo().with_context(active_test=False).search_count([]),
        "aliases": env["mail.alias"].sudo().search_count([]),
        "reactions": env["mail.message.reaction"].sudo().search_count([]),
        "link_previews": env["mail.message.link.preview"].sudo().search_count([]),
        "subtypes": env["mail.message.subtype"].sudo().search_count([]),
    }


module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_collaboration_restore")], limit=1,
)
run = env["usl.collaboration.restore.run"].sudo().search([], limit=1)
if not module or module.state != "installed" or not run or run.status != "passed":
    raise RuntimeError("A passed Collaboration restore is required before finalization")
before = business_counts()
module.button_immediate_uninstall()
env.cr.commit()
for table in TABLES:
    env.cr.execute(f'DROP TABLE IF EXISTS "{table}"')
env.cr.commit()
after = business_counts()
if after != before:
    raise RuntimeError(f"Collaboration business records changed during finalization: {before} -> {after}")
print(json.dumps({"migration_module": "uninstalled", "business_counts": after}, indent=2, sort_keys=True))
