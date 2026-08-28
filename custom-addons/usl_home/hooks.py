def post_init_hook(env):
    """Make Home the initial action for every existing internal user once."""
    home_action = env.ref("usl_home.action_usl_home")
    internal_users = env.ref("base.group_user").with_context(active_test=False).user_ids
    internal_users.sudo().write({"action_id": home_action.id})
