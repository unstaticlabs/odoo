# Bank identity cut-over

This one-shot tool adopts only exact provider identities already stored in
`account.bank.statement.line.transaction_details.extra.id` and present in the
operator-supplied OFX. It verifies the OFX account plus every FITID, date and
amount before writing provider and OCA import identities. It never matches by
partner or label and never changes dates, amounts, statement membership,
journal entries, or reconciliation.

Run it from the repository-built Odoo image with the product database mounted:

```bash
USL_BANK_ADOPTION_MODE=preview \
USL_BANK_ADOPTION_CONFIG_ID=<configuration-id> \
USL_BANK_ADOPTION_OFX=/run/private/shine-cutover.ofx \
USL_BANK_ADOPTION_REPORT=/mnt/private/bank-adoption-preview.json \
odoo shell --config=/etc/odoo/odoo.conf --database=odoo_dev \
  < /mnt/migration/bank_statement_ingestion/adopt.py
```

Review the private report, repeat with `USL_BANK_ADOPTION_MODE=apply`, then run
preview again. The final preview must report `candidate_count: 0`; repeated
apply runs are idempotent. Apply mode commits only after the complete identity
population and the private report have been validated. A missing or duplicate
source FITID, a missing or duplicate migrated identity, a date/amount mismatch,
or an existing provider or OCA identity conflict aborts the entire run. The JSON
report stores the OFX checksum and hashed FITIDs, not raw provider identifiers.
Configure the route with processing paused before running this command.

The real OFX is private operational evidence. Mount it read-only into the
migration container (for example at `/run/private/shine-cutover.ofx`); never
copy it into the repository or normal product add-on path.
