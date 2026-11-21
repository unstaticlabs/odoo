"""Fill the generic Link from legacy GitHub properties that are still unmigrated."""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    migrated = env["project.task"]._usl_migrate_github_references()
    _logger.info("usl_project: copied %s legacy GitHub property value(s) into the task Link", migrated)
