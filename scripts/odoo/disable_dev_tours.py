import os

if os.getenv("USL_DISABLE_DEV_TOURS") != "1":
    message = "Refusing to disable tours without USL_DISABLE_DEV_TOURS=1."
    raise RuntimeError(message)
if os.getenv("USL_EINVOICE_LIVE_ENABLED", "0") != "0":
    message = "Refusing to change tours while live e-invoicing is enabled."
    raise RuntimeError(message)
if os.getenv("USL_EREPORTING_LIVE_ENABLED", "0") != "0":
    message = "Refusing to change tours while live e-reporting is enabled."
    raise RuntimeError(message)

database = env.cr.dbname  # noqa: F821
if database == "odoo_online_source_saas_19_2":
    message = "Refusing to change the preserved source database."
    raise RuntimeError(message)

system_user = env.ref("base.user_root")  # noqa: F821
users = (
    env["res.users"]  # noqa: F821
    .sudo()
    .with_context(active_test=False)
    .search([])
    .filtered(
        lambda user: user != system_user and user._is_internal(),
    )
)
users.write({"tour_enabled": False})
users.invalidate_recordset(["tour_enabled"])

still_enabled = users.filtered("tour_enabled")
if still_enabled:
    message = (
        "Tours remain enabled for: "
        + ", ".join(still_enabled.mapped("login"))
    )
    raise RuntimeError(message)

env.cr.commit()  # noqa: F821
print(  # noqa: T201
    f"Disabled automatic tours for "
    f"{len(users)} internal users in {database}.",
)
