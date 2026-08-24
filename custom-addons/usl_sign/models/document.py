import base64
import hashlib
from io import BytesIO

from PyPDF2.generic import NameObject

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.pdf import PdfReader, PdfWriter

from .constants import MUTABLE_REQUEST_STATES


def _add_page(writer, page):
    method = getattr(writer, "add_page", None) or getattr(writer, "addPage")
    method(page)


def _inherited_page_value(page, key):
    """Resolve an inheritable PDF page attribute without trusting parser fallbacks."""
    node = page
    seen = set()
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        value = node.get(key)
        if value is not None:
            return value.get_object() if hasattr(value, "get_object") else value
        parent = node.get("/Parent")
        node = parent.get_object() if hasattr(parent, "get_object") else parent
    return None


def _normalized_page(page):
    """Materialize page-tree values before adding a page to a new PDF tree."""
    media_box = _inherited_page_value(page, "/MediaBox")
    try:
        coordinates = [float(value) for value in media_box]
    except (TypeError, ValueError):
        coordinates = []
    if (
        len(coordinates) != 4
        or coordinates[2] <= coordinates[0]
        or coordinates[3] <= coordinates[1]
    ):
        msg = "Every PDF page must define valid page dimensions."
        raise ValidationError(msg)
    for key in ("/Resources", "/MediaBox", "/CropBox", "/Rotate"):
        value = _inherited_page_value(page, key)
        if value is not None:
            page[NameObject(key)] = value
    return page


class SignRequestDocument(models.Model):
    _name = "usl.sign.request.document"
    _description = "Signature Request Source Document"
    _order = "sequence, id"

    request_id = fields.Many2one(
        "sign.oca.request", required=True, index=True, ondelete="cascade",
    )
    company_id = fields.Many2one(related="request_id.company_id", store=True, index=True)
    sequence = fields.Integer(default=10, required=True)
    is_annex = fields.Boolean(string="Annex")
    name = fields.Char(required=True)
    filename = fields.Char(required=True)
    data = fields.Binary(required=True, attachment=True)
    mimetype = fields.Char(default="application/pdf", required=True)
    source_sha256 = fields.Char(required=True, readonly=True, index=True)
    page_start = fields.Integer(readonly=True)
    page_end = fields.Integer(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        requests = self.env["sign.oca.request"].browse(
            [values.get("request_id") for values in vals_list if values.get("request_id")],
        )
        if not self.env.su:
            for request in requests:
                if not request._user_can_coordinate():
                    msg = "Only the requester or a named coordinator may add documents."
                    raise AccessError(msg)
        if requests.filtered(lambda request: request.state not in MUTABLE_REQUEST_STATES):
            msg = "Documents are frozen once a request is sent."
            raise ValidationError(msg)
        for values in vals_list:
            raw = base64.b64decode(values.get("data") or b"")
            self._validate_pdf(raw)
            values["source_sha256"] = hashlib.sha256(raw).hexdigest()
        return super().create(vals_list)

    def write(self, values):
        if not self.env.su:
            for document in self:
                if not document.request_id._user_can_coordinate():
                    msg = "Only the requester or a named coordinator may edit documents."
                    raise AccessError(msg)
        if self.filtered(lambda document: document.request_id.state not in MUTABLE_REQUEST_STATES):
            msg = "Documents are frozen once a request is sent."
            raise ValidationError(msg)
        if "data" in values:
            raw = base64.b64decode(values["data"] or b"")
            self._validate_pdf(raw)
            values["source_sha256"] = hashlib.sha256(raw).hexdigest()
        return super().write(values)

    def unlink(self):
        if not self.env.su:
            for document in self:
                if not document.request_id._user_can_coordinate():
                    msg = "Only the requester or a named coordinator may remove documents."
                    raise AccessError(msg)
        if self.filtered(lambda document: document.request_id.state not in MUTABLE_REQUEST_STATES):
            msg = "Documents are frozen once a request is sent."
            raise ValidationError(msg)
        return super().unlink()

    @staticmethod
    def _validate_pdf(raw):
        if not raw or not raw.startswith(b"%PDF-"):
            msg = "Every signing document must be a PDF."
            raise ValidationError(msg)
        try:
            reader = PdfReader(BytesIO(raw))
            if not reader.pages:
                msg = "empty"
                raise ValueError(msg)  # noqa: TRY301 - converted to the public PDF validation error below
            for page in reader.pages:
                _normalized_page(page)
        except ValidationError:
            raise
        except Exception as error:
            msg = "The signing document is not a readable PDF."
            raise ValidationError(msg) from error

    @api.model
    def consolidate(self, documents):
        documents = documents.sorted(lambda document: (document.sequence, document.id))
        if not documents:
            msg = "Add at least one PDF before preparing the request."
            raise ValidationError(msg)
        writer = PdfWriter()
        page_number = 1
        page_map = []
        for document in documents:
            raw = base64.b64decode(document.data)
            reader = PdfReader(BytesIO(raw))
            start = page_number
            for page in reader.pages:
                _add_page(writer, _normalized_page(page))
                page_number += 1
            end = page_number - 1
            page_map.append(
                {
                    "document_id": document.id,
                    "name": document.name,
                    "filename": document.filename,
                    "annex": document.is_annex,
                    "sha256": document.source_sha256,
                    "page_start": start,
                    "page_end": end,
                },
            )
        output = BytesIO()
        writer.write(output)
        return output.getvalue(), page_map


class SignTemplateDocument(models.Model):
    _name = "usl.sign.template.document"
    _description = "Reusable Signature Template Document"
    _order = "sequence, id"

    template_id = fields.Many2one(
        "sign.oca.template", required=True, index=True, ondelete="cascade",
    )
    company_id = fields.Many2one(related="template_id.company_id", store=True, index=True)
    sequence = fields.Integer(default=10, required=True)
    is_annex = fields.Boolean(string="Annex")
    name = fields.Char(required=True)
    filename = fields.Char(required=True)
    data = fields.Binary(required=True, attachment=True)
    source_sha256 = fields.Char(required=True, readonly=True, index=True)

    @api.model_create_multi
    def create(self, vals_list):
        templates = self.env["sign.oca.template"].browse(
            [values.get("template_id") for values in vals_list if values.get("template_id")],
        )
        if templates.filtered(
            lambda template: template.request_count or template.preparation_status == "ready",
        ):
            msg = "Published or used templates are immutable; create a new version."
            raise ValidationError(msg)
        for values in vals_list:
            raw = base64.b64decode(values.get("data") or b"")
            SignRequestDocument._validate_pdf(raw)
            values["source_sha256"] = hashlib.sha256(raw).hexdigest()
        return super().create(vals_list)

    def write(self, values):
        if self.filtered(
            lambda document: document.template_id.request_count
            or document.template_id.preparation_status == "ready",
        ):
            msg = "Published or used templates are immutable; create a new version."
            raise ValidationError(msg)
        if "data" in values:
            raw = base64.b64decode(values["data"] or b"")
            SignRequestDocument._validate_pdf(raw)
            values["source_sha256"] = hashlib.sha256(raw).hexdigest()
        return super().write(values)

    def unlink(self):
        if self.filtered(
            lambda document: document.template_id.request_count
            or document.template_id.preparation_status == "ready",
        ):
            msg = "Published or used templates are immutable; create a new version."
            raise ValidationError(msg)
        return super().unlink()
