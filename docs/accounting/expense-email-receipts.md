# Receipt emails on expenses

An incoming Google Play receipt can contain both the purchase confirmation and
an attachment containing generic terms of use. Identical terms do not establish
that two expenses concern the same purchase.

The expense identifies a receipt only when the readable email body names Google
Play and contains an English or French order-number label with one complete
transaction reference. Renewal suffixes remain part of that reference. Conflicting
references or references split across markup are left for manual review. Filenames,
links and hidden scripts do not establish receipt identity.

Content-confirmed generic terms remain attached in their original form. They are
excluded from automatic main-receipt selection and same-receipt warnings. Unknown
files retain the native behavior. An explicitly selected genuine replacement
receipt remains authoritative; existing email evidence is retained.

For an incoming draft or submitted expense without an existing receipt, Odoo can
render the confirmation into a PDF and select it as the main receipt. It retains
the email and every original attachment. A failed rendering leaves the original
conversation available. An existing receipt or split family is not replaced.
The native preview includes that selected PDF only while the expense, source
message and file remain readable. This is a reading projection; the file stays
linked to its source. Pending linked-receipt retrievals are superseded through
their native generation fence when the rendered receipt is selected.

**Receipt email** (**Courriel justificatif** in French) opens the native HTML rendering
from the expense without running the PDF engine again. Opening a foreground tab retains the native form behavior: Odoo may save pending edits when the expense tab becomes hidden. On a phone the link appears in the
native smart-button menu. Existing approved expenses gain this reading path
without creating an attachment, changing their main receipt or resetting their
approval. The correction paths documented in Documents still apply when an
operator needs to replace an approved expense's own receipt.

Opening **Receipt email** from an expense requires current access to both the
expense and its source conversation in the selected companies. Split parts use their readable original expense's
conversation in the same company. The rendered attachment belongs to the source
message, with native attachment access and generic metadata. It does not copy private email
content into a broader expense or document scope. Native posting continues to
copy original expense-bound files to the accounting entry; the message-bound
rendering remains available through the source expense and is not automatically
published among the entry's files. Native attachment downloads keep Odoo’s
existing temporary-token semantics; this feature does not invalidate tokens
already issued. The authenticated HTML link rechecks current rights.

Same-receipt warnings compare complete readable email identities and genuine
file checksums within native access rules. They remain advisory and do not change
amounts, taxes, approval, accounting entries or reconciliation. Three distinct
renewal references remain distinct; a second readable email with the same complete
reference can still identify a true duplicate.
