import { Component, onMounted, onWillStart, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { DateTimeInput } from "@web/core/datetime/datetime_input";
import {
    deserializeDate,
    serializeDate,
} from "@web/core/l10n/dates";
import { router } from "@web/core/browser/router";
import { download } from "@web/core/network/download";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useSetupAction } from "@web/search/action_hook";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const REPORT_QUERY_TO_FILTER = {
    company: "company_id",
    period: "period_preset",
    anchor: "period_anchor_date",
    date_from: "date_from",
    date_to: "date_to",
    moves: "target_move",
    comparison: "comparison_mode",
    comparison_from: "comparison_date_from",
    comparison_to: "comparison_date_to",
    group: "group_by",
    journals: "journal_ids",
    accounts: "account_ids",
    partners: "partner_ids",
    analytic_plans: "analytic_plan_ids",
    analytics: "analytic_account_ids",
    search: "search_text",
    collapsed: "collapsed_group_keys",
};
const REPORT_ID_FILTERS = new Set([
    "journal_ids",
    "account_ids",
    "partner_ids",
    "analytic_plan_ids",
    "analytic_account_ids",
]);

export function reportFiltersFromRoute(route, reportType) {
    if (!route.report) {
        return {};
    }
    if (route.report !== reportType) {
        return null;
    }
    const filters = {};
    for (const [queryKey, filterKey] of Object.entries(REPORT_QUERY_TO_FILTER)) {
        const value = route[queryKey];
        if (value === undefined || value === "") {
            continue;
        }
        if (filterKey === "company_id") {
            const companyId = Number(value);
            if (!Number.isSafeInteger(companyId) || companyId <= 0) {
                return null;
            }
            filters[filterKey] = companyId;
        } else if (REPORT_ID_FILTERS.has(filterKey)) {
            const rawIds = String(value).split(",");
            const recordIds = rawIds.map(Number);
            if (
                recordIds.some(
                    (recordId) => !Number.isSafeInteger(recordId) || recordId <= 0
                ) ||
                new Set(recordIds).size !== recordIds.length
            ) {
                return null;
            }
            filters[filterKey] = recordIds;
        } else if (filterKey === "collapsed_group_keys") {
            filters[filterKey] = String(value).split(",").filter(Boolean);
        } else {
            filters[filterKey] = value;
        }
    }
    return filters;
}

export function reportRouteFromFilters(reportType, filters) {
    return {
        report: reportType,
        company: filters.company_id,
        period: filters.period_preset,
        anchor: filters.period_anchor_date,
        date_from: filters.date_from,
        date_to: filters.date_to,
        moves: filters.target_move,
        comparison: filters.comparison_mode,
        comparison_from: filters.comparison_date_from,
        comparison_to: filters.comparison_date_to,
        group: filters.group_by,
        journals: filters.journal_ids?.slice().sort((a, b) => a - b).join(","),
        accounts: filters.account_ids?.slice().sort((a, b) => a - b).join(","),
        partners: filters.partner_ids?.slice().sort((a, b) => a - b).join(","),
        analytic_plans: filters.analytic_plan_ids?.slice().sort((a, b) => a - b).join(","),
        analytics: filters.analytic_account_ids?.slice().sort((a, b) => a - b).join(","),
        search: filters.search_text,
        collapsed: filters.collapsed_group_keys?.slice().sort().join(","),
        resId: undefined,
    };
}

export class AccountingReportAction extends Component {
    static template = "rebuild_account_migration.AccountingReportAction";
    static components = { DateTimeInput };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.navigation = useService("canonical_navigation");
        this.reportType = this.props.action.context.report_type;
        const previous = this.props.state || {};
        let routeFilters = reportFiltersFromRoute(router.current, this.reportType);
        if (
            routeFilters?.company_id &&
            routeFilters.company_id !== this.navigation.companyIds?.[0]
        ) {
            routeFilters = null;
        }
        this.state = useState({
            loading: true,
            data:
                routeFilters === null || router.current.report
                    ? null
                    : previous.data || null,
            filters:
                routeFilters === null
                    ? {}
                    : router.current.report
                    ? routeFilters
                    : previous.filters || {},
            advancedOpen: previous.advancedOpen || false,
            restorationError: routeFilters === null,
            loadError: routeFilters === null,
        });
        this.initialCanonicalLoad = true;

        onWillStart(async () => {
            if (this.state.restorationError) {
                this.notification.add(
                    "This link refers to another accounting report. No report was loaded.",
                    { type: "danger", sticky: true },
                );
                this.state.loading = false;
                return;
            }
            if (!this.state.data || router.current.report) {
                await this.load();
            } else {
                this.state.loading = false;
            }
        });
        onMounted(() => {
            browser.setTimeout(async () => {
                if (this.pendingCanonicalRoute) {
                    try {
                        await this.navigation.ensurePortable(this.pendingCanonicalRoute, {
                            history: "replace",
                        });
                    } catch {
                        this.state.data = null;
                        this.state.loadError = true;
                        this.notification.add(
                            "This report workspace could not be made portable. No broader report was loaded.",
                            { type: "danger", sticky: true },
                        );
                    } finally {
                        this.pendingCanonicalRoute = null;
                    }
                }
            }, 0);
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
        this.state.loadError = false;
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
            const canonicalRoute = {
                ...reportRouteFromFilters(this.reportType, this.state.filters),
                action: router.current.action,
            };
            if (this.initialCanonicalLoad) {
                this.pendingCanonicalRoute = canonicalRoute;
            } else {
                await this.navigation.ensurePortable(canonicalRoute, {
                    history: Object.keys(changes).length ? "push" : "replace",
                });
            }
            this.initialCanonicalLoad = false;
        } catch (error) {
            this.state.data = null;
            this.state.loadError = true;
            this.notification.add(
                "This report workspace could not be fully restored. No broader report was loaded.",
                { type: "danger", sticky: true },
            );
        } finally {
            this.state.loading = false;
        }
    }

    async onFilterChange(event) {
        const name = event.target.name;
        let value =
            event.target.type === "checkbox"
                ? event.target.checked
                : event.target.value;
        if (name === "company_id") {
            value = Number(value);
            const activation = await this.navigation.setCompanies([value]);
            if (activation.status !== "ok") {
                this.notification.add(
                    "The requested report company is not available.",
                    { type: "danger", sticky: true },
                );
                return;
            }
        }
        const changes = { [name]: value || false };
        if (["date_from", "date_to"].includes(name)) {
            changes.period_preset = "custom";
        }
        await this.load(changes);
    }

    dateFilterValue(fieldName) {
        const value = this.state.filters[fieldName];
        return value ? deserializeDate(value) : false;
    }

    async onDateFilterChange(fieldName, value) {
        const changes = {
            [fieldName]: value ? serializeDate(value) : false,
        };
        if (["date_from", "date_to"].includes(fieldName)) {
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

    async clearOptionalFilters() {
        await this.load({
            comparison_mode: "none",
            search_text: false,
            journal_ids: [],
            account_ids: [],
            partner_ids: [],
            analytic_plan_ids: [],
            analytic_account_ids: [],
            hide_zero_accounts: false,
        });
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

    filterOptionLabel(fieldName, option) {
        if (fieldName === "display_unit") {
            const symbol =
                this.state.data.currency?.symbol ||
                this.state.data.currency?.name ||
                "";
            const unitLabels = {
                units: ["Unités", symbol],
                thousands: ["Milliers", `k${symbol}`],
                millions: ["Millions", `M${symbol}`],
            };
            const [label, shortLabel] = unitLabels[option.value] || [
                option.label,
                "",
            ];
            return shortLabel ? `${label} (${shortLabel})` : label;
        }
        if (fieldName === "amount_rounding") {
            const labels = {
                whole: {
                    units: "À l’euro",
                    thousands: "Au millier d’euros",
                    millions: "Au million d’euros",
                },
                cents: {
                    units: "Au centime",
                    thousands: "Deux décimales en k€",
                    millions: "Deux décimales en M€",
                },
            };
            return (
                labels[option.value]?.[this.state.filters.display_unit] ||
                option.label
            );
        }
        const labels = {
            period_preset: {
                custom: "Dates personnalisées",
                month: "Mois",
                quarter: "Trimestre",
                fiscal_year: "Exercice",
                year_to_date: "Exercice à date",
            },
            target_move: {
                posted: "Écritures comptabilisées",
                all: "Comptabilisées et brouillons",
            },
            comparison_mode: {
                none: "Aucune comparaison",
                previous_period: "Période précédente",
                previous_year: "Même période N-1",
                custom: "Comparaison personnalisée",
            },
            group_by: {
                none: "Aucun regroupement",
                section: "Section",
                account: "Compte",
                partner: "Partenaire",
                journal: "Journal",
                month: "Mois",
                analytic: "Compte analytique",
            },
        };
        return labels[fieldName]?.[option.value] || option.label;
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

    get hasOptionalFilters() {
        return (
            this.activeAdvancedFilterCount > 0 ||
            Boolean(this.state.filters.search_text) ||
            Boolean(this.state.filters.hide_zero_accounts) ||
            this.state.filters.comparison_mode !== "none"
        );
    }

    get showCompanyFilter() {
        return this.state.data.options.companies.length > 1;
    }

    get advancedFilterGroups() {
        const capabilities = this.state.data.capabilities;
        return [
            {
                field: "journal_ids",
                option: "journals",
                label: "Journaux",
                enabled: capabilities.journals,
            },
            {
                field: "account_ids",
                option: "accounts",
                label: "Comptes",
                enabled: capabilities.accounts,
            },
            {
                field: "partner_ids",
                option: "partners",
                label: "Partenaires",
                enabled: capabilities.partners,
            },
            {
                field: "analytic_plan_ids",
                option: "analytic_plans",
                label: "Plans analytiques",
                enabled: capabilities.analytics,
            },
            {
                field: "analytic_account_ids",
                option: "analytic_accounts",
                label: "Comptes analytiques",
                enabled: capabilities.analytics,
            },
        ].filter((group) => group.enabled);
    }

    async exportReport(format) {
        const data = await this.orm.call(
            "rebuild.account.report.export.wizard",
            "report_client_export",
            [
                this.state.data.wizard_id,
                format,
                { ...this.state.filters },
            ],
        );
        this.applyExportReportPayload(data.report_payload);
        await download({ url: "/web/content", data });
    }

    applyExportReportPayload(payload) {
        if (!payload) {
            return;
        }
        this.state.data = payload;
        this.state.filters = { ...payload.filters };
        this.props?.updateActionState?.({ resId: payload.wizard_id });
    }

    async toggleGroup(line) {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "rebuild.account.report.export.wizard",
                "report_client_toggle_group",
                [this.state.data.wizard_id, line.id],
            );
            this.state.filters = { ...this.state.data.filters };
            await this.navigation.ensurePortable(
                {
                    ...reportRouteFromFilters(this.reportType, this.state.filters),
                    action: router.current.action,
                },
                { history: "replace" },
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

    async openMatchingItems(line) {
        const action = await this.orm.call(
            "account.move.line",
            "action_rebuild_open_matching_number",
            [line.matching_number, this.state.data.company_id],
        );
        await this.actionService.doAction(action);
    }

    formatAmount(value) {
        const decimalPlaces = this.amountDecimalPlaces;
        return new Intl.NumberFormat(this.state.data.locale || undefined, {
            minimumFractionDigits: decimalPlaces,
            maximumFractionDigits: decimalPlaces,
        }).format(Number(value || 0) / this.displayUnitFactor);
    }

    formatForeignAmount(value, currency) {
        if (!currency) {
            return "";
        }
        return new Intl.NumberFormat(this.state.data.locale || undefined, {
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
        return new Intl.DateTimeFormat(this.state.data.locale || undefined, {
            day: "2-digit",
            month: "2-digit",
            year: "numeric",
        }).format(
            new Date(Date.UTC(year, month - 1, day)),
        );
    }

    formatTimestamp(value) {
        if (!value) {
            return "";
        }
        return new Intl.DateTimeFormat(this.state.data.locale || undefined, {
            dateStyle: "short",
            timeStyle: "short",
        }).format(new Date(`${value.replace(" ", "T")}Z`));
    }

    isZero(value) {
        return Math.abs(Number(value || 0)) < 0.005;
    }

    isNegative(value) {
        return Number(value || 0) < -0.005;
    }

    amountClass(value, extra = "") {
        return [
            "text-end",
            extra,
            this.isZero(value) ? "o_usl_report_zero" : "",
            this.isNegative(value) ? "o_usl_report_negative" : "",
        ].filter(Boolean).join(" ");
    }

    rowClass(line) {
        return [
            "o_usl_report_row",
            `o_usl_report_role_${line.presentation_role || "detail"}`,
            line.is_group ? "o_usl_report_group" : "",
        ].filter(Boolean).join(" ");
    }

    get periodLabel() {
        return `${this.formatDate(this.state.filters.date_from)} — ${this.formatDate(
            this.state.filters.date_to,
        )}`;
    }

    get comparisonLabel() {
        if (this.state.filters.comparison_mode === "none") {
            return "";
        }
        return `${this.formatDate(
            this.state.filters.comparison_date_from,
        )} — ${this.formatDate(this.state.filters.comparison_date_to)}`;
    }

    get periodPresetLabel() {
        const option = this.state.data.options.period_preset.find(
            (candidate) => candidate.value === this.state.filters.period_preset,
        );
        return option ? this.filterOptionLabel("period_preset", option) : "";
    }

    get capabilities() {
        return this.state.data.capabilities;
    }

    get displayUnitFactor() {
        return Number(this.state.data.display_unit?.factor || 1);
    }

    get displayUnitLabel() {
        return this.state.data.display_unit?.short_label || "";
    }

    get amountDecimalPlaces() {
        return Number(this.state.data.amount_rounding?.decimal_places ?? 2);
    }

    get reportWorkspaceClass() {
        const landscapeReports = new Set([
            "trial_balance",
            "general_ledger",
            "journal_report",
            "partner_ledger",
            "customer_statement",
            "open_items",
            "aged_receivable",
            "aged_payable",
            "tax_report",
            "currency_report",
            "analytic_report",
            "fixed_assets",
            "depreciation_schedule",
            "deferred_schedule",
            "french_tax_package",
            "closing_package",
        ]);
        return [
            "o_usl_report_workspace",
            landscapeReports.has(this.reportType)
                ? "o_usl_report_workspace_landscape"
                : "o_usl_report_workspace_portrait",
        ].join(" ");
    }

    get documentThemeStyle() {
        const document = this.state.data.document || {};
        return [
            `--usl-report-primary:${document.primary_color || "#111111"}`,
            `--usl-report-section-bg:${
                document.section_background_color || "#E9ECEF"
            }`,
            `--usl-report-section-text:${
                document.section_text_color || "#111111"
            }`,
            `--usl-report-muted:${document.muted_color || "#666666"}`,
        ].join(";");
    }

    summaryCardLabel(card) {
        return card.type === "currency" && this.displayUnitLabel
            ? `${card.label} (${this.displayUnitLabel})`
            : card.label;
    }

    formatCell(value, valueType) {
        if (value === undefined || value === null || value === "") {
            return "";
        }
        if (valueType === "currency") {
            return this.formatAmount(Number(value));
        }
        if (valueType === "number") {
            return new Intl.NumberFormat(this.state.data.locale || undefined, {
                maximumFractionDigits: 2,
            }).format(Number(value));
        }
        if (valueType === "date") {
            return this.formatDate(value);
        }
        const labels = {
            open: "En cours",
            represented: "Représenté en comptabilité",
            imported_posted_entry: "Écriture comptabilisée",
            reconciled: "Lettré",
            unreconciled: "Non lettré",
            posted: "Comptabilisé",
            draft: "Brouillon",
            planned: "Planifié",
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

    get showForeignCurrency() {
        return Boolean(
            this.state.data?.lines?.some((line) => line.currency),
        );
    }

    get showMatching() {
        return Boolean(
            this.state.data?.lines?.some((line) => line.matching_number),
        );
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
