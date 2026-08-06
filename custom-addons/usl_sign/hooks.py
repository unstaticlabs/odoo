def post_init_hook(env):
    """Apply the product delivery default to companies present at installation."""
    env["res.company"].search([]).write({"sign_oca_send_sign_request_copy": True})
