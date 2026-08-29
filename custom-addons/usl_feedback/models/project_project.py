from odoo import fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    usl_feedback_project = fields.Boolean(
        string="Product Feedback Project",
        default=False,
        copy=False,
        index=True,
        help="Identifies the governed Project used for private product feedback.",
    )
