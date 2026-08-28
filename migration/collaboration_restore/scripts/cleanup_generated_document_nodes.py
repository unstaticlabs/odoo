"""Remove obsolete PDF roots created for retired Documents folders and URLs."""

documents = env["usl.document"].sudo().search(
    [
        ("source", "=", "odoo_generated"),
        ("name", "like", "Legacy Documents %"),
    ],
    order="id",
)
if documents.sudo().link_ids.filtered("active"):
    raise RuntimeError(
        "A generated legacy Documents node has an active business relationship",
    )

paperless_ids = documents.filtered(
    lambda document: document.availability_state != "permanently_deleted",
).mapped("paperless_id")
client = env["usl.document"]._paperless()
for document in documents.filtered(
    lambda candidate: candidate.availability_state == "available",
):
    client.trash_document(document.paperless_id)
if paperless_ids:
    client.permanently_delete_trashed_documents(paperless_ids)

document_ids = documents.ids
messages = env["mail.message"].sudo().search(
    [("model", "=", "usl.document"), ("res_id", "in", document_ids)],
)
message_ids = messages.ids
env["mail.followers"].sudo().search(
    [("res_model", "=", "usl.document"), ("res_id", "in", document_ids)],
).unlink()
if "usl.collaboration.restore.mapping" in env:
    env["usl.collaboration.restore.mapping"].sudo().search(
        [
            "|",
            "&", ("target_model", "=", "usl.document"),
            ("target_id", "in", document_ids),
            "&", ("target_model", "=", "mail.message"),
            ("target_id", "in", message_ids),
        ],
    ).unlink()
messages.unlink()
documents.unlink()
env.cr.commit()

remaining = env["usl.document"].sudo().search_count(
    [
        ("source", "=", "odoo_generated"),
        ("name", "like", "Legacy Documents %"),
    ],
)
if remaining:
    raise RuntimeError(f"{remaining} generated legacy Documents nodes remain")
print(f"COLLABORATION_GENERATED_DOCUMENT_NODES_REMOVED={len(document_ids)}")
