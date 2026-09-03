from werkzeug.exceptions import NotFound

from odoo.http import request

from odoo.addons.mail.controllers.attachment import AttachmentController
from odoo.addons.mail.tools.discuss import mail_route
from odoo.addons.usl_documents.models.attachment_bridge import ORIGIN_CAPTURE_TOKEN


class DocumentsAttachmentController(AttachmentController):
    @mail_route()
    def mail_attachment_upload(
        self,
        ufile,
        thread_id,
        thread_model,
        is_pending=False,
        **kwargs,
    ):
        request.update_context(
            usl_documents_origin_token=ORIGIN_CAPTURE_TOKEN,
            usl_documents_attachment_origin=(
                "portal" if request.env.user.share else "chatter"
            ),
        )
        return super().mail_attachment_upload(
            ufile,
            thread_id,
            thread_model,
            is_pending,
            **kwargs,
        )

    @mail_route()
    def mail_attachement_update_thumbnail(
        self,
        attachment_id,
        thumbnail=None,
        access_token=None,
    ):
        attachment = request.env["ir.attachment"].browse(int(attachment_id)).exists()
        if (
            attachment
            and attachment.has_access("read")
            and not attachment.has_access("write")
            and not attachment._has_attachments_ownership([access_token])
        ):
            # Existing browser sessions may still request a thumbnail before
            # loading the attachment-level capability added by this module.
            # Treat that immutable write as a no-op without granting access or
            # changing the protected evidence.
            return False
        return super().mail_attachement_update_thumbnail(
            attachment_id,
            thumbnail=thumbnail,
            access_token=access_token,
        )

    @mail_route()
    def mail_attachment_delete(self, attachment_id, access_token=None):
        """Keep record removal inside the Documents lifecycle.

        The standard route marks the carrying message as edited. Notification
        messages cannot be edited, which caused record attachment removal to
        fail before the attachment was touched.
        """
        attachment = request.env["ir.attachment"].browse(int(attachment_id)).exists()
        if not attachment or not attachment._has_attachments_ownership([access_token]):
            request.env.user._bus_send(
                "ir.attachment/delete",
                {"id": attachment_id},
            )
            raise NotFound()
        supported = request.env["usl.document.link"]._allowed_models()
        if (
            attachment.res_model in supported
            and attachment.usl_documents_archive_mode
            in {"mandatory", "automatic", "on_request"}
        ):
            return attachment.action_remove_archived_from_record("unlink")

        message = request.env["mail.message"].sudo().search(
            [("attachment_ids", "in", attachment.ids)],
            limit=1,
        )
        if message and message.message_type == "comment":
            return super().mail_attachment_delete(
                attachment_id,
                access_token=access_token,
            )
        # Technical or explicitly excluded attachments have no Documents
        # lifecycle. Remove them without trying to edit a system notification.
        attachment.sudo()._delete_and_notify(message)
        return None
