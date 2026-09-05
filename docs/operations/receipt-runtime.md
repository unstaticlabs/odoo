# Receipt recovery runtime

A release with receipt recovery publishes both `receipt-fetcher` and
`receipt-egress` images. GitOps promotes their immutable digests, resource
limits and Compose topology together. The fetcher communicates with Odoo over
a shared Unix socket with mutual TLS. Its only network is the internal receipt
proxy network; the egress proxy connects that network to the public network.
Neither service joins the application database network.

Release preparation creates separate credentials under
`/opt/usl-odoo/secrets/receipts` for production and
`/opt/usl-odoo/staging/secrets/receipts` for staging. Repeated preparation keeps
an existing complete credential set. The private parent directory protects
host access; mounted leaf directories permit non-root container access.

The receipt-control volume contains transient socket state. Introducing it
must not invalidate a backup of an earlier release. Baseline health and service
operations follow the active release and captured Compose configuration; the
candidate must provide both receipt services before admission.

Production quarantine and disposable recovery disable queue execution with
`ODOO_QUEUE_JOB_CHANNELS=root:0`. Admission restores the published channel
configuration. Neutralized databases also reject receipt retrieval and feedback
provider processing at the application boundary.
