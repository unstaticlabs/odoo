.DEFAULT_GOAL := help

COMPOSE_PROJECT ?= usl-odoo-saas-19-3
export COMPOSE_PROJECT_NAME := $(COMPOSE_PROJECT)
ODOO_DEV ?= scripts/odoo-dev
ODOO_DEV_DB ?= odoo_dev
MODULE ?=
SERVICE ?=
TARGET ?= local
SOURCE ?= production
SNAPSHOT ?=
ACCOUNTING_TEST_DB ?= odoo_rebuild_accounting_unit_$(shell date -u +%Y%m%d%H%M%S)
ACCOUNTING_TEST_TAGS ?= rebuild_account_migration_unit
ACCOUNTING_TEST_LOG_LEVEL ?= warn
USER_DOCS_HOST ?= 127.0.0.1
USER_DOCS_PORT ?= 8079
USER_DOCS_VENV ?= .venv-docs
USER_DOCS_PYTHON ?= $(USER_DOCS_VENV)/bin/python
ACTION_RISK_CANDIDATE ?= artifacts/action-risk/action_surface.candidate.json
ACTION_RISK_RUNTIME_CANDIDATE ?= artifacts/action-risk/runtime.candidate.json

.PHONY: help doctor status dev deploy rebuild logs stop login-link
.PHONY: configure-pocket-id repair-pocket-id paperless-users disable-tours
.PHONY: oca-addons-sync document-renderer-certs document-renderer-check
.PHONY: product-migration-source-boundary product-migration-boundary sign-product-validate
.PHONY: accounting-addon-tests accounting-multicompany-acceptance
.PHONY: user-docs-deps user-docs-serve user-docs-build
.PHONY: action-helpers action-risk-discover action-risk-refresh action-risk-compile-policy
.PHONY: action-risk-inventory action-risk-runtime product-assets french-translations
.PHONY: expense-batch-qa-bootstrap tese-qa-bootstrap
.PHONY: backup restore smoke health qa-refresh

help:
	@printf '%s\n' \
	  '' \
	  'USL Odoo Distribution — local development' \
	  '' \
	  '  make doctor                         Diagnose ownership and configuration' \
	  '  make dev                            Start the development runtime' \
	  '  make deploy [MODULE=module_name]    Update mounted add-ons' \
	  '  make rebuild [MODULE=module_name]   Rebuild the image, then deploy' \
	  '  make status                         Show health and URLs' \
	  '  make logs [SERVICE=odoo]            Follow service logs' \
	  '  make stop                           Stop containers and preserve data' \
	  '  make login-link USER=username       Create a local one-time sign-in link' \
	  '  make backup TARGET=production       Capture a coordinated recovery cohort' \
	  '  make restore TARGET=staging SNAPSHOT=<full-id>  Restore fresh staging volumes' \
	  '  make health TARGET=production       Run fast read-only runtime checks' \
	  '  make smoke TARGET=staging           Run read-only business controls' \
	  '  make product-migration-boundary     Check the delivered product boundary' \
	  '  make accounting-addon-tests         Run focused Accounting module tests' \
	  '  make user-docs-build                Render user documentation' \
	  '' \
	  'Migration and cutover use migration/manage exclusively.' \
	  'Run migration/manage --help for its lifecycle commands.'

backup:
	@scripts/usl-stack backup create --target "$(TARGET)"

restore:
	@if [ -z "$(strip $(SNAPSHOT))" ]; then printf 'Usage: make restore TARGET=<target> SOURCE=<source> SNAPSHOT=<full-id>\n' >&2; exit 2; fi
	@scripts/usl-stack restore run --source "$(SOURCE)" --target "$(TARGET)" \
		--snapshot "$(SNAPSHOT)" --replace --confirm "$(TARGET)"

qa-refresh:
	@if [ -z "$(strip $(SNAPSHOT))" ]; then printf 'Usage: make qa-refresh SNAPSHOT=<full-id>\n' >&2; exit 2; fi
	@$(MAKE) restore TARGET=staging SOURCE=production SNAPSHOT="$(SNAPSHOT)"

health:
	@scripts/usl-stack health --target "$(TARGET)"

smoke:
	@scripts/usl-stack smoke --target "$(TARGET)"

doctor:
	@$(ODOO_DEV) doctor

status:
	@$(ODOO_DEV) status

dev:
	@$(ODOO_DEV) start

deploy:
	@if [ -n "$(strip $(MODULE))" ]; then $(ODOO_DEV) deploy "$(MODULE)"; else $(ODOO_DEV) deploy; fi

rebuild:
	@if [ -n "$(strip $(MODULE))" ]; then $(ODOO_DEV) rebuild "$(MODULE)"; else $(ODOO_DEV) rebuild; fi

logs:
	@if [ -n "$(strip $(SERVICE))" ]; then $(ODOO_DEV) logs "$(SERVICE)"; else $(ODOO_DEV) logs; fi

stop:
	@$(ODOO_DEV) stop

login-link:
	@if [ "$(origin USER)" != "command line" ] || [ -z "$(strip $(USER))" ]; then \
		printf 'Usage: make login-link USER=<Pocket ID username>\n' >&2; exit 2; \
	fi
	@scripts/pocket-id-dev one-time-link "$(USER)"

configure-pocket-id:
	@$(ODOO_DEV) configure-pocket-id

repair-pocket-id:
	@$(ODOO_DEV) repair-pocket-id

paperless-users:
	@scripts/pocket-id-dev sync-paperless-users

disable-tours:
	@$(ODOO_DEV) disable-tours

expense-batch-qa-bootstrap:
	@$(ODOO_DEV) bootstrap-expense-batch-qa

tese-qa-bootstrap:
	@$(ODOO_DEV) bootstrap-tese-payroll-qa 01

oca-addons-sync:
	@scripts/sync-oca-addons

document-renderer-certs:
	@scripts/generate-document-renderer-certs

document-renderer-check:
	@scripts/check-document-renderer-submodule

product-migration-source-boundary:
	@scripts/check-product-migration-boundary

product-migration-boundary: product-migration-source-boundary
	@scripts/check-product-database-boundary

sign-product-validate:
	@scripts/check-sign-clean-boundary
	@scripts/check-sign-worker-build

accounting-multicompany-acceptance:
	@docker compose -p $(COMPOSE_PROJECT) exec -T \
		-e USL_EINVOICE_LIVE_ENABLED=0 -e USL_EREPORTING_LIVE_ENABLED=0 \
		odoo odoo shell --config=/etc/odoo/odoo.conf --database=$(ODOO_DEV_DB) \
		< scripts/odoo/multicompany_accounting_acceptance.py

accounting-addon-tests: oca-addons-sync document-renderer-certs
	@docker compose -p $(COMPOSE_PROJECT) --profile document-renderer up -d --wait usl-document-renderer
	@docker compose -p $(COMPOSE_PROJECT) --profile test run --rm \
		-e ODOO_INIT_DB=$(ACCOUNTING_TEST_DB) test odoo \
		--config=/etc/odoo/odoo.conf --database=$(ACCOUNTING_TEST_DB) \
		--init=rebuild_account_migration --without-demo=true --test-enable \
		--test-tags=$(ACCOUNTING_TEST_TAGS) --stop-after-init \
		--log-level=$(ACCOUNTING_TEST_LOG_LEVEL)

$(USER_DOCS_VENV)/.requirements-ready: requirements-docs.txt
	python3 -m venv $(USER_DOCS_VENV)
	$(USER_DOCS_VENV)/bin/python -m pip install --disable-pip-version-check --requirement requirements-docs.txt
	touch $(USER_DOCS_VENV)/.requirements-ready

user-docs-deps: $(USER_DOCS_VENV)/.requirements-ready

user-docs-serve: user-docs-deps
	@$(USER_DOCS_PYTHON) -m mkdocs serve --config-file mkdocs.yml --dev-addr $(USER_DOCS_HOST):$(USER_DOCS_PORT)

user-docs-build: user-docs-deps document-renderer-check
	@$(USER_DOCS_PYTHON) -m mkdocs build --config-file mkdocs.yml

action-helpers:
	@python3 scripts/check_action_helpers.py custom-addons

action-risk-discover:
	@mkdir -p "$(dir $(ACTION_RISK_CANDIDATE))" "$(dir $(ACTION_RISK_RUNTIME_CANDIDATE))"
	@docker compose -p $(COMPOSE_PROJECT) exec -T \
		-e ACTION_RISK_MODE=discover -e USL_EINVOICE_LIVE_ENABLED=0 \
		-e USL_EREPORTING_LIVE_ENABLED=0 odoo odoo shell \
		--config=/etc/odoo/odoo.conf --database=$(ODOO_DEV_DB) \
		< scripts/odoo/action_risk_inventory.py > "$(ACTION_RISK_RUNTIME_CANDIDATE)"
	@python3 scripts/action_risk_inventory.py discover \
		--runtime "$(ACTION_RISK_RUNTIME_CANDIDATE)" --output "$(ACTION_RISK_CANDIDATE)"

action-risk-refresh:
	@python3 scripts/action_risk_inventory.py refresh --candidate "$(ACTION_RISK_CANDIDATE)"

action-risk-compile-policy:
	@python3 scripts/action_risk_inventory.py compile-runtime-policy

action-risk-inventory: action-helpers
	@python3 scripts/action_risk_inventory.py check-source

action-risk-runtime: action-risk-inventory
	@docker compose -p $(COMPOSE_PROJECT) exec -T \
		-e ACTION_RISK_MODE=check -e USL_EINVOICE_LIVE_ENABLED=0 \
		-e USL_EREPORTING_LIVE_ENABLED=0 odoo odoo shell \
		--config=/etc/odoo/odoo.conf --database=$(ODOO_DEV_DB) \
		< scripts/odoo/action_risk_inventory.py

product-assets: document-renderer-check
	@docker compose -p $(COMPOSE_PROJECT) exec -T odoo odoo shell \
		--config=/etc/odoo/odoo.conf --database=$(ODOO_DEV_DB) \
		< scripts/odoo/compile_product_assets.py

french-translations:
	@docker compose -p $(COMPOSE_PROJECT) exec -T odoo \
		python3 - /mnt/custom-addons < scripts/check_fr_translations.py
