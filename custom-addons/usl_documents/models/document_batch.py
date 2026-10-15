"""Fixed per-document operations for the page-local metadata editor."""

from odoo import _, models
from odoo.exceptions import AccessError, ValidationError

from .paperless_client import PaperlessError


class UslDocument(models.Model):
    _inherit = "usl.document"

    def apply_batch_archive_metadata(self, operation, metadata_ids):
        """Apply one explicit selection; each document has its own RPC transaction.

        Remote writes cannot roll back with Odoo. A retry first reads the archive,
        so an acknowledged or uncertain prior request is never blindly replayed.
        """
        self.ensure_one()
        if not self._api_doc_access().get("write"):
            raise AccessError(_("Your current access does not allow this document change."))
        try:
            self.check_access("write")
        except AccessError as error:
            # Keep structured Agent policy denials, but do not expose native
            # record-rule diagnostics naming an inaccessible archive record.
            if type(error) is AccessError:
                raise AccessError(_("Your current access does not allow this document change.")) from None
            raise
        if self.availability_state != "available":
            raise ValidationError(
                _("Restore or repair this document before editing it."),
            )
        choices = {
            "add_tags": ("usl.paperless.tag", "tag_ids"),
            "set_correspondent": ("usl.paperless.correspondent", "correspondent_id"),
            "set_document_type": ("usl.paperless.document.type", "document_type_id"),
        }
        if not isinstance(operation, str) or operation not in choices:
            raise ValidationError(_("Choose a supported metadata change."))
        if (
            not isinstance(metadata_ids, list)
            or not metadata_ids
            or len(metadata_ids) > 100
            or any(type(item) is not int or item <= 0 for item in metadata_ids)
            or len(set(metadata_ids)) != len(metadata_ids)
            or (operation != "add_tags" and len(metadata_ids) != 1)
        ):
            raise ValidationError(_("Choose available metadata items."))
        model_name, field_name = choices[operation]
        items = self.env[model_name].search(
            [
                ("id", "in", metadata_ids),
                ("active", "=", True),
            ],
        )
        if set(items.ids) != set(metadata_ids):
            raise ValidationError(
                _("One or more selected metadata items are unavailable."),
            )
        items.check_access("read")
        try:
            remote = self._paperless().get_document(self.paperless_id)
        except PaperlessError:
            return {
                "status": "failed",
                "message": _(
                    "The archive could not be checked. No change was sent. Retry later.",
                ),
            }
        try:
            with self.env.cr.savepoint():
                self._batch_refresh_archive_metadata(remote)
        except (ValidationError, ValueError, TypeError, KeyError):
            return {
                "status": "failed",
                "message": _(
                    "The archive response could not be checked. No change was sent.",
                ),
            }
        values = {
            field_name: metadata_ids if operation == "add_tags" else metadata_ids[0],
        }
        try:
            with self.env.cr.savepoint():
                self.update_archive_metadata(values, add_tags=operation == "add_tags")
                confirmed = self._paperless().get_document(self.paperless_id)
                if confirmed.get("id") != self.paperless_id:
                    return {
                        "status": "uncertain",
                        "message": _(
                            "The result could not be confirmed. Retry to check the archive before applying again.",
                        ),
                    }
                remote_field = {
                    "add_tags": "tags",
                    "set_correspondent": "correspondent",
                    "set_document_type": "document_type",
                }[operation]
                reported = confirmed.get(remote_field)
                if operation == "add_tags":
                    reported_ids = {
                        int(item.get("id") if isinstance(item, dict) else item)
                        for item in reported or []
                    }
                    matches = set(items.mapped("paperless_id")).issubset(reported_ids)
                else:
                    reported_id = (
                        reported.get("id") if isinstance(reported, dict) else reported
                    )
                    matches = reported_id == items.paperless_id
                if not matches:
                    return {
                        "status": "uncertain",
                        "message": _(
                            "The result could not be confirmed. Retry to check the archive before applying again.",
                        ),
                    }
        except (
            PaperlessError,
            ValidationError,
            AccessError,
            ValueError,
            TypeError,
            KeyError,
        ):
            return {
                "status": "uncertain",
                "message": _(
                    "The result could not be confirmed. Retry to check the archive before applying again.",
                ),
            }
        return {"status": "completed", "message": _("Confirmed in the archive.")}

    def _batch_refresh_archive_metadata(self, remote):
        """Refresh only the authorized root; no company or privacy policy changes."""
        self.ensure_one()
        self.check_access("write")
        if not isinstance(remote, dict) or remote.get("id") != self.paperless_id:
            raise ValidationError(_("The archive returned a different document."))
        values = self._paperless_values(remote)
        metadata_fields = {
            "name", "document_date", "correspondent_id", "document_type_id",
            "tag_ids", "correspondent_name", "document_type_name", "paperless_modified",
        }
        values = {key: value for key, value in values.items() if key in metadata_fields}
        self.sudo().with_context(usl_documents_cache_write=True).write(values)
