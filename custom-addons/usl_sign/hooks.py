def post_init_hook(env):
    """Apply the product delivery default to companies present at installation."""
    env["res.company"].search([]).write(
        {
            "sign_oca_send_sign_request_copy": True,
            "sign_opentimestamps_enabled": True,
        },
    )
    for company in env["res.company"].search([]):
        env["usl.sign.service.health"]._ensure_company(company)
