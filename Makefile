ACCOUNTING_COMPAT ?= scripts/accounting-compat
.DEFAULT_GOAL := help
COMPOSE_PROJECT ?= usl-odoo-saas-19-3
export COMPOSE_PROJECT_NAME := $(COMPOSE_PROJECT)
ACCOUNTING_TEST_DB ?= odoo_rebuild_accounting_unit_$(shell date -u +%Y%m%d%H%M%S)
ACCOUNTING_TEST_TAGS ?= rebuild_account_migration_unit
ACCOUNTING_TEST_LOG_LEVEL ?= warn
USER_DOCS_HOST ?= 127.0.0.1
USER_DOCS_PORT ?= 8079
USER_DOCS_VENV ?= .venv-docs
USER_DOCS_PYTHON ?= $(USER_DOCS_VENV)/bin/python
ODOO_DEV ?= scripts/odoo-dev
ODOO_DEV_DB ?= odoo_dev
TESE_QA_GENERATION ?= 01
MODULE ?=
MODULES ?=
SERVICE ?=
CONFIRM ?=
PROFILE ?= full
SOURCE_SHA ?=
SOURCE_DIR ?=
CANDIDATE ?=
FINGERPRINT ?=
ENV_FILE ?=
IDENTITY_POLICY ?=
JOURNEY_EVIDENCE ?=
OUTPUT_ROOT ?=
ACTION_RISK_CANDIDATE ?= artifacts/action-risk/action_surface.candidate.json
ACTION_RISK_RUNTIME_CANDIDATE ?= artifacts/action-risk/runtime.candidate.json

define require_cutover_inputs
if [ -z "$(strip $(ENV_FILE))" ] || [ -z "$(strip $(CANDIDATE))" ] || [ -z "$(strip $(FINGERPRINT))" ]; then \
	printf 'ENV_FILE=<mode-0600-env>, CANDIDATE=<dir> and FINGERPRINT=<approved> are required.\n' >&2; \
	exit 2; \
fi
endef

.PHONY: product-restore product-restore-install product-restore-import product-restore-validate product-restore-finalize hr-restore hr-restore-install hr-restore-import hr-restore-validate hr-restore-finalize documents-restore documents-restore-install documents-restore-import documents-restore-validate documents-restore-serve documents-restore-status sign-restore
.PHONY: help help-advanced doctor status dev dev-up dev-down test deploy rebuild logs stop dev-reclaim login-link repair-pocket-id configure-pocket-id paperless-users disable-tours qa qa-reuse qa-clean qa-cache-status qa-cache-refresh qa-cache-resume qa-cache-qualify-resume qa-cache-prune release-verify migration-legacy-verify target-finalize target-reconstruct target-reconstruct-product target-reconstruct-reuse-documents migrate-production oca-addons-sync document-renderer-certs document-renderer-check
.PHONY: project-restore project-restore-install project-restore-import project-restore-validate project-restore-finalize project-product-validate
.PHONY: sign-product-validate
.PHONY: migration-source-inventory migration-source-report migration-source-gate migration-outbound-safety attachment-ledger attachment-ledger-gate identity-restore identity-restore-install identity-restore-import identity-restore-validate identity-restore-finalize
.PHONY: documents-qa-build documents-qa-up documents-qa-update documents-qa-bootstrap documents-qa-status documents-qa-test documents-qa-test-pocket documents-qa-test-js documents-qa-acceptance documents-qa-recovery-test documents-preprod-config documents-preprod-preflight documents-preprod-up documents-preprod-acceptance documents-preprod-recovery-test documents-acceptance documents-recovery-test
.PHONY: documents-release-build documents-release-verify documents-release-restore documents-release-accept documents-release-publish
.PHONY: accounting-restore-finalize accounting-product-validate accounting-restore-tests
.PHONY: tese-restore tese-restore-install tese-restore-import tese-restore-validate tese-restore-idempotence tese-restore-finalize tese-product-validate tese-qa-bootstrap
.PHONY: platform-billing-restore platform-billing-restore-install platform-billing-restore-import platform-billing-restore-validate platform-billing-restore-idempotence platform-billing-restore-finalize platform-billing-product-validate platform-billing-restore-test
.PHONY: product-migration-source-boundary product-migration-boundary accounting-compat accounting-multicompany-acceptance accounting-source-package-validate accounting-source-validate accounting-source-restore accounting-source-inspect accounting-attachment-audit accounting-extract accounting-source-validate-ledger accounting-failure-tests
.PHONY: accounting-validation-exact-reset accounting-validation-exact-import accounting-validation-exact-validate accounting-validation-exact-idempotence accounting-validation-exact-failure-tests
.PHONY: accounting-validation-native-reset accounting-validation-native-expenses accounting-validation-native-documents accounting-validation-native-assets accounting-validation-native-deferrals accounting-validation-native-analytics accounting-validation-native-expense-settlement accounting-validation-native-document-settlement accounting-validation-native-general-reconciliation accounting-validation-native-bank-categorization accounting-validation-native-bank-external
.PHONY: accounting-dev-reset accounting-dev-import accounting-dev-validate accounting-dev-attachments accounting-currency-rate-provider accounting-reports accounting-fec accounting-fec-preflight accounting-fec-validate accounting-compare accounting-readiness accounting-evidence accounting-addon-tests
.PHONY: user-docs-deps user-docs-serve user-docs-build action-helpers action-risk-discover action-risk-refresh action-risk-compile-policy action-risk-inventory action-risk-runtime product-assets french-translations expense-batch-qa-bootstrap
.PHONY: migration-candidate-build migration-candidate-verify migration-candidate-status production-cutover-preflight production-cutover-stage production-cutover-configure production-cutover-gate production-cutover-admit production-cutover-reset

help:
	@printf '%s\n' \
	  '' \
	  'USL Odoo Distribution — local development' \
	  '' \
	  'Common workflow' \
	  '  make doctor                         Diagnose ownership and configuration' \
	  '  make dev-up                         Start the existing development target' \
	  '  make dev-down                       Stop containers and preserve data' \
	  '  make test MODULES=usl_accounting    Run focused module tests' \
	  '  make qa                             Run the representative product QA gate' \
	  '  make release-verify                 Verify continuous-release contracts' \
	  '  make migration-legacy-verify        Verify the guarded legacy overlay only' \
	  '  make deploy [MODULE=module_name]    Update mounted add-ons without rebuilding' \
	  '  make rebuild [MODULE=module_name]   Rebuild the image, then deploy' \
	  '  make status                         Show service ownership, health, and URLs' \
	  '  make logs [SERVICE=odoo]            Follow all or one service log' \
	  '  make document-renderer-certs        Generate isolated local mTLS credentials' \
	  '  make document-renderer-check        Verify the pinned renderer submodule' \
	  '' \
	  'Access and recovery' \
	  '  make login-link USER=username       Create a local one-time sign-in link' \
	  '  make repair-pocket-id                Repair and verify local SSO runtime' \
	  '  make paperless-users                Reconcile governed document access' \
	  '  make dev-reclaim CONFIRM=$(COMPOSE_PROJECT)' \
	  '                                      Reclaim canonical containers; preserve volumes' \
	  '' \
	  'Data reconstruction' \
	  '  make qa [PROFILE=full]              Fast isolated QA from qualified cache' \
	  '  make qa-reuse                       Revalidate this worktree QA target in place' \
	  '  make qa-clean CONFIRM=qa-volumes    Remove this worktree QA volumes' \
	  '  make qa PROFILE=no-documents        Full Odoo data without Documents runtime' \
	  '  make qa PROFILE=documents-smoke     Deterministic Documents sample' \
	  '  make qa PROFILE=clean-install       Clean product plus self-contained fixtures' \
	  '  make qa PROFILE=home                Focused isolated Home cockpit review' \
	  '  make qa-cache-status                Check shared cache compatibility' \
	  '  make qa-cache-refresh               Full fresh migration and atomic cache refresh' \
	  '  make qa-cache-resume                Revalidate Accounting and resume a failed refresh' \
	  '  make qa-cache-qualify-resume        Resume seed qualification after migration finalization' \
	  '  make migrate-production SOURCE_SHA=<sha256>' \
	  '                                      Authoritative full-source production migration' \
	  '  make migration-candidate-build SOURCE_DIR=<final-source>' \
	  '                                      Seal sanitized Odoo/Paperless production assets' \
	  '  make migration-candidate-status SOURCE_DIR=<final-source>' \
	  '                                      Verify the current private candidate' \
	  '  make target-reconstruct-product     Fresh reconstruction of shipped product scopes' \
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
	  '  make migration-source-report        Write exact private coverage/gap evidence' \
	  '  make product-migration-boundary     Check source and database boundaries' \
	  '  make accounting-compat              Run the complete Accounting harness' \
	  '  make accounting-multicompany-acceptance' \
	  '                                      Prove company isolation and workflows' \
	  '' \
	  'Focused restoration' \
	  '  make identity-restore | product-restore | hr-restore' \
	  '  make project-restore | tese-restore | platform-billing-restore' \
	  '  make documents-restore | sign-restore' \
	  '' \
	  'QA and documentation' \
	  '  make accounting-addon-tests         Run focused Accounting module tests' \
	  '  make documents-qa-test              Run Documents QA tests' \
	  '  make action-helpers                  Check guidance on consequential actions' \
	  '  make action-risk-inventory           Reject unclassified or stale product actions' \
	  '  make action-risk-compile-policy      Compile the compact protected runtime policy' \
	  '  make action-risk-runtime             Check the live odoo_dev action registry' \
	  '  make expense-batch-qa-bootstrap      Seed mixed-payer Expense Batch QA data' \
	  '  make user-docs-build                Render and validate user documentation' \
	  '  make qa-cache-prune CONFIRM=qa-seeds' \
	  '                                      Remove superseded private QA seeds' \
	  '  make production-cutover-preflight ENV_FILE=<0600-env> CANDIDATE=<dir> FINGERPRINT=<approved>' \
	  '  make production-cutover-stage ...   Restore into fresh dedicated volumes, no OCR' \
	  '  make production-cutover-configure IDENTITY_POLICY=<0600-json> ...' \
	  '  make production-cutover-gate JOURNEY_EVIDENCE=<0600-json> ...' \
	  '  make production-cutover-admit ...   Admit and permanently disable candidate reset' \
	  '  make production-cutover-reset ...   Pre-admission candidate-owned reset only' \
	  '' \
	  'All historical target names remain available; inspect Makefile for exact stages.'

doctor:
	@$(ODOO_DEV) doctor

document-renderer-certs:
	@scripts/generate-document-renderer-certs

document-renderer-check:
	@scripts/check-document-renderer-submodule

status:
	@$(ODOO_DEV) status

dev:
	@$(ODOO_DEV) start

dev-up: dev

dev-down: stop

test:
	@if [ -z "$(strip $(MODULES))" ]; then \
		printf 'Usage: make test MODULES=module_a[,module_b]\n' >&2; \
		exit 2; \
	fi
	@case "$(MODULES)" in *[!A-Za-z0-9_,\ ]*) printf 'MODULES contains an unsafe module name.\n' >&2; exit 2;; esac
	@for module in $$(printf '%s' "$(MODULES)" | tr ',' ' '); do \
		$(ODOO_DEV) test "$$module" || exit $$?; \
	done

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

release-verify:
	@scripts/release-verify

migration-legacy-verify:
	@if [ ! -x scripts/migration-legacy ]; then \
		printf 'scripts/migration-legacy is delivered by the gated post-migration cleanup; it is not present on this branch.\n' >&2; \
		exit 2; \
	fi
	@scripts/migration-legacy verify

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

configure-pocket-id:
	@$(ODOO_DEV) configure-pocket-id

repair-pocket-id:
	@$(ODOO_DEV) repair-pocket-id

paperless-users:
	scripts/pocket-id-dev sync-paperless-users

disable-tours:
	$(ODOO_DEV) disable-tours

qa: document-renderer-check
	@COMPOSE_PROJECT_NAME= PROFILE="$(PROFILE)" scripts/qa-environment "$(PROFILE)"

qa-reuse: document-renderer-check
	@COMPOSE_PROJECT_NAME= USL_QA_REUSE_EXISTING=1 \
		PROFILE="$${PROFILE:-full}" scripts/qa-environment "$${PROFILE:-full}"

qa-clean:
	@COMPOSE_PROJECT_NAME= USL_QA_CLEAN_CONFIRM="$(CONFIRM)" scripts/qa-clean

qa-cache-status:
	@COMPOSE_PROJECT_NAME= scripts/qa-seed status

qa-cache-refresh:
	@USL_MIGRATION_PURPOSE=qa-cache USL_QA_DATA_PROFILE=full USL_QA_SEED_REFRESH=1 scripts/target-reconstruct

qa-cache-resume:
	@USL_MIGRATION_PURPOSE=qa-cache USL_QA_DATA_PROFILE=full USL_QA_SEED_REFRESH=1 \
		USL_RECONSTRUCT_RESUME_ACCOUNTING=1 scripts/target-reconstruct

qa-cache-qualify-resume:
	@USL_MIGRATION_PURPOSE=qa-cache USL_QA_DATA_PROFILE=full USL_QA_SEED_REFRESH=1 \
		USL_RECONSTRUCT_RESUME_FINALIZED=1 scripts/target-reconstruct

qa-cache-prune:
	@USL_QA_SEED_PRUNE_CONFIRM="$(CONFIRM)" COMPOSE_PROJECT_NAME= scripts/qa-seed prune

expense-batch-qa-bootstrap:
	$(ODOO_DEV) bootstrap-expense-batch-qa

migration-candidate-build:
	@if [ -z "$(strip $(SOURCE_DIR))" ]; then printf 'Usage: make migration-candidate-build SOURCE_DIR=<final-source> [OUTPUT_ROOT=<private-dir>]\n' >&2; exit 2; fi
	@scripts/migration-candidate build "$(SOURCE_DIR)" $(if $(strip $(OUTPUT_ROOT)),"$(OUTPUT_ROOT)",)

migration-candidate-verify:
	@if [ -z "$(strip $(CANDIDATE))" ] || [ -z "$(strip $(FINGERPRINT))" ] || [ -z "$(strip $(SOURCE_DIR))" ]; then printf 'Usage: make migration-candidate-verify CANDIDATE=<dir> FINGERPRINT=<approved> SOURCE_DIR=<final-source>\n' >&2; exit 2; fi
	@scripts/migration-candidate verify "$(CANDIDATE)" "$(FINGERPRINT)" "$(SOURCE_DIR)"

migration-candidate-status:
	@if [ -z "$(strip $(SOURCE_DIR))" ]; then printf 'Usage: make migration-candidate-status SOURCE_DIR=<final-source> [CANDIDATE=<dir>]\n' >&2; exit 2; fi
	@scripts/migration-candidate status $(if $(strip $(CANDIDATE)),"$(CANDIDATE)","") "$(SOURCE_DIR)"

production-cutover-preflight:
	@$(call require_cutover_inputs)
	@scripts/production-cutover preflight "$(ENV_FILE)" "$(CANDIDATE)" "$(FINGERPRINT)"

production-cutover-stage:
	@$(call require_cutover_inputs)
	@scripts/production-cutover stage "$(ENV_FILE)" "$(CANDIDATE)" "$(FINGERPRINT)"

production-cutover-configure:
	@$(call require_cutover_inputs)
	@if [ -z "$(strip $(IDENTITY_POLICY))" ]; then printf 'IDENTITY_POLICY=<mode-0600-json> is required.\n' >&2; exit 2; fi
	@scripts/production-cutover configure "$(ENV_FILE)" "$(CANDIDATE)" "$(IDENTITY_POLICY)" "$(FINGERPRINT)"

production-cutover-gate:
	@$(call require_cutover_inputs)
	@if [ -z "$(strip $(JOURNEY_EVIDENCE))" ]; then printf 'JOURNEY_EVIDENCE=<mode-0600-json> is required.\n' >&2; exit 2; fi
	@scripts/production-cutover gate "$(ENV_FILE)" "$(CANDIDATE)" "$(JOURNEY_EVIDENCE)" "$(FINGERPRINT)"

production-cutover-admit:
	@$(call require_cutover_inputs)
	@scripts/production-cutover admit "$(ENV_FILE)" "$(CANDIDATE)" --confirm "$(FINGERPRINT)"

production-cutover-reset:
	@$(call require_cutover_inputs)
	@scripts/production-cutover reset "$(ENV_FILE)" "$(CANDIDATE)" --confirm "$(FINGERPRINT)"

target-finalize:
	scripts/target-finalize

target-reconstruct:
	@printf 'target-reconstruct is the production migration path.\n'
	@printf 'Use: make migrate-production SOURCE_SHA=<exact dump SHA-256>\n'
	@exit 2

migrate-production:
	@if [ -z "$(strip $(SOURCE_SHA))" ]; then \
		printf 'Usage: make migrate-production SOURCE_SHA=<exact dump SHA-256>\n' >&2; \
		exit 2; \
	fi
	USL_MIGRATION_PURPOSE=production \
		USL_MIGRATION_CONFIRM_SOURCE_SHA="$(SOURCE_SHA)" \
		USL_QA_DATA_PROFILE=full scripts/target-reconstruct

target-reconstruct-product:
	USL_MIGRATION_PURPOSE=development USL_QA_DATA_PROFILE=full \
		scripts/target-reconstruct

target-reconstruct-reuse-documents:
	USL_MIGRATION_PURPOSE=development USL_RECONSTRUCT_REUSE_DOCUMENTS=1 \
		scripts/target-reconstruct

migration-source-inventory:
	scripts/migration-source-truth inventory

migration-source-report:
	scripts/migration-source-truth report

migration-source-gate:
	scripts/migration-source-truth gate

migration-outbound-safety:
	scripts/migration-outbound-safety manual-check

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

sign-restore:
	scripts/sign-restore all

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

documents-release-build:
	scripts/documents-release-bundle build "$(SOURCE_DIR)"

documents-release-verify:
	scripts/documents-release-bundle verify "$(BUNDLE)"

documents-release-restore:
	scripts/documents-release-bundle restore "$(BUNDLE)" --project "$(PROJECT)"

documents-release-accept:
	scripts/documents-release-bundle accept "$(BUNDLE)"

documents-release-publish:
	scripts/documents-release-bundle publish "$(BUNDLE)" "$(DESTINATION)"

oca-addons-sync:
	scripts/sync-oca-addons

accounting-compat: oca-addons-sync
	$(ACCOUNTING_COMPAT) all

project-restore:
	scripts/project-restore all

sign-product-validate:
	scripts/check-sign-clean-boundary
	scripts/check-sign-worker-build

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

.PHONY: b2c-restore b2c-restore-validate b2c-restore-finalize
b2c-restore:
	COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) scripts/b2c-restore all

b2c-restore-validate:
	COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) scripts/b2c-restore validate

b2c-restore-finalize:
	COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) scripts/b2c-restore finalize

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

user-docs-build: user-docs-deps document-renderer-check
	$(USER_DOCS_PYTHON) -m mkdocs build --config-file mkdocs.yml

action-helpers:
	python3 scripts/check_action_helpers.py custom-addons

action-risk-discover:
	mkdir -p "$(dir $(ACTION_RISK_CANDIDATE))" "$(dir $(ACTION_RISK_RUNTIME_CANDIDATE))"
	docker compose -p $(COMPOSE_PROJECT) exec -T \
		-e ACTION_RISK_MODE=discover \
		-e USL_EINVOICE_LIVE_ENABLED=0 \
		-e USL_EREPORTING_LIVE_ENABLED=0 \
		odoo odoo shell --config=/etc/odoo/odoo.conf --database=$(ODOO_DEV_DB) \
		< scripts/odoo/action_risk_inventory.py \
		> "$(ACTION_RISK_RUNTIME_CANDIDATE)"
	python3 scripts/action_risk_inventory.py discover \
		--runtime "$(ACTION_RISK_RUNTIME_CANDIDATE)" \
		--output "$(ACTION_RISK_CANDIDATE)"
	@printf 'Candidate written to %s\n' "$(ACTION_RISK_CANDIDATE)"

action-risk-refresh:
	python3 scripts/action_risk_inventory.py refresh \
		--candidate "$(ACTION_RISK_CANDIDATE)"

action-risk-compile-policy:
	python3 scripts/action_risk_inventory.py compile-runtime-policy

action-risk-inventory: action-helpers
	python3 scripts/action_risk_inventory.py check-source

action-risk-runtime: action-risk-inventory
	docker compose -p $(COMPOSE_PROJECT) exec -T \
		-e ACTION_RISK_MODE=check \
		-e USL_EINVOICE_LIVE_ENABLED=0 \
		-e USL_EREPORTING_LIVE_ENABLED=0 \
		odoo odoo shell \
		--config=/etc/odoo/odoo.conf --database=$(ODOO_DEV_DB) \
		< scripts/odoo/action_risk_inventory.py

product-assets: document-renderer-check
	docker compose -p $(COMPOSE_PROJECT) exec -T odoo odoo shell \
		--config=/etc/odoo/odoo.conf --database=$(ODOO_DEV_DB) \
		< scripts/odoo/compile_product_assets.py

french-translations:
	docker compose -p $(COMPOSE_PROJECT) exec -T odoo \
		python3 - /mnt/custom-addons < scripts/check_fr_translations.py
