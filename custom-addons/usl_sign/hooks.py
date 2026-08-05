def post_init_hook(env):
    env["usl.sign.policy"]._ensure_company_defaults(env["res.company"].search([]))

