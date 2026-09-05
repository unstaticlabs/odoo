# September 5 upstream and dependency integration

This integration starts from staging `b6f00b945530` and preserves the histories
of PRs #50, #51, #52, #54 and #67–#70 with merge commits. The upstream target is
`4d26b5cfe7259498a08284d523ac320aa4720de9` on `saas-19.3`.

## Resolutions

- Retain staging's current qualification-evidence framework. PR #67 includes
  the older production-promotion implementation, already replaced in staging;
  merging its ancestry must not restore that retired implementation.
- Follow upstream's inventory-report layout revert. The USL edit to the
  reverted lines only removed whitespace; it did not add product behavior.
- Retain USL's dependency pins when upstream uses older versions.
- Update cryptography to 50.0.1 and platformdirs to 4.11.5. Reconcile PR #70's
  earlier image pins with PR #54's current Ollama 0.33.3 digest and Valkey 9.1.2.
- Apply cleaner 0.4.5 only on Python 3.14+, paired with lxml 6.1.2. Python 3.12
  and 3.13 retain their existing cleaner 0.4.4: their lxml 5.2.1 and 5.4.0 pins
  cannot satisfy cleaner 0.4.5's lxml>=6.1.1 requirement. This integration does
  **not** deliver that cleaner security fix on the production Python 3.12
  runtime. Upgrading its XML stack requires separate compatibility qualification.

## Qualification scope

Upstream changes include a new default Factur-X exporter, shared CII import
helpers, French PDP lifecycle metadata, historical-rate landed costs, AVCO
valuation, and ORM domain optimization. A conflict-free merge is not sufficient
evidence for these changes.

Qualification must cover clean product installation, repeated upgrades, the
product/migration boundary, the exact runtime action inventory, offline CII
reception/export fixtures, and affected accounting and access-control tests.
Keep external e-invoicing and e-reporting disabled in all qualification runs.

The three new PDP `fields_get` overrides can initialize missing selection
metadata through elevated writes. They must not receive a read-only policy
classification solely because of the generic method name. The new CII model's
own methods are private; inherited public methods still require exact review.

## Local qualification results

- Exact Python 3.12 test-image build passed.
- Repository tests: 444 passed checks, including one host-specific skipped test.
- Clean product installation and two successive module upgrades passed.
- Upgrade of a restored earlier local QA database passed. This was a database
  fixture upgrade, not a production backup/filestore recovery rehearsal.
- USL e-invoice reception, access-control and landed-cost selection: 54 tests,
  no failures or errors.
- Native upstream CII/PDP, landed-cost and ORM-domain selection: 31 tests,
  no failures or errors. Optional Community-unavailable deferral/Chorus fixtures
  are skipped; separate USL tests cover missing and partial deferral schemas.
- Product/migration source boundary passed.

The runtime action review covers 295 added/changed exact actions and 20 removed
actions. Applying and resealing that reviewed policy is still pending explicit
authorization required by the automated permission reviewer. No merge or PR
closure is justified until the refreshed source and runtime policy checks pass.
