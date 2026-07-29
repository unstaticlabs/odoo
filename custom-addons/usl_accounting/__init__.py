from . import models


def post_init_hook(env):
    env["res.company"].search([])._ensure_immediate_settlement_journal()
