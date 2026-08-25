# Bank identity cut-over

This one-shot tool adopts only exact provider identities already stored in
`account.bank.statement.line.transaction_details.extra.id`. It never matches
by date, amount, partner, or label and never changes dates, amounts, statement
membership, journal entries, or reconciliation.

Run it from the repository-built Odoo image with the product database mounted:

```bash
USL_BANK_ADOPTION_MODE=preview \
USL_BANK_ADOPTION_CONFIG_ID=<configuration-id> \
USL_BANK_ADOPTION_REPORT=/mnt/private/bank-adoption-preview.json \
odoo shell --config=/etc/odoo/odoo.conf --database=odoo_dev \
  < /mnt/migration/bank_statement_ingestion/adopt.py
```

Review the private report, repeat with `USL_BANK_ADOPTION_MODE=apply`, then run
preview again. The final preview must report `candidate_count: 0`; repeated
apply runs are idempotent. Duplicate source IDs or any existing identity that
points to different provider facts abort the entire run. Configure the route
with processing paused before running this command.
