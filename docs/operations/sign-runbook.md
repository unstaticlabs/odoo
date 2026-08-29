# Sign operations

USL Sign is part of the unified Odoo/Paperless/Pocket ID runtime. It has no
standalone QA stack. Use `migration/manage` for reconstructed runtime identity
and lifecycle operations.

## Runtime checks

Before reviewing or operating Sign:

```bash
migration/manage qa status --runtime <runtime-id>
```

Confirm that Odoo, Pocket ID, Paperless, the Sign services, and the document
renderer are healthy; the recorded release identity matches the intended
runtime; and live external integrations remain disabled.

## Acceptance

For changed Sign behavior, verify:

- signer and company access in single- and multi-company mode;
- request, participant, field, and evidence state transitions;
- identity and strong-acceptance requirements;
- immutable signed originals, completion certificates, audit events, and
  Paperless links;
- cancellation, expiry, refusal, reminder, and retry behavior;
- renderer failure handling and PDF/A-3 dossier integrity;
- no unexplained pending, processing, or failed Sign work;
- identical restart and coordinated recovery behavior.

Browser QA is required for changed user-facing journeys. Use focused module
tests for unchanged workflows.

## Historical Online records

Completed Online requests are restored as **Odoo Online (External)** records,
not as native USL Sign ceremonies. Tokens and reusable signature images are
not imported. Signed PDFs, source documents, certificates, participants,
chatter, and sanitized audit history remain preserved evidence. See
[`migration/sign_restore`](../../migration/sign_restore/README.md).

## Production

Sign PostgreSQL and evidence, Step CA material, Odoo records, Paperless state,
and release identity form one coordinated cohort. Capture and independently
restore them through `migration/manage cohort`; do not copy or reset a Sign
component independently.

Keep production identity, ingress, and private key material in the deployment
secret store. Do not place them in migration configuration, Git, screenshots,
or evidence bundles. See [Production operations](production.md).
