# Direct document file reads

`usl.document.mcp_read_binary` is a read-only native entry point. It returns a
bounded original or archive file to the authenticated caller, without creating
a bearer grant, persisting a copy or changing a financial record.

The method uses the existing document lookup and authorized binary descriptor.
They enforce model and record access, selected companies, version ownership,
linked-record access and confirmed archive permissions before storage is read.
Authorization uses the primary database, including deployments with read
replicas, so a new request cannot authorize a withdrawn share from a lagging
replica. This remains a read-only business operation; primary routing adds no
write permission. It never switches user, enables sudo, accepts a source URL
or constructs new permissions. The storage client uses its existing fixed document download path
and version/variant parameters. Its network operation is a read.

The response buffers at most 4 MiB and closes the native stream on success or
failure. The storage request explicitly asks for identity encoding and refuses
any non-identity Content-Encoding before consuming the body. Mid-stream timeouts,
socket failures and malformed HTTP framing return the safe incomplete-transfer
business error, without a partial file or private upstream exception details.
A non-complete response, incomplete length, excessive body or mismatch
with a recognized archive checksum returns no file. The response always binds
its actual byte count and SHA-256 to the same returned bytes. Historical MD5
remains verifiable; absent or unrecognized archive checksums are explicitly
unverified and require a matching Content-Length or native chunked decoding
completed through its final terminator. A close-delimited HTTP/1.x EOF alone
cannot distinguish a complete file from a nonempty prefix and is refused unless
the selected version’s recognized source checksum verifies the exact bytes.
Recognized checksums permit legitimate unframed responses without guessing a
length. No ETag or received-prefix hash is treated as independent completeness
proof. The connector independently checks size, SHA-256, document, selected
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

- Initial canonical qualification: 313 Documents test methods passed, including
  byte/share/replacement cases and existing HTTP grant tests. Removing only the
  new method reproduced six missing-method errors in the 19-method grant class.
  The completeness follow-up passes 315 methods on a clean standalone install;
  five optional full-product authority cases are explicitly skipped there and
  remain covered by the earlier canonical run. The new real HTTP/1.1 regression
  rejects normal EOF prefixes without a recognized checksum, permits matching
  SHA-256/MD5 and fully framed responses, and refuses truncated chunked bodies.
  A test-only literal restoration of the prior method reproduces two failed
  EOF subcases with zero errors; the exact restored grant/streaming classes
  pass 28 methods on the 169-module canonical registry.
- MCP: 357 checks pass. The existing content tool retains its ordinary paginated
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

## Primary authorization and interrupted-transfer follow-up

Fresh document authorization is routed to the primary database for both native
RPC transports, including installations configured with a read replica. The
operation remains semantically read-only and keeps the same native delegation.
No real replica-lag environment was created for qualification; the regression
executes both native routing decisions against the installed model MRO.

A clean Documents install exercises 318 test methods with zero failures or
errors, including 67 desktop and 55 mobile component tests. Six optional
full-product authority cases are identified explicitly rather than treated as
passing in the reduced module closure; all six subsequently pass on the
existing full 169-module registry. The primary-routing, encoded-response
and interrupted-stream regressions reproduce four assertion failures and five
unhandled transport errors on the original source and its literal test-only
restoration. The restored grant and stream classes pass all 31 methods.

Two additional current-state upgrades preserve the same frozen rows, financial
fingerprints and exact attachment bytes. They are repeat upgrades of the
existing QA registry, separate from the historical predecessor proof above.
The current source also passes 1,205 script tests (39 explicit skips), French
validation and normal asset compilation. Synthetic Agent and packaged-HTTP
replays verify the exact original and archive bytes and refusal of withdrawn
files, context and foreign-company access. No production files are used.
