# Direct document file reads

`usl.document.mcp_read_binary` is a read-only native entry point. It returns a
bounded original or archive file to the authenticated caller, without creating
a bearer grant, persisting a copy or changing a financial record.

The method uses the existing document lookup and authorized binary descriptor.
They enforce model and record access, selected companies, version ownership,
linked-record access and confirmed archive permissions before storage is read.
It never switches user, enables sudo, accepts a source URL or constructs new
permissions. The storage client uses its existing fixed document download path
and version/variant parameters. Its network operation is a read.

The response buffers at most 4 MiB and closes the native stream on success or
failure. A non-complete response, incomplete length, excessive body or mismatch
with a recognized archive checksum returns no file. The response always binds
its actual byte count and SHA-256 to the same returned bytes. Historical MD5
remains verifiable; absent or unrecognized archive checksums are explicitly
unverified. The connector independently checks size, SHA-256, document, selected
version and variant before emitting a binary content block.

Normal share withdrawal is exercised through native record link/unlink. A
subsequent file read, metadata/context read and integrity read are refused under
the same record rules; an existing grant also loses redemption authority.
Company changes are denied before storage access. Explicit historical versions
remain bound after replacement while the default selects the new current
version. Existing native HTTP tests cover expiry, revocation, company/issuer
changes, range semantics and unavailable storage. A read already authorized in flight follows native Odoo transaction snapshot
semantics. Already received client bytes cannot be recalled, and a client must separately prove its PDF viewing behavior.

The companion integrity context read from PR495 remains read-only. Its bounded
HEAD probes and digest algorithm labels do not transfer file bodies or issue
grants. The new entry point uses the same file authority; it is not a metadata
access shortcut.

## Qualification

- Native Documents module:313 test methods pass, including the new byte and
  share/replacement cases and existing HTTP grant tests. Removing only the new
  method reproduces six missing-method errors in the19-method grant class;
  restored full-module qualification passes.
- MCP:356 checks pass. The existing content tool retains its ordinary paginated
  text contract and uses one native read for an explicit binary selection.
  Older-server unavailability, wrong version and digest mismatch return no file.
- The module upgrade preserves original financial columns, frozen attachments,
  chatter, projects, memberships and exact evidence bytes on an old-source
  predecessor and repeated upgrade. Positive financial controls use invented
  posted journal entries; empty reconciliation/bank tables are not positive
  coverage of historic settlements.
- Gateway qualification uses the corrected deployed GitOps1298 contract: the
  production gateway returns502 on an unavailable upstream, while staging maps
  it to a private503 maintenance response. These separate URL-route tests do
  not prove PDF rendering or network access from a hosted scheduled client.

The native entry point is classified `read_only`. No other method, role or
public controller acquires authority from this classification.
