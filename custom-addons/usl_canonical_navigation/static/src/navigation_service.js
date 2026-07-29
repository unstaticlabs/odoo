import { browser } from "@web/core/browser/browser";
import { router } from "@web/core/browser/router";
import { registry } from "@web/core/registry";
import { user, userBus } from "@web/core/user";
import {
    MAX_DIRECT_URL_LENGTH,
    EXPANDED_PORTABLE_KEYS,
    QUERY_ORDER,
    WORKSPACE_ONLY_KEYS,
    canonicalCompanyValue,
    companyTransitionPatch,
    navigationPatch,
    parseCompanyIds,
    withPortableActionAlias,
    writePortableRoute,
} from "./navigation_state";

function currentCompanyIds() {
    return user.activeCompanies.map((company) => company.id);
}

function redirectToUnavailable(reason) {
    const query = new URLSearchParams({
        nv: "1",
        cids: canonicalCompanyValue(currentCompanyIds()),
        recovery: reason,
    });
    browser.location.replace(`/odoo/usl-navigation-unavailable?${query}`);
}

export const canonicalNavigationService = {
    dependencies: ["action", "orm"],
    async start(_env, { action, orm }) {
        const loadOdooState = action.loadState.bind(action);
        const switchOdooView = action.switchView.bind(action);
        const allowedCompanyIds = new Set(user.allowedCompanies.map((company) => company.id));
        let internalCompanyChange = false;
        let service;
        let restoringCanonicalState = false;
        const activateRouteCompanies = async (nextCompanyIds) => {
            if (
                nextCompanyIds.length &&
                nextCompanyIds.join(",") !== currentCompanyIds().join(",")
            ) {
                internalCompanyChange = true;
                try {
                    await user.activateCompanies(nextCompanyIds, {
                        includeChildCompanies: false,
                        reload: false,
                    });
                } finally {
                    internalCompanyChange = false;
                }
            }
            if (service) {
                service.companyIds = [...currentCompanyIds()];
            }
        };
        action.switchView = async (viewType, props = {}, options = {}) => {
            const result = await switchOdooView(viewType, props, options);
            if (
                !restoringCanonicalState &&
                !options.newWindow &&
                Number(router.current.nv) === 1
            ) {
                writePortableRoute(() =>
                    router.pushState(
                        navigationPatch(
                            { view_type: viewType, active: undefined },
                            currentCompanyIds()
                        ),
                        { sync: true }
                    )
                );
            }
            return result;
        };
        action.loadState = async (state = router.current) => {
            if (Number(state.nv) !== 1 || (!state.action && !state.model)) {
                return loadOdooState(state);
            }
            const routeCompanyIds = parseCompanyIds(state.cids);
            if (
                routeCompanyIds === null ||
                routeCompanyIds.some((companyId) => !allowedCompanyIds.has(companyId))
            ) {
                redirectToUnavailable("company");
                return true;
            }
            await activateRouteCompanies(routeCompanyIds);
            const options = {
                clearBreadcrumbs: true,
                viewType: state.resId ? "form" : state.view_type,
            };
            if (state.active_id) {
                options.additionalContext = {
                    active_id: state.active_id,
                    active_ids: [state.active_id],
                };
            }
            if (state.resId && state.resId !== "new") {
                options.props = { resId: state.resId };
            }
            const actionRequest = state.action || {
                name: state.model,
                res_model: state.model,
                type: "ir.actions.act_window",
                views: state.resId
                    ? [[false, "form"]]
                    : [[false, state.view_type || "list"], [false, "form"]],
            };
            restoringCanonicalState = true;
            try {
                await withPortableActionAlias(() => action.doAction(actionRequest, options));
            } finally {
                restoringCanonicalState = false;
            }
            return true;
        };
        const requested = parseCompanyIds(router.current.cids);
        if (requested === null || requested?.some((companyId) => !allowedCompanyIds.has(companyId))) {
            redirectToUnavailable("company");
            return {
                blocked: true,
                commit() {},
                async loadWorkspace() {
                    return { status: "unavailable" };
                },
            };
        }

        const companyIds = requested?.length ? requested : currentCompanyIds();
        await activateRouteCompanies(companyIds);
        userBus.addEventListener("ACTIVE_COMPANIES_CHANGED", () => {
            if (
                !internalCompanyChange &&
                Number(router.current.nv) === 1
            ) {
                const nextCompanyIds = currentCompanyIds();
                if (service) {
                    service.companyIds = [...nextCompanyIds];
                }
                if (
                    parseCompanyIds(router.current.cids)?.join(",") ===
                    nextCompanyIds.join(",")
                ) {
                    return;
                }
                writePortableRoute(() =>
                    router.pushState(
                        companyTransitionPatch(router.current, nextCompanyIds),
                        { sync: true }
                    )
                );
            }
        });
        browser.addEventListener(
            "popstate",
            async () => {
                const visibleCompanyIds = parseCompanyIds(
                    new URL(browser.location.href).searchParams.get("cids")
                );
                if (
                    visibleCompanyIds?.length &&
                    visibleCompanyIds.every((companyId) => allowedCompanyIds.has(companyId))
                ) {
                    await activateRouteCompanies(visibleCompanyIds);
                }
            },
            { capture: true }
        );
        if (Number(router.current.nv) === 1 && !router.current.ws) {
            if ([...WORKSPACE_ONLY_KEYS].some((key) => router.current[key] !== undefined)) {
                redirectToUnavailable("state");
                return {
                    blocked: true,
                    commit() {},
                    async loadWorkspace() {
                        return { status: "unavailable" };
                    },
                };
            }
            const validationState = {
                action: router.current.action,
                model: router.current.model,
                res_id: router.current.resId,
                active_id: router.current.active_id,
            };
            for (const key of QUERY_ORDER) {
                if (router.current[key] !== undefined) {
                    validationState[key] = router.current[key];
                }
            }
            const validation = await orm.call(
                "usl.navigation.workspace",
                "validate_state",
                [validationState, companyIds]
            );
            if (validation.status !== "ok") {
                redirectToUnavailable("state");
                return {
                    blocked: true,
                    commit() {},
                    async loadWorkspace() {
                        return { status: "unavailable" };
                    },
                };
            }
        }
        router.replaceState(
            navigationPatch({}, companyIds),
            { sync: true }
        );

        service = {
            blocked: false,
            companyIds,
            async setCompanies(nextCompanyIds) {
                if (
                    !nextCompanyIds.length ||
                    nextCompanyIds.some((companyId) => !allowedCompanyIds.has(companyId))
                ) {
                    return { status: "unavailable" };
                }
                await activateRouteCompanies(nextCompanyIds);
                this.commit({}, { history: "replace" });
                return { status: "ok" };
            },
            commit(patch, { history = "replace", sync = true } = {}) {
                const state = navigationPatch(patch, currentCompanyIds());
                const method = history === "push" ? "pushState" : "replaceState";
                writePortableRoute(() => router[method](state, { sync }));
            },
            async loadWorkspace(publicId) {
                const result = await orm.call(
                    "usl.navigation.workspace",
                    "read_workspace",
                    [publicId]
                );
                if (result.status !== "ok") {
                    return { status: "unavailable" };
                }
                const permitted = new Set(user.allowedCompanies.map((company) => company.id));
                if (result.company_ids.some((companyId) => !permitted.has(companyId))) {
                    return { status: "unavailable" };
                }
                await activateRouteCompanies(result.company_ids);
                const expandedState = navigationPatch(result.state, result.company_ids);
                if (Array.isArray(result.state.selection)) {
                    expandedState.selection = result.state.selection;
                }
                writePortableRoute(() =>
                    router.replaceState(
                        {
                            ...expandedState,
                            ws: publicId,
                        },
                        { sync: true }
                    )
                );
                return result;
            },
            async ensurePortable(state, options = {}) {
                const currentPortableState = Object.fromEntries(
                    [...EXPANDED_PORTABLE_KEYS, ...WORKSPACE_ONLY_KEYS]
                        .filter((key) => router.current[key] !== undefined)
                        .map((key) => [key, router.current[key]])
                );
                const directState = navigationPatch(
                    { ...currentPortableState, ...state, ws: undefined },
                    currentCompanyIds()
                );
                const candidate =
                    browser.location.origin +
                    writePortableRoute(() =>
                        router.stateToUrl({
                            ...router.current,
                            ...directState,
                            ws: undefined,
                        })
                    );
                const selection = Array.isArray(state.selection)
                    ? state.selection
                    : String(state.selection || "").split(",").filter(Boolean);
                if (
                    candidate.length <= MAX_DIRECT_URL_LENGTH &&
                    selection.length <= 40 &&
                    !options.sensitive &&
                    directState.selection_mode !== "domain"
                ) {
                    this.commit(directState, options);
                    return { url: candidate };
                }
                const workspaceState = {
                    ...currentPortableState,
                    ...state,
                    action: state.action ?? router.current.action,
                    model: state.model ?? router.current.model,
                    active_id: state.active_id ?? router.current.active_id,
                    res_id: state.res_id ?? router.current.resId,
                };
                delete workspaceState.resId;
                for (const [key, value] of Object.entries(workspaceState)) {
                    if (value === undefined || value === null || value === "") {
                        delete workspaceState[key];
                    }
                }
                const result = await orm.call(
                    "usl.navigation.workspace",
                    "create_workspace",
                    [workspaceState],
                    {
                        name: options.name,
                        company_ids: currentCompanyIds(),
                        automatic: true,
                    }
                );
                writePortableRoute(() =>
                    router.replaceState(
                        navigationPatch({ ws: result.public_id }, currentCompanyIds()),
                        { sync: true }
                    )
                );
                return result;
            },
        };
        return service;
    },
};

registry.category("services").add("canonical_navigation", canonicalNavigationService);
