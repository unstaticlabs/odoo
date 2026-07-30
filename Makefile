ACCOUNTING_COMPAT ?= scripts/accounting-compat
COMPOSE_PROJECT ?= usl-odoo-saas-19-2
ACCOUNTING_TEST_DB ?= odoo_rebuild_accounting_unit_$(shell date -u +%Y%m%d%H%M%S)
ACCOUNTING_TEST_TAGS ?= rebuild_account_migration_unit
ACCOUNTING_TEST_LOG_LEVEL ?= warn
USER_DOCS_HOST ?= 127.0.0.1
USER_DOCS_PORT ?= 8079
USER_DOCS_PYTHON ?= python3
ODOO_DEV ?= scripts/odoo-dev

.PHONY: dev deploy rebuild oca-addons-sync project-restore project-restore-install project-restore-import project-restore-validate project-restore-finalize project-product-validate product-migration-boundary accounting-compat accounting-source-package-validate accounting-source-validate accounting-source-restore accounting-source-inspect accounting-attachment-audit accounting-extract accounting-source-validate-ledger accounting-failure-tests accounting-validation-exact-reset accounting-validation-exact-import accounting-validation-exact-validate accounting-validation-exact-idempotence accounting-validation-exact-failure-tests accounting-validation-native-reset accounting-validation-native-expenses accounting-validation-native-documents accounting-validation-native-assets accounting-validation-native-deferrals accounting-validation-native-analytics accounting-validation-native-expense-settlement accounting-validation-native-document-settlement accounting-validation-native-general-reconciliation accounting-validation-native-bank-categorization accounting-validation-native-bank-external accounting-dev-reset accounting-dev-import accounting-dev-validate accounting-dev-attachments accounting-currency-rate-provider accounting-reports accounting-fec accounting-fec-preflight accounting-fec-validate accounting-compare accounting-readiness accounting-evidence accounting-addon-tests user-docs-serve user-docs-build

dev:
	$(ODOO_DEV) start

deploy:
	$(ODOO_DEV) deploy

rebuild:
	$(ODOO_DEV) rebuild

oca-addons-sync:
	scripts/sync-oca-addons

accounting-compat: oca-addons-sync
	$(ACCOUNTING_COMPAT) all

project-restore:
	scripts/project-restore all

project-restore-install:
	scripts/project-restore install

project-restore-import:
	scripts/project-restore import

project-restore-validate:
	scripts/project-restore validate

project-restore-finalize:
	scripts/project-restore finalize

project-product-validate:
	scripts/project-restore product-validate

product-migration-boundary:
	scripts/check-product-migration-boundary

accounting-source-package-validate:
	$(ACCOUNTING_COMPAT) source-validate

accounting-source-validate:
	$(ACCOUNTING_COMPAT) source-controls

accounting-source-restore:
	$(ACCOUNTING_COMPAT) source-restore

accounting-source-inspect:
	$(ACCOUNTING_COMPAT) source-inspect

accounting-attachment-audit:
	$(ACCOUNTING_COMPAT) attachment-audit

accounting-extract:
	$(ACCOUNTING_COMPAT) extract

accounting-source-validate-ledger:
	$(ACCOUNTING_COMPAT) source-controls

accounting-failure-tests:
	$(ACCOUNTING_COMPAT) failure-tests

accounting-validation-exact-reset: oca-addons-sync
	$(ACCOUNTING_COMPAT) validation-exact-reset

accounting-validation-exact-import:
	$(ACCOUNTING_COMPAT) validation-exact-import

accounting-validation-exact-validate:
	$(ACCOUNTING_COMPAT) validation-exact-validate

accounting-validation-exact-idempotence:
	$(ACCOUNTING_COMPAT) validation-exact-idempotence

accounting-validation-exact-failure-tests:
	$(ACCOUNTING_COMPAT) validation-exact-failure-tests

accounting-validation-native-reset: oca-addons-sync
	$(ACCOUNTING_COMPAT) validation-native-reset

accounting-validation-native-expenses:
	$(ACCOUNTING_COMPAT) validation-native-expenses

accounting-validation-native-documents:
	$(ACCOUNTING_COMPAT) validation-native-documents

accounting-validation-native-assets:
	$(ACCOUNTING_COMPAT) validation-native-assets

accounting-validation-native-deferrals:
	$(ACCOUNTING_COMPAT) validation-native-deferrals

accounting-validation-native-analytics:
	$(ACCOUNTING_COMPAT) validation-native-analytics

accounting-validation-native-expense-settlement:
	$(ACCOUNTING_COMPAT) validation-native-expense-settlement

accounting-validation-native-document-settlement:
	$(ACCOUNTING_COMPAT) validation-native-document-settlement

accounting-validation-native-general-reconciliation:
	$(ACCOUNTING_COMPAT) validation-native-general-reconciliation

accounting-validation-native-bank-categorization:
	$(ACCOUNTING_COMPAT) validation-native-bank-categorization

accounting-validation-native-bank-external:
	$(ACCOUNTING_COMPAT) validation-native-bank-external

accounting-dev-reset:
	$(ACCOUNTING_COMPAT) dev-reset

accounting-dev-import:
	$(ACCOUNTING_COMPAT) dev-import

accounting-dev-validate:
	$(ACCOUNTING_COMPAT) dev-validate

accounting-dev-attachments:
	$(ACCOUNTING_COMPAT) dev-attachments

accounting-currency-rate-provider:
	$(ACCOUNTING_COMPAT) currency-rate-provider

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

accounting-addon-tests: oca-addons-sync
	docker compose -p $(COMPOSE_PROJECT) --profile init run --rm -e ODOO_INIT_DB=$(ACCOUNTING_TEST_DB) init-db odoo --config=/etc/odoo/odoo.conf --database=$(ACCOUNTING_TEST_DB) --init=rebuild_account_migration --without-demo=true --test-enable --test-tags=$(ACCOUNTING_TEST_TAGS) --stop-after-init --log-level=$(ACCOUNTING_TEST_LOG_LEVEL)

user-docs-serve:
	$(USER_DOCS_PYTHON) -m mkdocs serve --config-file mkdocs.yml --dev-addr $(USER_DOCS_HOST):$(USER_DOCS_PORT)

user-docs-build:
	$(USER_DOCS_PYTHON) -m mkdocs build --config-file mkdocs.yml
