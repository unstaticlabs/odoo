# Bank suggestion labels

Payment suggestions show the bank transaction label as the primary link text. The journal-entry identifier remains in the hover text and accessible link name. The link is keyboard reachable and Enter opens the same related record. Opening the link still uses the same native related-record action. For a bank transaction, this opens its statement line with a link to the original journal entry.

The label comes from the native bank statement line, independently of a bookkeeping reference. When the bank label is empty, the entry identifier remains visible. Existing payments without a bank statement keep their native entry identifier. Long labels wrap on desktop and mobile. Narrow desktop payment widgets reuse the existing mobile layout, keeping the description together below the date and amount instead of squeezing it between the other columns.

This changes presentation and the suggestion payload. It does not change candidate amounts, ranking, accounting entries, matching authority or certified bank facts. Existing native certified-statement undo protection remains in place.
