# Native receipt copies during delegated operations

Native company-paid expense posting copies every receipt onto the accounting entry. The source receipts remain attached to the expense. The copy must preserve exact bytes and identify the acting user, including when that user is a delegated Agent.

The native shared-model attachment implementation delivered in commit `a425d6509be500f835ddc57d96957ee46c103c67` already supports this operation. Ordinary attachment ACLs and linked-parent access checks enforce current owner authority, writable application delegation and company scope. Direct attachment calls use the same checks. Read-only delegation does not authorize posting, and deleting retained evidence continues to require the existing irreversible-action authority.

PR #304 now adds focused regression coverage for this existing behavior. Its earlier attachment-create collaboration-token override is removed: propagating that token into linked-parent checks would bypass mixed read-only application delegation on the current implementation. There is no new runtime override, action-policy sink, module version or approval requirement.

The receipt test exercises ordinary human and delegated company-paid posting with two synthetic attachments, including binary bytes. It checks posted/paid state, distinct copies linked to the entry, exact content, acting-user attribution and unchanged source attachment metadata and main receipt. A read-only Agent remains unable to post or produce an entry. The existing native attachment suite separately checks mixed-profile parent permissions, owner/company boundaries, batches and protected evidence deletion.

PR #302 owns the visible expense decision. Qualify its final posting paths against the native attachment implementation and these receipt checks. Earlier October 17 causal evidence describes the historical predecessor; it does not establish that the removed override is needed on current staging.
