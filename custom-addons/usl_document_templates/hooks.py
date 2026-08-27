def post_init_hook(env):
    env["res.company"].with_context(active_test=False).search([]).write(
        {"usl_document_renderer_enabled": True}
    )

