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
