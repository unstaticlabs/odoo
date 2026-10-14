# Bank suggestion labels

Payment suggestions show the bank transaction label as the primary link text. The journal-entry identifier remains in the hover text and accessible link name. Opening the link still uses the same native journal-entry action.

The label comes from the native bank statement line, independently of a bookkeeping reference. When the bank label is empty, the entry identifier remains visible. Existing payments without a bank statement keep their native presentation. Long labels wrap on desktop and mobile.

This changes presentation and the suggestion payload. It does not change candidate amounts, ranking, accounting entries, matching authority or certified bank facts. Existing native certified-statement undo protection remains in place.
