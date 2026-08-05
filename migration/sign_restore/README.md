# Odoo Online Sign restoration

This temporary add-on restores the historical Sign perimeter into the native
USL Sign models. It is exposed only by the `sign-migration` Compose profile and
is uninstalled after validation.

The restore copies original PDFs, final signed PDFs and Odoo Online completion
certificates as separate immutable evidence. The certificate is described as
source completion evidence, not as proof of a legal assurance level. Historical
requests use `provider_code=odoo_online`, never call a provider, remain read-only,
and explicitly record that achieved assurance could not be established.

Templates are matched by company, name and PDF SHA-256, so equivalent source
templates deliberately converge on one target template while every source
template-to-target link is counted. Requests are matched by
a deterministic fingerprint of original/final hashes, completion date, subject
and normalized signer emails. Source database IDs remain only on the temporary
run/issues and are removed with the module.

Run `scripts/sign-restore all`. The sequence installs the temporary module,
imports through a read-only source connection and filestore mount, validates
hashes and evidence classes, then uninstalls the module and checks the delivered
registry boundary. A malformed layout is archived for manual review; an
ambiguous or incomplete completed request is rejected instead of being marked
complete.
