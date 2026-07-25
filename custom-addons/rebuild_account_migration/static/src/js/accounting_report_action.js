import { Component, onWillStart, useState } from "@odoo/owl";
import { download } from "@web/core/network/download";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useSetupAction } from "@web/search/action_hook";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

export class AccountingReportAction extends Component {
    static template = "rebuild_account_migration.AccountingReportAction";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.reportType = this.props.action.context.report_type;
        const previous = this.props.state || {};
        this.state = useState({
            loading: true,
            data: previous.data || null,
            filters: previous.filters || {},
            advancedOpen: previous.advancedOpen || false,
        });

        onWillStart(async () => {
            if (!this.state.data) {
                await this.load();
            } else {
                this.state.loading = false;
            }
        });
        useSetupAction({
            getLocalState: () => ({
                data: this.state.data,
                filters: this.state.filters,
                advancedOpen: this.state.advancedOpen,
            }),
        });
    }

    async load(changes = {}) {
        this.state.loading = true;
        try {
            const filters = { ...this.state.filters, ...changes };
            const data = await this.orm.call(
                "rebuild.account.report.export.wizard",
                "report_client_load",
                [
                    this.reportType,
                    filters,
                    this.state.data?.wizard_id || this.props.resId || false,
                ],
            );
            this.state.data = data;
            this.state.filters = { ...data.filters };
            this.props.updateActionState({ resId: data.wizard_id });
        } finally {
            this.state.loading = false;
        }
    }

    async onFilterChange(event) {
        const name = event.target.name;
        let value = event.target.value;
        if (name === "company_id") {
            value = Number(value);
        }
        const changes = { [name]: value || false };
        if (["date_from", "date_to"].includes(name)) {
            changes.period_preset = "custom";
        }
        await this.load(changes);
    }

    async onSearch(event) {
        event.preventDefault();
        await this.load({
            search_text: event.currentTarget.elements.search_text.value,
        });
    }

    async onManyFilterChange(event, fieldName) {
        const recordId = Number(event.target.value);
        const selectedIds = new Set(this.state.filters[fieldName] || []);
        if (event.target.checked) {
            selectedIds.add(recordId);
        } else {
            selectedIds.delete(recordId);
        }
        await this.load({ [fieldName]: [...selectedIds] });
    }

    async removeFilter(fieldName, recordId = false) {
        if (recordId) {
            await this.load({
                [fieldName]: (this.state.filters[fieldName] || []).filter(
                    (id) => id !== recordId,
                ),
            });
            return;
        }
        const emptyValue = fieldName === "comparison_mode" ? "none" : false;
        await this.load({ [fieldName]: emptyValue });
    }

    optionIsSelected(fieldName, recordId) {
        return (this.state.filters[fieldName] || []).includes(recordId);
    }

    optionLabel(optionName, recordId) {
        return (
            this.state.data.options[optionName].find(
                (option) => option.value === recordId,
            )?.label || String(recordId)
        );
    }

    get activeAdvancedFilterCount() {
        return [
            "journal_ids",
            "account_ids",
            "partner_ids",
            "analytic_plan_ids",
            "analytic_account_ids",
        ].reduce(
            (count, fieldName) =>
                count + (this.state.filters[fieldName] || []).length,
            0,
        );
    }

    get advancedFilterGroups() {
        const capabilities = this.state.data.capabilities;
        return [
            {
                field: "journal_ids",
                option: "journals",
                label: "Journals",
                enabled: capabilities.journals,
            },
            {
                field: "account_ids",
                option: "accounts",
                label: "Accounts",
                enabled: capabilities.accounts,
            },
            {
                field: "partner_ids",
                option: "partners",
                label: "Partners",
                enabled: capabilities.partners,
            },
            {
                field: "analytic_plan_ids",
                option: "analytic_plans",
                label: "Analytic plans",
                enabled: capabilities.analytics,
            },
            {
                field: "analytic_account_ids",
                option: "analytic_accounts",
                label: "Analytic accounts",
                enabled: capabilities.analytics,
            },
        ].filter((group) => group.enabled);
    }

    async exportReport(format) {
        const data = await this.orm.call(
            "rebuild.account.report.export.wizard",
            "report_client_export",
            [this.state.data.wizard_id, format],
        );
        await download({ url: "/web/content", data });
    }

    async toggleGroup(line) {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "rebuild.account.report.export.wizard",
                "report_client_toggle_group",
                [this.state.data.wizard_id, line.id],
            );
        } finally {
            this.state.loading = false;
        }
    }

    async openSources(line) {
        const action = await this.orm.call(
            "rebuild.account.report.export.wizard",
            "report_client_open_sources",
            [this.state.data.wizard_id, line.id],
        );
        await this.actionService.doAction(action);
    }

    formatAmount(value) {
        return new Intl.NumberFormat(undefined, {
            style: "currency",
            currency: this.state.data.currency.name,
            maximumFractionDigits: 2,
        }).format(value || 0);
    }

    formatForeignAmount(value, currency) {
        if (!currency) {
            return "";
        }
        return new Intl.NumberFormat(undefined, {
            style: "currency",
            currency,
            maximumFractionDigits: 2,
        }).format(value || 0);
    }

    formatDate(value) {
        if (!value) {
            return "";
        }
        const [year, month, day] = value.split("-").map(Number);
        return new Intl.DateTimeFormat(undefined).format(
            new Date(Date.UTC(year, month - 1, day)),
        );
    }

    formatTimestamp(value) {
        if (!value) {
            return "";
        }
        return new Intl.DateTimeFormat(undefined, {
            dateStyle: "short",
            timeStyle: "short",
        }).format(new Date(`${value.replace(" ", "T")}Z`));
    }

    isZero(value) {
        return Math.abs(Number(value || 0)) < 0.005;
    }

    get capabilities() {
        return this.state.data.capabilities;
    }

    formatCell(value, valueType) {
        if (value === undefined || value === null || value === "") {
            return "";
        }
        if (valueType === "currency") {
            return this.formatAmount(Number(value));
        }
        if (valueType === "number") {
            return new Intl.NumberFormat(undefined, {
                maximumFractionDigits: 2,
            }).format(Number(value));
        }
        if (valueType === "date") {
            return this.formatDate(value);
        }
        const labels = {
            open: "In progress",
            represented: "Represented in accounting",
            imported_posted_entry: "Posted accounting entry",
            reconciled: "Reconciled",
            unreconciled: "Unreconciled",
        };
        return labels[value] || String(value).replaceAll("_", " ");
    }

    matchingColor(reference) {
        let hash = 0;
        for (const character of reference || "") {
            hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
        }
        return `o_usl_matching_color_${hash % 10}`;
    }

    get isDetailedLedger() {
        return [
            "general_ledger",
            "partner_ledger",
            "customer_statement",
        ].includes(this.reportType);
    }

    get isTrialBalance() {
        return this.reportType === "trial_balance";
    }

    get isDynamicReport() {
        return !this.isTrialBalance && !this.isDetailedLedger;
    }

    get showOpening() {
        return this.isTrialBalance;
    }

    get showDebitCredit() {
        return [
            "trial_balance",
            "general_ledger",
            "journal_report",
            "partner_ledger",
            "customer_statement",
            "tax_report",
            "analytic_report",
        ].includes(this.reportType);
    }

    get showResidual() {
        return [
            "open_items",
            "aged_receivable",
            "aged_payable",
            "partner_ledger",
            "customer_statement",
        ].includes(this.reportType);
    }

    get showClosing() {
        return this.isTrialBalance;
    }

    get showComparison() {
        return this.state.filters.comparison_mode !== "none";
    }
}

registry
    .category("actions")
    .add("rebuild_accounting_report", AccountingReportAction);
