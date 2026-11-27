from odoo import _, models
from odoo.exceptions import MissingError, ValidationError

from ..tools.project_notes import read_project_notes


class Project(models.Model):
    _inherit = "project.project"

    def get_team_notes(self, path=None):
        """Read the project's current note list and selected Markdown note."""
        self.ensure_one()
        self.check_access("read")
        if not self.exists():
            raise MissingError(_("This project is no longer available."))
        if path is not None and not isinstance(path, str):
            raise ValidationError(_("Choose a note from this project."))
        return read_project_notes(self.id, path, self.env.cr.dbname)
