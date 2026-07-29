import { onMounted } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { router } from "@web/core/browser/router";
import { _t } from "@web/core/l10n/translation";
import { Notebook } from "@web/core/notebook/notebook";
import { user } from "@web/core/user";
import { patch } from "@web/core/utils/patch";
import { useBus, useService } from "@web/core/utils/hooks";
import { DynamicList } from "@web/model/relational_model/dynamic_list";
import { SearchBarMenu } from "@web/search/search_bar_menu/search_bar_menu";
import { SearchModel } from "@web/search/search_model";
import { CalendarModel } from "@web/views/calendar/calendar_model";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { ListController } from "@web/views/list/list_controller";
import { ListRenderer } from "@web/views/list/list_renderer";
import {
    NAVIGATION_VERSION,
    navigationPatch,
    normalizeIds,
    parsePanelState,
    writePortableRoute,
} from "./navigation_state";

function activeCompanyIds() {
    return user.activeCompanies.map((company) => company.id);
}

function commit(patchValue, { history = "replace" } = {}) {
    const method = history === "push" ? "pushState" : "replaceState";
    writePortableRoute(() =>
        router[method](navigationPatch(patchValue, activeCompanyIds()), { sync: true })
    );
}

function canonicalPanelState(searchModel) {
    if (!searchModel.display?.searchPanel || !searchModel.sections) {
        return {};
    }
    const panel = {};
    for (const section of searchModel.sections.values()) {
        if (section.type === "category" && section.activeValueId) {
            panel[section.fieldName] = section.activeValueId;
        } else if (section.type === "filter") {
            const checked = [
                ...new Set(
                    [...section.values.values()]
                        .filter((value) => value.checked)
                        .map((value) => value.id)
                ),
            ].sort((left, right) =>
                String(left).localeCompare(String(right), undefined, { numeric: true })
            );
            if (checked.length) {
                panel[section.fieldName] = checked;
            }
        }
    }
    return panel;
}

function canonicalSearchState(searchModel, { includePanel = true } = {}) {
    const { preFavorite } = searchModel._getIrFilterDescription();
    const activeFavorite = searchModel.query
        .map(({ searchItemId }) => searchModel.searchItems[searchItemId])
        .find((item) => item?.type === "favorite" && item.serverSideId);
    const state = {
        domain: preFavorite.domain === "[]" ? undefined : preFavorite.domain,
        groupBy: preFavorite.groupBys.length ? preFavorite.groupBys : undefined,
        orderBy: preFavorite.orderBy.length ? preFavorite.orderBy : undefined,
        favorite: activeFavorite?.serverSideId,
        offset: undefined,
        selection: undefined,
    };
    if (includePanel && searchModel.display?.searchPanel) {
        state.panel = canonicalPanelState(searchModel);
    }
    return state;
}

function searchStateMatchesRoute(state) {
    const normalized = navigationPatch(state, activeCompanyIds());
    return ["domain", "groupBy", "orderBy", "favorite", "panel"].every((key) => {
        // An omitted search-panel parameter means "use the action defaults".
        // Once a user changes the panel, even an empty object is written so
        // clearing a default selection remains portable.
        if (key === "panel" && router.current.panel === undefined) {
            return true;
        }
        const expected = normalized[key];
        const actual = router.current[key];
        return expected === undefined ? actual === undefined : String(expected) === String(actual);
    });
}

function canonicalSearchRouteSignature() {
    return JSON.stringify(
        ["domain", "groupBy", "orderBy", "favorite", "panel"].map(
            (key) => router.current[key] ?? null
        )
    );
}

function markCanonicalSearchChange(searchModel) {
    if (!searchModel.blockNotification) {
        searchModel.canonicalCommitPending = true;
    }
}

function visibleOptionalColumns(renderer) {
    return (
        Object.keys(renderer.optionalActiveFields)
            .filter((fieldName) => renderer.optionalActiveFields[fieldName])
            .sort()
            .join(",") || "-"
    );
}

async function persistCanonicalSelection(controller) {
    if (!controller.canonicalSelectionReady) {
        return;
    }
    const root = controller.model.root;
    if (root.isDomainSelected) {
        if (router.current.selection_mode === "domain") {
            return;
        }
        await controller.canonicalNavigation.ensurePortable(
            {
                selection: undefined,
                selection_mode: "domain",
            },
            { history: "replace", sensitive: true }
        );
    } else {
        const selectedIds = normalizeIds(
            root.selection.map((record) => record.resId)
        );
        const routedIds = normalizeIds(router.current.selection);
        if (
            router.current.selection_mode === undefined &&
            selectedIds.length === routedIds.length &&
            selectedIds.every((recordId, index) => recordId === routedIds[index])
        ) {
            return;
        }
        await controller.canonicalNavigation.ensurePortable(
            {
                selection: selectedIds,
                selection_mode: undefined,
            },
            { history: "replace" }
        );
    }
}

async function restoreCanonicalCollection(controller, { restoreSearch = false } = {}) {
    if (
        controller.canonicalListRestoring ||
        !controller.model.isReady ||
        Number(router.current.nv) !== NAVIGATION_VERSION ||
        (router.current.resId && router.current.resId !== "new")
    ) {
        return;
    }
    controller.canonicalListRestoring = true;
    try {
        const searchRouteSignature = canonicalSearchRouteSignature();
        if (
            restoreSearch &&
            controller.canonicalSearchRouteSignature !== searchRouteSignature
        ) {
            await controller.env.searchModel.restoreCanonicalRoute?.();
            await new Promise((resolve) => browser.setTimeout(resolve, 0));
            await controller.model.mutex.getUnlockedDef();
            controller.canonicalSearchRouteSignature = searchRouteSignature;
        }
        const offset = Number(router.current.offset || 0);
        const limit = Number(router.current.limit);
        const routeLoad = {};
        if (
            router.current.offset !== undefined &&
            Number.isSafeInteger(offset) &&
            offset >= 0
        ) {
            routeLoad.offset = offset;
        }
        if (
            router.current.limit !== undefined &&
            Number.isSafeInteger(limit) &&
            limit > 0
        ) {
            routeLoad.limit = limit;
        }
        if (Object.keys(routeLoad).length) {
            const routedRoot = controller.model.root;
            await routedRoot.load(routeLoad);
            if (controller.model.root !== routedRoot) {
                controller.canonicalRestoreAgain = true;
                return;
            }
        }
        if (router.current.selection_mode === "domain") {
            await controller.model.root.selectDomain(true);
            controller.canonicalSelectionReady = true;
            return;
        }
        const selectedIds = new Set(normalizeIds(router.current.selection));
        if (!selectedIds.size || controller.model.root.isDomainSelected) {
            controller.canonicalSelectionReady = true;
            return;
        }
        for (const record of controller.model.root.records) {
            if (selectedIds.has(record.resId) && !record.selected) {
                await record.toggleSelection(true);
            }
        }
        controller.canonicalSelectionReady = true;
    } finally {
        controller.canonicalListRestoring = false;
        if (controller.canonicalRestoreAgain) {
            controller.canonicalRestoreAgain = false;
            browser.setTimeout(
                () => restoreCanonicalCollection(controller, { restoreSearch }),
                0
            );
        }
    }
}

function withCanonicalRootLoadHook(controller, params, restore) {
    const onRootLoaded = params.hooks?.onRootLoaded;
    return {
        ...params,
        hooks: {
            ...params.hooks,
            async onRootLoaded(root) {
                await onRootLoaded?.(root);
                if (!controller.canonicalListRestoring) {
                    browser.setTimeout(restore, 0);
                }
            },
        },
    };
}

patch(SearchModel.prototype, {
    setup() {
        super.setup(...arguments);
        this.canonicalNavigation = useService("canonical_navigation");
    },

    async load(config) {
        const panel = parsePanelState(router.current.panel);
        if (Number(router.current.nv) === NAVIGATION_VERSION && router.current.panel !== undefined) {
            const context = Object.fromEntries(
                Object.entries(config.context || {}).filter(
                    ([key]) => !key.startsWith("searchpanel_default_")
                )
            );
            if (panel) {
                for (const [fieldName, value] of Object.entries(panel)) {
                    context[`searchpanel_default_${fieldName}`] = value;
                }
            } else {
                config = {
                    ...config,
                    domain: [...(config.domain || []), ["id", "=", 0]],
                };
            }
            config = { ...config, context };
        }
        this.canonicalLoadConfig = { ...config, state: undefined };
        if (Number(router.current.nv) === NAVIGATION_VERSION && config.state) {
            // Browser history state is an optimization only.  A canonical URL is
            // authoritative, including when it intentionally contains no custom
            // search facets and should therefore restore the action defaults.
            config = { ...config, state: undefined };
        }
        const result = await super.load(config);
        if (
            Number(router.current.nv) === NAVIGATION_VERSION &&
            router.current.panel !== undefined
        ) {
            await this.sectionsPromise;
            const restoredPanel = canonicalPanelState(this);
            const panelUnavailable =
                panel === null ||
                Object.keys(panel).some((fieldName) => {
                    const requested = panel[fieldName];
                    const restored = restoredPanel[fieldName];
                    return JSON.stringify(requested) !== JSON.stringify(restored);
                });
            if (panelUnavailable) {
                this.query = [];
                this.createNewFilters([
                    {
                        description: _t("Workspace could not be fully restored"),
                        domain: "[('id', '=', 0)]",
                        name: "canonicalNavigationFailClosed",
                    },
                ]);
                this.notificationService.add(
                    _t(
                        "This workspace contains an unavailable search-panel selection. No records were shown."
                    ),
                    { type: "danger", sticky: true }
                );
            }
        }
        return result;
    },

    async restoreCanonicalRoute() {
        if (
            this.canonicalRestoring ||
            Number(router.current.nv) !== NAVIGATION_VERSION ||
            searchStateMatchesRoute(canonicalSearchState(this))
        ) {
            return;
        }
        this.canonicalRestoring = true;
        try {
            await this.load(this.canonicalLoadConfig);
            this.trigger("update");
        } finally {
            this.canonicalRestoring = false;
        }
    },

    _tryApplySharedFilters(urlDomain, urlGroupBy, urlOrderBy) {
        if (Number(router.current.nv) !== NAVIGATION_VERSION) {
            return super._tryApplySharedFilters(...arguments);
        }
        const status = { anyErrors: false, anySuccess: false };
        this._createUrlFilter(urlDomain, status);
        this._createUrlGroupBy(urlGroupBy, status);
        this._createUrlOrderBy(urlOrderBy, status);
        if (!status.anyErrors && status.anySuccess) {
            return true;
        }

        // A malformed canonical filter must never fall through to a broader
        // default favorite. Replace every partially applied URL facet with a
        // universally empty record domain.
        this.query = [];
        this.createNewFilters([
            {
                description: _t("Workspace could not be fully restored"),
                domain: "[('id', '=', 0)]",
                name: "canonicalNavigationFailClosed",
            },
        ]);
        this.notificationService.add(
            _t("This workspace contains an invalid or unavailable filter. No records were shown."),
            { type: "danger", sticky: true }
        );
        return true;
    },

    async _notify() {
        const shouldCommit = this.canonicalCommitPending;
        this.canonicalCommitPending = false;
        const result = await super._notify(...arguments);
        if (shouldCommit && !this.blockNotification && !this.canonicalNavigation.blocked) {
            const state = canonicalSearchState(this);
            if (!searchStateMatchesRoute(state)) {
                await this.canonicalNavigation.ensurePortable(state, { history: "push" });
            }
        }
        return result;
    },

    addAutoCompletionValues() {
        markCanonicalSearchChange(this);
        return super.addAutoCompletionValues(...arguments);
    },

    applySearch() {
        markCanonicalSearchChange(this);
        return super.applySearch(...arguments);
    },

    clearQuery() {
        markCanonicalSearchChange(this);
        return super.clearQuery(...arguments);
    },

    clearFilters() {
        markCanonicalSearchChange(this);
        return super.clearFilters(...arguments);
    },

    createNewFavorite() {
        markCanonicalSearchChange(this);
        return super.createNewFavorite(...arguments);
    },

    createNewGroupBy() {
        markCanonicalSearchChange(this);
        return super.createNewGroupBy(...arguments);
    },

    deactivateGroup() {
        markCanonicalSearchChange(this);
        return super.deactivateGroup(...arguments);
    },

    splitAndAddDomain() {
        markCanonicalSearchChange(this);
        return super.splitAndAddDomain(...arguments);
    },

    toggleCategoryValue() {
        markCanonicalSearchChange(this);
        return super.toggleCategoryValue(...arguments);
    },

    toggleDateGroupBy() {
        markCanonicalSearchChange(this);
        return super.toggleDateGroupBy(...arguments);
    },

    toggleFilterValues() {
        markCanonicalSearchChange(this);
        return super.toggleFilterValues(...arguments);
    },

    toggleParentFilter() {
        markCanonicalSearchChange(this);
        return super.toggleParentFilter(...arguments);
    },

    toggleSearchItem() {
        markCanonicalSearchChange(this);
        return super.toggleSearchItem(...arguments);
    },

    clearSections() {
        markCanonicalSearchChange(this);
        return super.clearSections(...arguments);
    },
});

patch(SearchBarMenu.prototype, {
    async shareViewUrl() {
        const shareUrl = browser.location.href.replace("/scoped_app", "/odoo");
        try {
            await browser.navigator.clipboard.writeText(shareUrl);
            this.notificationService.add(_t("Workspace link copied to clipboard"), {
                type: "success",
            });
        } catch {
            this.notificationService.add(
                _t("Copy the visible address-bar URL to share this workspace."),
                { type: "warning", sticky: true }
            );
        }
    },
});

patch(ListController.prototype, {
    get modelParams() {
        let params = super.modelParams;
        const limit = Number(router.current.limit);
        if (Number.isSafeInteger(limit) && limit > 0) {
            params.limit = limit;
        }
        params = withCanonicalRootLoadHook(
            this,
            params,
            () => this.restoreCanonicalListState()
        );
        return params;
    },

    setup() {
        super.setup(...arguments);
        this.canonicalNavigation = useService("canonical_navigation");
        this.canonicalSelectionReady = false;
        useBus(this.env.bus, "ACTION_MANAGER:UI-UPDATED", () =>
            this.restoreCanonicalListState()
        );
    },

    async restoreCanonicalListState() {
        return restoreCanonicalCollection(this, { restoreSearch: true });
    },

    async onSelectionChanged() {
        const result = await super.onSelectionChanged(...arguments);
        await persistCanonicalSelection(this);
        return result;
    },
});

patch(KanbanController.prototype, {
    get modelParams() {
        let params = super.modelParams;
        const limit = Number(router.current.limit);
        if (Number.isSafeInteger(limit) && limit > 0) {
            params.limit = limit;
        }
        params = withCanonicalRootLoadHook(
            this,
            params,
            () => restoreCanonicalCollection(this, { restoreSearch: true })
        );
        return params;
    },

    setup() {
        super.setup(...arguments);
        this.canonicalNavigation = useService("canonical_navigation");
        this.canonicalSelectionReady = false;
        useBus(this.env.bus, "ACTION_MANAGER:UI-UPDATED", () =>
            restoreCanonicalCollection(this, { restoreSearch: true })
        );
    },

    async onSelectionChanged() {
        const result = await super.onSelectionChanged(...arguments);
        await persistCanonicalSelection(this);
        return result;
    },
});

patch(DynamicList.prototype, {
    async load(params = {}) {
        const result = await super.load(...arguments);
        if (this.config.isRoot && Array.isArray(this.records)) {
            const selectedIds = new Set(normalizeIds(router.current.selection));
            for (const record of this.records) {
                record._toggleSelection(selectedIds.has(record.resId));
            }
        }
        if (
            this.config.isRoot &&
            (Object.hasOwn(params, "offset") || Object.hasOwn(params, "limit")) &&
            (
                Number(router.current.offset || 0) !== this.offset ||
                Number(router.current.limit || 0) !== this.limit
            )
        ) {
            commit(
                {
                    offset: this.offset || undefined,
                    limit: this.limit || undefined,
                    selection: undefined,
                },
                { history: "push" }
            );
        }
        return result;
    },

    async sortBy() {
        const result = await super.sortBy(...arguments);
        if (this.config.isRoot) {
            commit(
                {
                    orderBy: this.orderBy.length ? this.orderBy : undefined,
                    offset: this.offset || undefined,
                    selection: undefined,
                },
                { history: "push" }
            );
        }
        return result;
    },
});

patch(ListRenderer.prototype, {
    setup() {
        super.setup(...arguments);
        onMounted(() => {
            if (
                Number(router.current.nv) === NAVIGATION_VERSION &&
                this.props.list.config.isRoot &&
                router.current.columns === undefined
            ) {
                commit({ columns: visibleOptionalColumns(this) });
            }
        });
    },

    computeOptionalActiveFields() {
        const fields = super.computeOptionalActiveFields(...arguments);
        if (
            !this.props.list.config.isRoot ||
            router.current.columns === undefined
        ) {
            return fields;
        }
        const visible = new Set(String(router.current.columns || "").split(",").filter(Boolean));
        for (const fieldName of Object.keys(fields)) {
            fields[fieldName] = visible.has(fieldName);
        }
        return fields;
    },

    async toggleOptionalField() {
        const result = await super.toggleOptionalField(...arguments);
        if (this.props.list.config.isRoot) {
            commit({
                columns: visibleOptionalColumns(this),
            });
        }
        return result;
    },

    toggleOptionalFieldGroup() {
        const result = super.toggleOptionalFieldGroup(...arguments);
        if (this.props.list.config.isRoot) {
            commit({
                columns: visibleOptionalColumns(this),
            });
        }
        return result;
    },
});

patch(Notebook.prototype, {
    setup() {
        super.setup(...arguments);
        if (!this.env.inDialog && router.current.tab) {
            const page = String(router.current.tab);
            if (this.pages.some(([pageId]) => pageId === page)) {
                this.state.currentPage = page;
            }
        }
    },

    async activatePage(pageIndex) {
        const result = await super.activatePage(...arguments);
        if (!this.env.inDialog && this.state.currentPage === pageIndex) {
            commit({ tab: pageIndex });
        }
        return result;
    },
});

patch(CalendarModel.prototype, {
    setup(params) {
        super.setup(...arguments);
        if (router.current.scale && this.meta.scales.includes(router.current.scale)) {
            this.meta.scale = router.current.scale;
        }
        if (router.current.date) {
            const restoredDate = luxon.DateTime.fromISO(String(router.current.date));
            if (restoredDate.isValid) {
                this.meta.date = restoredDate.startOf("day");
            }
        }
        this.canonicalInitialLoad = true;
    },

    async load(params = {}) {
        const initialLoad = this.canonicalInitialLoad;
        const result = await super.load(...arguments);
        this.canonicalInitialLoad = false;
        if (initialLoad || params.date || params.scale) {
            commit(
                {
                    date: this.meta.date.toISODate(),
                    scale: this.meta.scale,
                },
                { history: initialLoad ? "replace" : "push" }
            );
        }
        return result;
    },
});
