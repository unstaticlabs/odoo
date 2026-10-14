"""Populate canonical references without deleting their legacy evidence."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["project.task"]._usl_migrate_github_references()
