from uuid import UUID

from odoo import _, fields, models
from odoo.exceptions import AccessError, ValidationError

from .paperless_client import PaperlessNotFound


class PaperlessTag(models.Model):
    _inherit = "usl.paperless.tag"

    def action_review_classifier_rule(self):
        self.ensure_one()
        self.check_access("write")
        try:
            response = self._paperless().tag_score_rule(self.paperless_id)
        except PaperlessNotFound:
            raise ValidationError(_("The archive needs to be upgraded before setting a classifier rule.")) from None
        score = response["minimum_score"]
        wizard = self.env["usl.document.tag.rule.wizard"].create({
            "tag_id": self.id, "use_score": score is not None,
            "minimum_score": str(score) if score is not None else False,
        })
        return {
            "type": "ir.actions.act_window", "name": _("Classifier rule"),
            "res_model": wizard._name, "res_id": wizard.id, "view_mode": "form", "target": "new",
            "context": {"dialog_size": "medium"},
        }


class TagRuleWizard(models.TransientModel):
    _name = "usl.document.tag.rule.wizard"
    _description = "Classifier tag score rule"

    tag_id = fields.Many2one("usl.paperless.tag", required=True, readonly=True)
    use_score = fields.Boolean(string="Require a model score")
    minimum_score = fields.Char(string="Minimum score out of 100")

    def action_save(self):
        self.ensure_one()
        self.check_access("write")
        tag = self.tag_id
        tag.check_access("write")
        score = None
        if self.use_score:
            number = (self.minimum_score or "").strip()
            if not number.isascii() or not number.isdecimal() or len(number) > 3 or int(number) > 100:
                raise ValidationError(_("Enter a whole-number score from 0 to 100."))
            if tag.matching_algorithm != "6":
                raise ValidationError(_("Choose a tag that uses the learned classification method."))
            score = int(number)
        tag._paperless().set_tag_score_rule(tag.paperless_id, score)
        return {"type": "ir.actions.act_window_close"}


class Document(models.Model):
    _inherit = "usl.document"

    def classifier_tag_history(self):
        self.ensure_one()
        self.check_access("read")
        events = self._paperless().tag_decisions(self.paperless_id)["events"]
        tags = self.env["usl.paperless.tag"].with_context(active_test=False).search([
            ("paperless_id", "in", [event["tag_id"] for event in events]),
        ])
        accessible = {tag.paperless_id: tag for tag in tags}
        return [
            {"tag": accessible[event["tag_id"]].display_name,
             **{key: event[key] for key in ("batch", "score", "minimum_score", "status", "created")}}
            for event in events if event["tag_id"] in accessible
        ]

    def classifier_batch_documents(self, batch):
        self.ensure_one()
        if not any(event["batch"] == batch for event in self.classifier_tag_history()):
            return []
        if not self._api_doc_access().get("write"):
            return []
        events = self._paperless().tag_batch_documents(UUID(batch))["events"]
        visible_tags = self.env["usl.paperless.tag"].with_context(active_test=False).search([
            ("paperless_id", "in", [event["tag_id"] for event in events]),
        ])
        allowed_tags = set(visible_tags.mapped("paperless_id"))
        document_ids = {event["document_id"] for event in events if event["tag_id"] in allowed_tags}
        documents = self.search([
            ("paperless_id", "in", list(document_ids)), ("availability_state", "=", "available"),
        ], order="name, id")
        return [{"id": doc.id, "name": doc.display_name} for doc in documents if doc.has_access("write")]

    def undo_classifier_batch(self, batch):
        self.ensure_one()
        if not self._api_doc_access().get("write"):
            raise AccessError(_("Your current access does not allow this document change."))
        self.check_access("write")
        if self.availability_state != "available":
            raise ValidationError(_("Restore the document before changing its tags."))
        try:
            batch = UUID(str(batch))
        except (ValueError, TypeError) as error:
            raise ValidationError(_("Choose a batch from the document history.")) from error
        tags = self.env["usl.paperless.tag"].with_context(active_test=False).search([])
        client = self._paperless()
        result = client.undo_tag_decisions(self.paperless_id, batch, tags.mapped("paperless_id"))
        self._batch_refresh_archive_metadata(client.get_document(self.paperless_id))
        return result
