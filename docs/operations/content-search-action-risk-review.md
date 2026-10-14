# Live content search action review

The canonical runtime review adds three Home RPCs and five frontend transports.
It removes no existing action and changes no existing action digest or risk
class. The module closure remains the tracked 164 modules.

`search_records` accepts an internal caller, a text query and a finite business
category list. It uses native model/field/record read access and selected-company
scope. Related labels have their own authorized model query. Native User names
retain their public-identity authority; a denied User record cannot influence a
match through the raw-SQL portal helper. Search previews exclude credential and
binary values and read ordinary business text only after record access.

Native Properties schema reads use the existing accessor, with representative
parents derived only from the caller's permitted aggregate. Elevation remains
inside the native definition accessor; there is no elevated owner enumeration
or read of private Project names/business fields. Owner-bound property predicates
prevent another schema's ordinary label from enabling a credential property.

`search_documents` delegates to the existing governed Documents facade, including
its file-content authorization and explicit provider scope. It adds no archive
write or unrestricted binary request. Provider errors are returned as a local
unavailable state. Its preview window is bounded; continuation uses the native
Documents workspace with the current query.

`get_search_result_action` validates the fixed business-model catalogue and
rechecks native record/company access. It returns a normal form action with a
small context; it cannot execute an arbitrary client-selected server action or
carry stale project defaults into another record. Native form permissions still
control subsequent editing.

The five frontend entries forward only to these exact RPCs. They add no mutation,
messaging, filesystem write, credential read, production operation or new
provider integration. The existing Documents provider integration remains the
owner of its network reads. Search does not persist an index or run a cron job.

Automated evidence covers readable description/activity/property matches,
caller-denied related records, private schema authority, public identities,
selected companies, portal denial, stale destination access, readonly Accounting,
scoped OCR results, independent provider completion and native keyboard/opening.
Offline provider fixtures exercise transport and authorization; they do not
claim real archive availability or semantic model quality.
