def post_init_hook(env):
    env["project.task.type"]._usl_initialize_reactivation_roles()
    env["project.task"]._usl_migrate_github_references()
