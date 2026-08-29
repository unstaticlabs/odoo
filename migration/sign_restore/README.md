# Odoo Online Sign restoration

This one-shot stage preserves completed Online Sign records and their evidence
without presenting an Enterprise ceremony as a native USL Sign completion. It
is invoked only by `migration/manage` during full reconstruction.

Each source request becomes an immutable **Odoo Online (External)** record.
Signed PDFs, source documents, certificates, sanitized audit history, chatter,
participants, and attachments retain exact source identity and chronology.
Tokens and reusable signer marks are never imported.

Signed exports are matched by exact digest and size. Missing, extra, reused,
or changed evidence fails closed. Finalization removes temporary bindings and
the product-boundary gate rejects Enterprise Sign models, migration fields,
menus, and source-specific provenance.

Acceptance covers exact request, signer, event, message, field, and attachment
counts; idempotent replay; Paperless permissions and links; readable evidence;
and final registry cleanup. Private evidence remains under the runtime's
ignored `private/migration/` directory.

See [Migration operations](../../docs/operations/migration.md) for the public
command and lifecycle.
