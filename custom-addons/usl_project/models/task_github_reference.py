from urllib.parse import urlsplit

from odoo import fields, models


def _is_legacy_github_property(prop):
    return prop.get("type") == "char" and (prop.get("string") or "").strip().casefold() == "github"


def _legacy_github_reference(properties):
    """Copy only one explicit usable legacy URL; retain ambiguous evidence."""
    values = [prop.get("value") for prop in properties
              if _is_legacy_github_property(prop) and prop.get("value")]
    if len(values) != 1 or not isinstance(values[0], str):
        return False
    value = values[0].strip()
    try:
        url = urlsplit(value)
        url.port  # Validate malformed port syntax without narrowing valid URLs.
        valid = (url.scheme in {"http", "https"}
                 and url.hostname in {"github.com", "www.github.com"}
                 and not url.username and not url.password)
    except ValueError:
        return False
    return value if valid else False


class ProjectTask(models.Model):
    _inherit = "project.task"

    github_url = fields.Char(
        string="GitHub URL",
        help="The canonical GitHub issue, pull request or repository link for this task.",
    )

    def _usl_migrate_github_references(self):
        """Lifecycle-only transition; never rewrite legacy values or definitions."""
        Project = self.env["project.project"].with_context(active_test=False)
        Task = self.with_context(active_test=False)
        migrated = 0
        for project in Project.search([]):
            if not any(_is_legacy_github_property(prop)
                       for prop in project.task_properties_definition or []):
                continue
            last_id = 0
            while tasks := Task.search([
                ("project_id", "=", project.id), ("github_url", "=", False), ("id", ">", last_id),
            ], order="id", limit=500):
                last_id = tasks[-1].id
                # Properties record values are mappings; native read format
                # includes the definition/type/label needed to identify GitHub.
                properties_by_id = {
                    row["id"]: row["task_properties"] for row in tasks.read(["task_properties"])
                }
                for task in tasks:
                    reference = _legacy_github_reference(properties_by_id[task.id])
                    if reference:
                        task.write({"github_url": reference})
                        migrated += 1
        return migrated
