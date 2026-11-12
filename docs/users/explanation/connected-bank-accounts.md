---
title: Connected bank accounts and imported transactions
type: explanation
description: How bank access, account identity, booked transactions and reported balances relate to native Accounting.
lang: en
persona: accountant
app: Accounting
models:
  - usl.enablebanking.connection
  - usl.enablebanking.account
  - account.bank.statement.line
actions:
  - usl_enablebanking.connection_action
screen: /odoo/action-usl_enablebanking.connection_action
source:
  - custom-addons/usl_enablebanking/models/connection.py
  - custom-addons/usl_enablebanking/models/account.py
  - custom-addons/usl_enablebanking/models/provider.py
generated: false
---

# Connected bank accounts and imported transactions

A bank connection authorizes Odoo to read the personal accounts that the bank
makes available through Enable Banking. Each discovered account maps to a
native bank journal in the same company and currency. Products the bank does
not expose through this service are unavailable here.

Own-account mode uses accounts linked to your Enable Banking application. The
bank still asks for a separate authorization. An Accounting manager configures
the application and renews access; an administrator mounts its signing key.
The key never belongs in an Odoo form. The bank's consent choice determines
which supported accounts become available.

Renewing access keeps existing account identities, journal choices and imported
history. An account absent from the new session is marked unavailable rather
than deleted. The access expiry date describes the bank connection, not the
validity of transactions already imported.

Only booked transactions become native bank lines. Their bank booking date,
currency and debit/credit direction are preserved. Importing a period again
uses stable bank references to keep existing entries. Two different entries
with the same date and amount remain two transactions.

If a bank response lacks a reliable transaction identity or contains conflicting
financial details, that account's automatic import stops with an explanation.
Its history and successful-import checkpoint stay in place; other accounts can
continue. The journal's native manual and file imports remain available. Those paths
have their own import identities, so an automatic import period starts after a
period already handled through another path unless exact source identities
have been reconciled. An existing transaction with an exact matching import
identity and financial facts can be linked as **Matched existing**, with its
original fields unchanged. The
source response needs checking before automatic import is retried.

Reported balances keep the bank's label, type, currency and reference time.
They are separate from the Odoo ledger balance. An available balance does not
certify a bank statement, and importing transactions does not reconcile them.
Native Bank Matching and ordinary accounting corrections remain available.

If balances cannot be refreshed, complete booked transactions can still import.
Odoo keeps the previous bank balances with their original dates and shows a
refresh warning. Retry later, or renew bank access when prompted. A failed
transaction batch keeps its import checkpoint and existing entries unchanged.
