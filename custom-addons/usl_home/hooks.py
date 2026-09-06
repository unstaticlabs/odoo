from odoo import Command


def ensure_distribution_update_channel(env):
    """Keep release updates available to internal users after upgrades."""
    channel = env.ref("usl_home.channel_distribution_updates", raise_if_not_found=False)
    internal_group = env.ref("base.group_user")
    portal_group = env.ref("base.group_portal")
    if channel:
        channel.sudo().write(
            {
                "group_ids": [
                    Command.link(internal_group.id),
                    Command.unlink(portal_group.id),
                ],
            },
        )


def post_init_hook(env):
    """Make Home the initial action for every existing internal user once."""
    ensure_distribution_update_channel(env)
    home_action = env.ref("usl_home.action_usl_home")
    internal_users = env.ref("base.group_user").with_context(active_test=False).user_ids
    if "usl_identity_classification" in internal_users._fields:
        internal_users = internal_users.filtered(
            lambda user: user.usl_identity_classification != "agent",
        )
    internal_users.sudo().write({"action_id": home_action.id})
