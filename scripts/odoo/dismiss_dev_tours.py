import os

from odoo import Command

if os.getenv("USL_DISMISS_DEV_TOURS") != "1":
    message = "Refusing to dismiss tours without USL_DISMISS_DEV_TOURS=1."
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
tours = env["web_tour.tour"].sudo().search([])  # noqa: F821
if tours and users:
    links = [Command.link(user_id) for user_id in users.ids]
    for tour in tours:
        tour.write({"user_consumed_ids": links})
    tours.invalidate_recordset(["user_consumed_ids"])

expected_user_ids = set(users.ids)
incomplete_tours = tours.filtered(
    lambda tour: not expected_user_ids.issubset(tour.user_consumed_ids.ids),
)
if incomplete_tours:
    details = []
    for tour in incomplete_tours:
        missing_users = users.filtered(
            lambda user: user not in tour.user_consumed_ids,
        )
        details.append(
            f"{tour.name} ({', '.join(missing_users.mapped('login'))})",
        )
    message = (
        "Some tours were not marked as completed: "
        + ", ".join(details)
    )
    raise RuntimeError(message)

env.cr.commit()  # noqa: F821
print(  # noqa: T201
    f"Marked {len(tours)} tours as completed for "
    f"{len(users)} internal users in {database}.",
)
