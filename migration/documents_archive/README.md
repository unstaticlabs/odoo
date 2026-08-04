# Odoo attachment to Paperless pilot

This boundary tool qualifies a small, explicit set of PDFs from a preserved
Odoo dump before any broader archive migration. It is intentionally outside the
normal add-ons path and never installs a migration module in the delivered
database.

The operator supplies a private selection file based on
`selection.example.json`. The runner:

- mounts the dump, filestore, extracted attachment manifest, and selection as
  read-only inputs;
- verifies the dump SHA-256 plus every selected binary's manifest size, SHA-1,
  path, MIME type, and PDF header;
- uploads through Odoo's supported asynchronous Paperless integration;
- preserves the original filename and records the legacy Odoo source identity
  in Paperless custom fields;
- applies explicit Odoo company, confidentiality, evidence, and link policy;
- verifies the received original byte-for-byte after archival, preview
  availability, object-permission synchronization, and the durable Odoo link;
- asserts that no additional binary was stored in Odoo.

The pilot is capped at ten documents. It is not the bulk migration procedure.
Selections and source binaries are private artifacts and must never be
committed.

Run against the isolated QA project only:

```bash
make documents-qa-build
make documents-qa-update
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
  make documents-qa-source-pilot \
  SELECTION=/absolute/path/to/private-selection.json
```

Re-running the same selection is safe: checksum reuse keeps one Paperless root
and one Odoo relationship per target record.
