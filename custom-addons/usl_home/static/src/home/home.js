import { _t } from "@web/core/l10n/translation";
import { formatDate } from "@web/core/l10n/dates";
import { registry } from "@web/core/registry";
import { user, userBus } from "@web/core/user";
import { useBus, useService } from "@web/core/utils/hooks";
import { useSortable } from "@web/core/utils/sortable_owl";
import { Component, onMounted, onWillStart, useRef, useState } from "@odoo/owl";

const { DateTime } = luxon;

const WIDGET_METHODS = {
    activities: "get_activities",
    my_tasks: "get_my_tasks",
    ai_pipelines: "get_ai_attention",
    accounting: "get_accounting_alerts",
};

const WIDGET_LABELS = {
    activities: _t("Activities"),
    my_tasks: _t("My Tasks"),
    favorites: _t("Favorite Views"),
    ai_pipelines: _t("AI Pipelines"),
    accounting: _t("Accounting & Compliance Alerts"),
};

const WIDGET_ICONS = {
    activities: "fa-clock-o",
    my_tasks: "fa-check-square-o",
    favorites: "fa-bookmark-o",
    ai_pipelines: "fa-magic",
    accounting: "fa-shield",
};

const FAVORITE_ICONS = {
    tasks: "fa-check-square-o",
    project: "fa-folder-open-o",
    accounting: "fa-balance-scale",
    ai: "fa-magic",
    view: "fa-filter",
    record: "fa-file-text-o",
    destination: "fa-location-arrow",
};

const ACCOUNTING_ICONS = {
    closing: "fa-lock",
    declarations: "fa-calendar-check-o",
    reviews: "fa-eye",
    bank: "fa-bank",
    evidence: "fa-paperclip",
    hygiene: "fa-shield",
};

export class UslHome extends Component {
    static template = "usl_home.Home";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.gridRef = useRef("grid");
        this.state = useState({
            loadingConfiguration: true,
            configurationError: false,
            customize: false,
            layout: { version: 1, order: [], hidden: [] },
            availableWidgets: [],
            favorites: [],
            availableDestinations: [],
            activeCompany: null,
            companyScope: null,
            widgets: {},
        });

        useSortable({
            ref: this.gridRef,
            elements: ".o_usl_home_widget",
            handle: ".o_usl_home_drag",
            cursor: "move",
            onDrop: ({ parent }) => {
                const order = [...parent.querySelectorAll(".o_usl_home_widget")].map(
                    (element) => element.dataset.widget
                );
                this.saveOrder(order);
            },
        });
        useBus(userBus, "ACTIVE_COMPANIES_CHANGED", () => this.reloadForCompany());

        onWillStart(() => this.loadConfiguration());
        onMounted(() => this.refreshAll());
    }

    get visibleWidgets() {
        return this.state.layout.order.filter(
            (key) =>
                this.state.availableWidgets.includes(key) &&
                !this.state.layout.hidden.includes(key)
        );
    }

    get configurableWidgets() {
        return this.state.layout.order.map((key) => ({
            key,
            label: WIDGET_LABELS[key],
            visible: !this.state.layout.hidden.includes(key),
        }));
    }

    widgetState(key) {
        return this.state.widgets[key] || { loading: true, error: false, data: null };
    }

    widgetLabel(key) {
        return WIDGET_LABELS[key];
    }

    widgetIcon(key) {
        return WIDGET_ICONS[key] || "fa-circle-o";
    }

    widgetScopeLabel(key) {
        const scope = this.state.companyScope;
        if (!scope?.combined) {
            return {
                activities: _t("What needs you now"),
                my_tasks: _t("Your workload by real project state"),
                favorites: _t("Resume the exact place you use"),
                ai_pipelines: _t("Human review, blockers, and failures"),
            }[key];
        }
        if (key === "favorites") {
            return _t("Personal destinations; company-specific links are labeled");
        }
        return _t("Combined across %s selected companies", scope.companies.length);
    }

    companyScopeNames() {
        return (this.state.companyScope?.companies || [])
            .map((company) => company.name)
            .join(" · ");
    }

    accountingBreakdown(alert) {
        return (alert.companies || [])
            .map((company) => `${company.name}: ${company.count}`)
            .join(" · ");
    }

    favoriteIcon(key) {
        return FAVORITE_ICONS[key] || FAVORITE_ICONS.destination;
    }

    accountingIcon(key) {
        return ACCOUNTING_ICONS[key] || "fa-exclamation-circle";
    }

    async loadConfiguration() {
        this.state.loadingConfiguration = true;
        this.state.configurationError = false;
        try {
            const configuration = await this.orm.call(
                "usl.home.service",
                "get_configuration",
                []
            );
            this.state.layout = configuration.layout;
            this.state.availableWidgets = configuration.available_widgets;
            this.state.favorites = configuration.favorites;
            this.state.availableDestinations = configuration.available_destinations;
            this.state.activeCompany = configuration.active_company;
            this.state.companyScope = configuration.company_scope || {
                mode: "single",
                combined: false,
                active_company: configuration.active_company,
                companies: [configuration.active_company],
                label: configuration.active_company?.name,
            };
            for (const key of configuration.available_widgets) {
                if (!this.state.widgets[key]) {
                    this.state.widgets[key] = { loading: true, error: false, data: null };
                }
            }
        } catch {
            this.state.configurationError = true;
        } finally {
            this.state.loadingConfiguration = false;
        }
    }

    async refreshAll() {
        for (const key of this.visibleWidgets) {
            if (key !== "favorites") {
                this.loadWidget(key);
            }
        }
    }

    async loadWidget(key) {
        const method = WIDGET_METHODS[key];
        if (!method) {
            return;
        }
        const widget = this.state.widgets[key];
        widget.loading = true;
        widget.error = false;
        try {
            widget.data = await this.orm.call("usl.home.service", method, []);
        } catch {
            widget.error = true;
            widget.data = null;
        } finally {
            widget.loading = false;
        }
    }

    async reloadForCompany() {
        await this.loadConfiguration();
        await this.refreshAll();
    }

    async saveLayout(layout) {
        try {
            this.state.layout = await this.orm.call("usl.home.service", "save_layout", [
                layout,
            ]);
        } catch {
            this.notification.add(_t("Your Home layout could not be saved."), {
                type: "danger",
            });
            await this.loadConfiguration();
        }
    }

    saveOrder(order) {
        const remaining = this.state.layout.order.filter((key) => !order.includes(key));
        return this.saveLayout({
            version: 1,
            order: [...order, ...remaining],
            hidden: this.state.layout.hidden,
        });
    }

    toggleWidget(key) {
        const hidden = new Set(this.state.layout.hidden);
        if (hidden.has(key)) {
            hidden.delete(key);
            if (key !== "favorites") {
                this.loadWidget(key);
            }
        } else {
            hidden.add(key);
        }
        return this.saveLayout({
            version: 1,
            order: this.state.layout.order,
            hidden: [...hidden],
        });
    }

    moveWidget(key, offset) {
        const order = [...this.state.layout.order];
        const from = order.indexOf(key);
        const to = from + offset;
        if (from < 0 || to < 0 || to >= order.length) {
            return;
        }
        [order[from], order[to]] = [order[to], order[from]];
        return this.saveOrder(order);
    }

    formatDate(value) {
        return value ? formatDate(DateTime.fromISO(value)) : "";
    }

    activityTiming(item) {
        if (item.bucket === "overdue") {
            return _t("Overdue · %s", this.formatDate(item.deadline));
        }
        if (item.bucket === "today") {
            return _t("Today");
        }
        return this.formatDate(item.deadline);
    }

    async openRecord(resModel, resId) {
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: resModel,
            res_id: resId,
            views: [[false, "form"]],
            view_mode: "form",
            target: "current",
        });
    }

    openAllActivities() {
        return this.action.doAction("mail.mail_activity_action_my");
    }

    openMyTasks() {
        return this.action.doAction("project.action_view_my_task");
    }

    taskMetricAriaLabel(label, count) {
        return _t("Open %s tasks (%s)", label, count);
    }

    async openMyTasksFilter(filterType, filterValue) {
        const action = await this.orm.call(
            "usl.home.service",
            "get_my_tasks_action",
            [filterType, filterValue]
        );
        return this.action.doAction(action);
    }

    async openAiPipelines() {
        const action = await this.orm.call("usl.home.service", "get_ai_workspace_action", []);
        return this.action.doAction(action);
    }

    async openAccountingAlert(key) {
        const action = await this.orm.call(
            "usl.home.service",
            "get_accounting_alert_action",
            [key]
        );
        return this.action.doAction(action);
    }

    async openFavorite(favorite) {
        if (!favorite.available) {
            return;
        }
        const target = await this.orm.call("usl.home.service", "resolve_favorite", [
            favorite.id,
        ]);
        if (!target.available) {
            favorite.available = false;
            favorite.name = _t("Destination unavailable");
            return;
        }
        if (target.company_id && user.activeCompany?.id !== target.company_id) {
            const selected = user.activeCompanies.map((company) => company.id);
            await user.activateCompanies(
                [target.company_id, ...selected.filter((id) => id !== target.company_id)],
                { includeChildCompanies: false, reload: false }
            );
        }
        return this.action.doAction(target.action);
    }

    async removeFavorite(favorite) {
        await this.orm.unlink("usl.home.favorite", [favorite.id]);
        this.state.favorites = this.state.favorites.filter((item) => item.id !== favorite.id);
        await this.refreshDestinations();
    }

    async moveFavorite(favorite, offset) {
        const favorites = [...this.state.favorites];
        const from = favorites.findIndex((item) => item.id === favorite.id);
        const to = from + offset;
        if (from < 0 || to < 0 || to >= favorites.length) {
            return;
        }
        [favorites[from], favorites[to]] = [favorites[to], favorites[from]];
        this.state.favorites = favorites;
        await this.orm.call("usl.home.favorite", "reorder", [
            favorites.map((item) => item.id),
        ]);
    }

    async addProvider(key) {
        const favorite = await this.orm.call(
            "usl.home.service",
            "add_provider_favorite",
            [key]
        );
        this.state.favorites.push(favorite);
        await this.refreshDestinations();
    }

    async refreshDestinations() {
        const configuration = await this.orm.call(
            "usl.home.service",
            "get_configuration",
            []
        );
        this.state.availableDestinations = configuration.available_destinations;
    }
}

registry.category("actions").add("usl_home.Home", UslHome);
