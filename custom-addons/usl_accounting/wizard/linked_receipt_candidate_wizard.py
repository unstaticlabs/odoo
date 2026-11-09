import hashlib
import re
from collections import Counter

from lxml import etree, html

from odoo import _, fields, models
from odoo.exceptions import UserError

from ..models.linked_receipt import MAX_DISCOVERED_LINKS, OPAQUE_TOKEN_RE


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

    def _bill_display_labels(self, retrieval, candidates):
        """Use readable source copy only in the transient bill picker."""
        source_labels = {}
        try:
            root = html.fragment_fromstring(str(retrieval.source_message_id.body or ""), create_parent="div")
            for position, node in enumerate(root.iter("a")):
                if position >= MAX_DISCOVERED_LINKS:
                    break
                url = node.get("href")
                if url:
                    label = " ".join(node.text_content().split())
                    label = re.sub(r"https?://\S+", "", label, flags=re.IGNORECASE)
                    label = OPAQUE_TOKEN_RE.sub("{id}", label).strip()[:120]
                    source_labels.setdefault(hashlib.sha256(url.encode()).hexdigest(), label)
        except (ValueError, TypeError, etree.ParserError, etree.XMLSyntaxError):
            pass
        counts = Counter(source_labels.get(candidate["fingerprint"], "").casefold() for candidate in candidates)
        labels = {}
        for candidate in candidates:
            label = source_labels.get(candidate["fingerprint"], "")
            position = candidate["position"] + 1
            if not label:
                label = self.env._("Link %(position)s in source email", position=position)
            elif counts[label.casefold()] > 1:
                label = self.env._("%(label)s (link %(position)s in source email)", label=label, position=position)
            labels[candidate["fingerprint"]] = label
        return labels

    def _candidate_commands(self, retrieval):
        retrieval.ensure_one()
        retrieval._check_can_manage()
        candidates = retrieval._extract_candidates(retrieval.source_message_id)
        if not candidates:
            raise UserError(
                _("No receipt link remains in the source email. Attach the receipt manually."),
            )
        bill_labels = self._bill_display_labels(retrieval, candidates) if retrieval.move_id else {}
        return [
            (
                0,
                0,
                {
                    "fingerprint": candidate["fingerprint"],
                    "label": bill_labels.get(candidate["fingerprint"]) or self._display_label(candidate["label"]),
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
