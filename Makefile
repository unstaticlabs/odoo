ACCOUNTING_COMPAT ?= scripts/accounting-compat
.DEFAULT_GOAL := help
COMPOSE_PROJECT ?= usl-odoo-saas-19-2
export COMPOSE_PROJECT_NAME := $(COMPOSE_PROJECT)
ACCOUNTING_TEST_DB ?= odoo_rebuild_accounting_unit_$(shell date -u +%Y%m%d%H%M%S)
ACCOUNTING_TEST_TAGS ?= rebuild_account_migration_unit
ACCOUNTING_TEST_LOG_LEVEL ?= warn
USER_DOCS_HOST ?= 127.0.0.1
USER_DOCS_PORT ?= 8079
USER_DOCS_VENV ?= .venv-docs
USER_DOCS_PYTHON ?= $(USER_DOCS_VENV)/bin/python
ODOO_DEV ?= scripts/odoo-dev
TESE_QA_GENERATION ?= 01
MODULE ?=
SERVICE ?=
CONFIRM ?=

.PHONY: product-restore product-restore-install product-restore-import product-restore-validate product-restore-finalize hr-restore hr-restore-install hr-restore-import hr-restore-validate hr-restore-finalize documents-restore documents-restore-install documents-restore-import documents-restore-validate documents-restore-serve documents-restore-status
.PHONY: help help-advanced doctor status dev deploy rebuild logs stop dev-reclaim login-link paperless-users disable-tours target-finalize target-reconstruct target-reconstruct-reuse-documents oca-addons-sync
.PHONY: project-restore project-restore-install project-restore-import project-restore-validate project-restore-finalize project-product-validate
.PHONY: migration-source-inventory migration-source-gate attachment-ledger attachment-ledger-gate identity-restore identity-restore-install identity-restore-import identity-restore-validate identity-restore-finalize
.PHONY: documents-qa-build documents-qa-up documents-qa-update documents-qa-bootstrap documents-qa-status documents-qa-test documents-qa-test-pocket documents-qa-test-js documents-qa-acceptance documents-qa-recovery-test documents-preprod-config documents-preprod-preflight documents-preprod-up documents-preprod-acceptance documents-preprod-recovery-test documents-acceptance documents-recovery-test
.PHONY: accounting-restore-finalize accounting-product-validate accounting-restore-tests
.PHONY: tese-restore tese-restore-install tese-restore-import tese-restore-validate tese-restore-idempotence tese-restore-finalize tese-product-validate tese-qa-bootstrap
.PHONY: platform-billing-restore platform-billing-restore-install platform-billing-restore-import platform-billing-restore-validate platform-billing-restore-idempotence platform-billing-restore-finalize platform-billing-product-validate platform-billing-restore-test
.PHONY: product-migration-source-boundary product-migration-boundary accounting-compat accounting-multicompany-acceptance accounting-source-package-validate accounting-source-validate accounting-source-restore accounting-source-inspect accounting-attachment-audit accounting-extract accounting-source-validate-ledger accounting-failure-tests
.PHONY: accounting-validation-exact-reset accounting-validation-exact-import accounting-validation-exact-validate accounting-validation-exact-idempotence accounting-validation-exact-failure-tests
.PHONY: accounting-validation-native-reset accounting-validation-native-expenses accounting-validation-native-documents accounting-validation-native-assets accounting-validation-native-deferrals accounting-validation-native-analytics accounting-validation-native-expense-settlement accounting-validation-native-document-settlement accounting-validation-native-general-reconciliation accounting-validation-native-bank-categorization accounting-validation-native-bank-external
.PHONY: accounting-dev-reset accounting-dev-import accounting-dev-validate accounting-dev-attachments accounting-currency-rate-provider accounting-reports accounting-fec accounting-fec-preflight accounting-fec-validate accounting-compare accounting-readiness accounting-evidence accounting-addon-tests
.PHONY: user-docs-deps user-docs-serve user-docs-build french-translations

help:
	@printf '%s\n' \
	  '' \
	  'USL Odoo Distribution — local development' \
	  '' \
	  'Common workflow' \
	  '  make doctor                         Diagnose ownership and configuration' \
	  '  make dev                            Start the existing development target' \
	  '  make deploy [MODULE=module_name]    Update mounted add-ons without rebuilding' \
	  '  make rebuild [MODULE=module_name]   Rebuild the image, then deploy' \
	  '  make status                         Show service ownership, health, and URLs' \
	  '  make logs [SERVICE=odoo]            Follow all or one service log' \
	  '  make stop                           Stop containers and preserve data' \
	  '' \
	  'Access and recovery' \
	  '  make login-link USER=username       Create a local one-time sign-in link' \
	  '  make paperless-users                Reconcile governed document access' \
	  '  make dev-reclaim CONFIRM=$(COMPOSE_PROJECT)' \
	  '                                      Reclaim canonical containers; preserve volumes' \
	  '' \
	  'Data reconstruction' \
	  '  make target-reconstruct             Full fresh deterministic reconstruction' \
	  '  make target-reconstruct-reuse-documents' \
	  '                                      Reuse verified Paperless ingestion in development' \
	  '' \
	  'Run make help-advanced for migration, validation, and specialized QA commands.'

help-advanced:
	@printf '%s\n' \
	  '' \
	  'USL Odoo Distribution — advanced commands' \
	  '' \
	  'Migration and boundaries' \
	  '  make migration-source-inventory     Audit populated source perimeters' \
	  '  make product-migration-boundary     Check source and database boundaries' \
	  '  make accounting-compat              Run the complete Accounting harness' \
	  '  make accounting-multicompany-acceptance' \
	  '                                      Prove company isolation and workflows' \
	  '' \
	  'Focused restoration' \
	  '  make identity-restore | product-restore | hr-restore' \
	  '  make project-restore | tese-restore | platform-billing-restore' \
	  '  make documents-restore' \
	  '' \
	  'QA and documentation' \
	  '  make accounting-addon-tests         Run focused Accounting module tests' \
	  '  make documents-qa-test              Run Documents QA tests' \
	  '  make user-docs-build                Render and validate user documentation' \
	  '' \
	  'All historical target names remain available; inspect Makefile for exact stages.'

doctor:
	@$(ODOO_DEV) doctor

status:
	@$(ODOO_DEV) status

dev:
	@$(ODOO_DEV) start

deploy:
	@if [ -n "$(strip $(MODULE))" ]; then \
		$(ODOO_DEV) deploy "$(MODULE)"; \
	else \
		$(ODOO_DEV) deploy; \
	fi

rebuild:
	@if [ -n "$(strip $(MODULE))" ]; then \
		$(ODOO_DEV) rebuild "$(MODULE)"; \
	else \
		$(ODOO_DEV) rebuild; \
	fi

logs:
	@if [ -n "$(strip $(SERVICE))" ]; then \
		$(ODOO_DEV) logs "$(SERVICE)"; \
	else \
		$(ODOO_DEV) logs; \
	fi

stop:
	@$(ODOO_DEV) stop

dev-reclaim:
	@USL_DEV_RECLAIM_CONFIRM="$(CONFIRM)" $(ODOO_DEV) reclaim

login-link:
	@if [ "$(origin USER)" != "command line" ] || [ -z "$(strip $(USER))" ]; then \
		printf 'Usage: make login-link USER=<Pocket ID username>\n' >&2; \
		exit 2; \
	fi
	@if [ -n "$${POCKET_ID_ENV_FILE:-}" ] && [ -f "$$POCKET_ID_ENV_FILE" ]; then \
		COMPOSE_PROJECT_NAME= scripts/pocket-id-dev one-time-link "$(USER)"; \
	else \
		scripts/pocket-id-dev one-time-link "$(USER)"; \
	fi

paperless-users:
	scripts/pocket-id-dev sync-paperless-users

disable-tours:
	$(ODOO_DEV) disable-tours

target-finalize:
	scripts/target-finalize

target-reconstruct:
	scripts/target-reconstruct

target-reconstruct-reuse-documents:
	USL_RECONSTRUCT_REUSE_DOCUMENTS=1 scripts/target-reconstruct

migration-source-inventory:
	scripts/migration-source-truth inventory

migration-source-gate:
	scripts/migration-source-truth gate

attachment-ledger:
	scripts/attachment-ledger inventory

attachment-ledger-gate:
	scripts/attachment-ledger gate

identity-restore:
	scripts/identity-restore all

identity-restore-install:
	scripts/identity-restore install

identity-restore-import:
	scripts/identity-restore import

identity-restore-validate:
	scripts/identity-restore validate

identity-restore-finalize:
	scripts/identity-restore finalize

product-restore:
	scripts/product-restore all

product-restore-install:
	scripts/product-restore install

product-restore-import:
	scripts/product-restore import

product-restore-validate:
	scripts/product-restore validate

product-restore-finalize:
	scripts/product-restore finalize

hr-restore:
	scripts/hr-restore all

hr-restore-install:
	scripts/hr-restore install

hr-restore-import:
	scripts/hr-restore import

hr-restore-validate:
	scripts/hr-restore validate

hr-restore-finalize:
	scripts/hr-restore finalize

documents-restore:
	scripts/documents-restore all

documents-restore-install:
	scripts/documents-restore install

documents-restore-import:
	scripts/documents-restore import

documents-restore-validate:
	scripts/documents-restore validate

documents-restore-serve:
	scripts/documents-restore serve

documents-restore-status:
	scripts/documents-restore status

documents-qa-build:
	scripts/documents-stack qa build

documents-qa-up:
	scripts/documents-stack qa up

documents-qa-update:
	scripts/documents-stack qa update

documents-qa-bootstrap:
	scripts/documents-stack qa bootstrap

documents-qa-status:
	scripts/documents-stack qa status

documents-qa-test:
	scripts/documents-stack qa test

documents-qa-test-pocket:
	scripts/documents-stack qa test-pocket

documents-qa-test-js:
	scripts/documents-stack qa test-js

documents-qa-acceptance:
	scripts/documents-acceptance qa

documents-qa-recovery-test:
	USL_DOCUMENTS_SYNTHETIC_RECOVERY=1 scripts/documents-recovery-test qa

documents-preprod-config:
	scripts/documents-stack preprod config

documents-preprod-preflight:
	scripts/documents-stack preprod preflight

documents-preprod-up:
	scripts/documents-stack preprod up

documents-preprod-acceptance:
	scripts/documents-acceptance preprod

documents-preprod-recovery-test:
	USL_DOCUMENTS_SYNTHETIC_RECOVERY=1 scripts/documents-recovery-test preprod

documents-acceptance:
	scripts/documents-acceptance qa

documents-recovery-test:
	USL_DOCUMENTS_SYNTHETIC_RECOVERY=1 scripts/documents-recovery-test qa

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

accounting-restore-finalize:
	scripts/accounting-restore finalize

accounting-product-validate:
	scripts/accounting-restore product-validate

accounting-restore-tests: oca-addons-sync
	docker compose -p $(COMPOSE_PROJECT) --profile test run --rm -e ODOO_INIT_DB=$(ACCOUNTING_TEST_DB) test odoo --config=/etc/odoo/odoo.conf --addons-path=/opt/odoo/addons,/opt/odoo/odoo/addons,/mnt/custom-addons,/mnt/oca-addons,/mnt/accounting-migration-addons --database=$(ACCOUNTING_TEST_DB) --init=usl_accounting_restore --without-demo=true --test-enable --test-tags=usl_accounting_restore --stop-after-init --log-level=$(ACCOUNTING_TEST_LOG_LEVEL)

tese-restore:
	scripts/tese-restore all

tese-restore-install:
	scripts/tese-restore install

tese-restore-import:
	scripts/tese-restore import

tese-restore-validate:
	scripts/tese-restore validate

tese-restore-idempotence:
	scripts/tese-restore idempotence

tese-restore-finalize:
	scripts/tese-restore finalize

tese-product-validate:
	scripts/tese-restore product-validate

tese-qa-bootstrap:
	$(ODOO_DEV) bootstrap-tese-payroll-qa $(TESE_QA_GENERATION)

platform-billing-restore:
	COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) scripts/platform-billing-restore all

platform-billing-restore-install:
	COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) scripts/platform-billing-restore install

platform-billing-restore-import:
	COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) scripts/platform-billing-restore import

platform-billing-restore-validate:
	COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) scripts/platform-billing-restore validate

platform-billing-restore-idempotence:
	COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) scripts/platform-billing-restore idempotence

platform-billing-restore-finalize:
	COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) scripts/platform-billing-restore finalize

platform-billing-product-validate:
	COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) scripts/platform-billing-restore product-validate

platform-billing-restore-test:
	COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) scripts/platform-billing-restore test

product-migration-source-boundary:
	scripts/check-product-migration-boundary

product-migration-boundary: product-migration-source-boundary
	scripts/check-product-database-boundary

accounting-multicompany-acceptance:
	docker compose -p $(COMPOSE_PROJECT) exec -T \
		-e USL_EINVOICE_LIVE_ENABLED=0 \
		-e USL_EREPORTING_LIVE_ENABLED=0 \
		odoo odoo shell --config=/etc/odoo/odoo.conf \
		--database=$${ODOO_INIT_DB:-odoo_dev} \
		< scripts/odoo/multicompany_accounting_acceptance.py

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
	docker compose -p $(COMPOSE_PROJECT) --profile test run --rm -e ODOO_INIT_DB=$(ACCOUNTING_TEST_DB) test odoo --config=/etc/odoo/odoo.conf --database=$(ACCOUNTING_TEST_DB) --init=rebuild_account_migration --without-demo=true --test-enable --test-tags=$(ACCOUNTING_TEST_TAGS) --stop-after-init --log-level=$(ACCOUNTING_TEST_LOG_LEVEL)

$(USER_DOCS_VENV)/.requirements-ready: requirements-docs.txt
	python3 -m venv $(USER_DOCS_VENV)
	$(USER_DOCS_VENV)/bin/python -m pip install --disable-pip-version-check --requirement requirements-docs.txt
	touch $(USER_DOCS_VENV)/.requirements-ready

user-docs-deps: $(USER_DOCS_VENV)/.requirements-ready

user-docs-serve: user-docs-deps
	$(USER_DOCS_PYTHON) -m mkdocs serve --config-file mkdocs.yml --dev-addr $(USER_DOCS_HOST):$(USER_DOCS_PORT)

user-docs-build: user-docs-deps
	$(USER_DOCS_PYTHON) -m mkdocs build --config-file mkdocs.yml

french-translations:
	docker compose -p $(COMPOSE_PROJECT) exec -T odoo \
		python3 - /mnt/custom-addons < scripts/check_fr_translations.py
