from odoo import _, fields, models
from odoo.exceptions import UserError


class UslMailPdfCandidateWizard(models.TransientModel):
    _name = "usl.mail.pdf.candidate.wizard"
    _description = "Choose a linked PDF receipt"

    retrieval_id = fields.Many2one("usl.mail.pdf.retrieval", required=True, readonly=True)
    candidate_ids = fields.One2many(
        "usl.mail.pdf.candidate.wizard.line",
        "wizard_id",
        string="Possible receipt links",
    )

    @staticmethod
    def _display_label(label):
        words = ["PDF" if word.casefold() == "pdf" else word for word in label.split()]
        display = " ".join(words)
        return display[:1].upper() + display[1:]

    def _candidate_commands(self, retrieval):
        retrieval.ensure_one()
        retrieval._check_can_manage()
        candidates = retrieval._extract_candidates(retrieval.source_message_id)
        if not candidates:
            raise UserError(
                _("No receipt link remains in the source email. Attach the receipt manually.")
            )
        return [
            (
                0,
                0,
                {
                    "fingerprint": candidate["fingerprint"],
                    "label": self._display_label(candidate["label"]),
                    "hostname": candidate["hostname"],
                    "path_template": candidate["path_template"],
                    "score": candidate["score"],
                },
            )
            for candidate in candidates
        ]


class UslMailPdfCandidateWizardLine(models.TransientModel):
    _name = "usl.mail.pdf.candidate.wizard.line"
    _description = "Possible linked PDF receipt"
    _order = "score desc, id"

    wizard_id = fields.Many2one(
        "usl.mail.pdf.candidate.wizard",
        required=True,
        ondelete="cascade",
    )
    fingerprint = fields.Char(required=True, readonly=True)
    label = fields.Char(required=True, readonly=True)
    hostname = fields.Char(required=True, readonly=True)
    path_template = fields.Char(readonly=True)
    score = fields.Integer(readonly=True)

    def action_choose(self):
        self.ensure_one()
        retrieval = self.wizard_id.retrieval_id
        retrieval._check_can_manage()
        retrieval.action_select_candidate(self.fingerprint)
        return {"type": "ir.actions.client", "tag": "reload"}
