# Enable Banking account information

This focused adapter reuses pinned OCA `account_statement_import_online`, its
native scheduler, journal mapping and bank-line import. It supports the
Fortuneo personal-account pilot and discovers all account products returned by
the bank. No payment API is implemented.

See [the financial contract](../../docs/accounting/enablebanking-import.md) for
identity, multiplicity, date/currency mapping, atomicity and the precise
missing-reference limitation. The public directory lists Fortuneo personal AIS;
no live account consent or real Fortuneo transaction payload was used in QA.

The company application ID is configured on the connection. Its signing key is
read from the file named by `USL_ENABLEBANKING_KEY_FILE_<company-id>`. The
existing deployment secret mechanism must mount that file read-only for Odoo.
The private key is never stored in the database or entered in a form. The
connection displays the exact public HTTPS callback URL that the human must
register with Enable Banking. Deployment secret wiring and human activation
are separate from installing this addon.

Normal Accounting and company permissions apply equally to human and delegated
Agent identities. Automated tests mock every provider request, use disposable
synthetic companies and create actual native bank lines.
