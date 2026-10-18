# Native receipt copies during delegated operations

An authorized Agent could start native company-paid expense posting, but posting failed when Odoo copied the expense receipt onto the accounting entry. Odoo creates that attachment without sudo. The Agent side-effect guard rejected it even though the root posting operation was authorized. The entire posting transaction rolled back.

Attachment creation now carries the existing internal collaboration context when an authenticated, server-issued write-operation scope belongs to the current Agent. Odoo still performs its ordinary attachment ACL and linked-record checks. The owner’s access and the delegated company scope still apply. No sudo, approval role, expense-specific permission or additional action restriction is introduced.

The same behavior supports legitimate native attachment creation outside Expenses. Direct attachment RPCs retain their existing delegation requirements; a client string cannot forge an operation scope. Read-only delegation does not gain write authority, and evidence deletion retains its existing irreversible-action requirement.

The owning access-control module advances from `saas~19.3.1.8.1` to `.2`. The change adds no stored fields and requires no business-record migration.

Qualification includes native company posting with an exact receipt copy, an unrelated project attachment, missing and forged scope controls, owner access loss, foreign-company denial and read-only denial. Separate actual JSON-2 requests exercise both ordinary no-match expense posting and the posting-review wizard from PR #302. Removing only this source correction reproduces both receipt-copy denials on the same records, leaving them approved without entries; restoring the exact source lets both native postings complete. Source receipts remain attached to their expenses and copied bytes match exactly. An evidence change refreshes the review before posting; replay after completion cannot create another entry.

PR #302 owns the shared posting decision and visible interface. This companion owns native receipt-copy plumbing. Qualify their combined revision before marking the Agent journey complete. Preserve ancestry when integrating both, and regenerate the combined action surface from the canonical runtime; do not choose one branch’s generated policy wholesale.
