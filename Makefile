ACCOUNTING_COMPAT ?= scripts/accounting-compat
ACCOUNTING_TEST_DB ?= odoo_rebuild_accounting_unit_$(shell date -u +%Y%m%d%H%M%S)
ACCOUNTING_TEST_TAGS ?= rebuild_account_migration_unit
ACCOUNTING_TEST_LOG_LEVEL ?= warn
USER_DOCS_HOST ?= 127.0.0.1
USER_DOCS_PORT ?= 8079

.PHONY: oca-addons-sync accounting-compat accounting-source-package-validate accounting-source-validate accounting-source-restore accounting-source-inspect accounting-extract accounting-source-validate-ledger accounting-failure-tests accounting-target-reset accounting-target-import accounting-target-validate accounting-target-idempotence accounting-target-failure-tests accounting-document-regeneration accounting-target-reconciliation-probe accounting-reports accounting-fec accounting-fec-preflight accounting-fec-validate accounting-compare accounting-readiness accounting-evidence accounting-addon-tests user-docs-serve user-docs-build

oca-addons-sync:
	scripts/sync-oca-addons

accounting-compat: oca-addons-sync
	$(ACCOUNTING_COMPAT) all

accounting-source-package-validate:
	$(ACCOUNTING_COMPAT) source-validate

accounting-source-validate:
	$(ACCOUNTING_COMPAT) source-controls

accounting-source-restore:
	$(ACCOUNTING_COMPAT) source-restore

accounting-source-inspect:
	$(ACCOUNTING_COMPAT) source-inspect

accounting-extract:
	$(ACCOUNTING_COMPAT) extract

accounting-source-validate-ledger:
	$(ACCOUNTING_COMPAT) source-controls

accounting-failure-tests:
	$(ACCOUNTING_COMPAT) failure-tests

accounting-target-reset: oca-addons-sync
	$(ACCOUNTING_COMPAT) target-reset

accounting-target-import:
	$(ACCOUNTING_COMPAT) target-import

accounting-target-validate:
	$(ACCOUNTING_COMPAT) target-validate

accounting-target-idempotence:
	$(ACCOUNTING_COMPAT) target-idempotence

accounting-target-failure-tests:
	$(ACCOUNTING_COMPAT) target-failure-tests

accounting-document-regeneration:
	$(ACCOUNTING_COMPAT) document-regeneration

accounting-target-reconciliation-probe:
	$(ACCOUNTING_COMPAT) target-reconciliation-probe

accounting-reports:
	$(ACCOUNTING_COMPAT) reports

accounting-fec:
	$(ACCOUNTING_COMPAT) fec

accounting-fec-preflight:
	$(ACCOUNTING_COMPAT) fec-preflight

accounting-fec-validate:
	$(ACCOUNTING_COMPAT) fec-validate

accounting-compare:
	$(ACCOUNTING_COMPAT) compare

accounting-readiness:
	$(ACCOUNTING_COMPAT) readiness

accounting-evidence:
	$(ACCOUNTING_COMPAT) evidence

accounting-addon-tests:
	docker compose --profile init run --rm -e ODOO_INIT_DB=$(ACCOUNTING_TEST_DB) init-db odoo --config=/etc/odoo/odoo.conf --database=$(ACCOUNTING_TEST_DB) --init=rebuild_account_migration --without-demo=true --test-enable --test-tags=$(ACCOUNTING_TEST_TAGS) --stop-after-init --log-level=$(ACCOUNTING_TEST_LOG_LEVEL)

user-docs-serve:
	python3 -m mkdocs serve --config-file mkdocs.yml --dev-addr $(USER_DOCS_HOST):$(USER_DOCS_PORT)

user-docs-build:
	python3 -m mkdocs build --config-file mkdocs.yml
