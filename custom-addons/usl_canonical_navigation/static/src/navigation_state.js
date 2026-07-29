import { browser } from "@web/core/browser/browser";
import { router } from "@web/core/browser/router";

export const NAVIGATION_VERSION = 1;
export const MAX_DIRECT_URL_LENGTH = 1800;
export const MAX_DIRECT_SELECTION = 40;

export const QUERY_ORDER = [
    "nv",
    "cids",
    "ws",
    "view_type",
    "domain",
    "groupBy",
    "orderBy",
    "favorite",
    "panel",
    "columns",
    "offset",
    "limit",
    "selection",
    "active",
    "date",
    "scale",
    "measures",
    "rows",
    "columnsBy",
    "pivot_order",
    "graph",
    "stacked",
    "cumulated",
    "tab",
    "parent_domain",
    "parent_groupBy",
    "parent_orderBy",
    "parent_favorite",
    "parent_panel",
    "parent_columns",
    "parent_offset",
    "parent_limit",
    "parent_selection",
    "report",
    "company",
    "period",
    "anchor",
    "date_from",
    "date_to",
    "moves",
    "comparison",
    "comparison_from",
    "comparison_to",
    "group",
    "journals",
    "accounts",
    "partners",
    "analytic_plans",
    "analytics",
    "search",
    "collapsed",
    "lang",
    "debug",
];

export const EXPANDED_PORTABLE_KEYS = new Set(
    QUERY_ORDER.filter((key) => !["nv", "cids", "ws", "lang", "debug"].includes(key))
);
export const WORKSPACE_ONLY_KEYS = new Set(["selection_mode"]);
const PORTABLE_STATE_KEYS = [...EXPANDED_PORTABLE_KEYS, ...WORKSPACE_ONLY_KEYS];

const orderedIndex = new Map(QUERY_ORDER.map((key, index) => [key, index]));
let installed = false;
let portableWriteDepth = 0;
let portableActionAliasDepth = 0;

export function writePortableRoute(callback) {
    portableWriteDepth += 1;
    try {
        return callback();
    } finally {
        portableWriteDepth -= 1;
    }
}

export async function withPortableActionAlias(callback) {
    portableActionAliasDepth += 1;
    try {
        return await callback();
    } finally {
        portableActionAliasDepth -= 1;
    }
}

export function stableJson(value) {
    if (Array.isArray(value)) {
        return `[${value.map(stableJson).join(",")}]`;
    }
    if (value && typeof value === "object") {
        return `{${Object.keys(value)
            .sort()
            .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
            .join(",")}}`;
    }
    return JSON.stringify(value);
}

export function normalizeIds(value, { max = Number.MAX_SAFE_INTEGER } = {}) {
    const source = Array.isArray(value) ? value : String(value || "").split(",");
    const ids = [
        ...new Set(source.map(Number).filter((id) => Number.isSafeInteger(id) && id > 0)),
    ].sort((a, b) => a - b);
    return ids.slice(0, max);
}

export function parsePanelState(value) {
    if (value === undefined) {
        return undefined;
    }
    try {
        const parsed = typeof value === "string" ? JSON.parse(value) : value;
        if (
            !parsed ||
            Array.isArray(parsed) ||
            typeof parsed !== "object" ||
            Object.keys(parsed).length > 30
        ) {
            return null;
        }
        const normalized = {};
        for (const [fieldName, rawValue] of Object.entries(parsed)) {
            if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(fieldName)) {
                return null;
            }
            const isArray = Array.isArray(rawValue);
            const values = isArray ? rawValue : [rawValue];
            if (values.length > 200) {
                return null;
            }
            const semanticValues = values.map((item) => {
                if (Number.isSafeInteger(item) && item > 0) {
                    return item;
                }
                if (
                    typeof item === "string" &&
                    item.length > 0 &&
                    item.length <= 128 &&
                    !/[\u0000-\u001F]/.test(item)
                ) {
                    return item;
                }
                return null;
            });
            if (semanticValues.includes(null)) {
                return null;
            }
            const deduplicated = [...new Set(semanticValues)].sort((left, right) =>
                String(left).localeCompare(String(right), undefined, { numeric: true })
            );
            if (deduplicated.length !== semanticValues.length) {
                return null;
            }
            normalized[fieldName] = isArray ? deduplicated : deduplicated[0];
        }
        return normalized;
    } catch {
        return null;
    }
}

export function parseCompanyIds(value) {
    if (value === undefined || value === null || value === "") {
        return [];
    }
    const parts = String(value).split("-");
    const ids = parts.map(Number);
    if (
        ids.some((id) => !Number.isSafeInteger(id) || id <= 0) ||
        ids.length !== new Set(ids).size
    ) {
        return null;
    }
    return ids;
}

function encode(value) {
    return encodeURIComponent(String(value)).replace(
        /[!'()*]/g,
        (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`
    );
}

export function canonicalizeUrl(url) {
    const parsed = new URL(url, browser.location.origin);
    const entries = [...parsed.searchParams.entries()].filter(([, value]) => value !== "");
    entries.sort(([left], [right]) => {
        const leftIndex = orderedIndex.get(left);
        const rightIndex = orderedIndex.get(right);
        if (leftIndex !== undefined || rightIndex !== undefined) {
            return (leftIndex ?? QUERY_ORDER.length) - (rightIndex ?? QUERY_ORDER.length);
        }
        return left.localeCompare(right);
    });
    parsed.search = entries.map(([key, value]) => `${encode(key)}=${encode(value)}`).join("&");
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
}

export function canonicalCompanyValue(companyIds) {
    if (!companyIds.length) {
        return "";
    }
    return [companyIds[0], ...companyIds.slice(1).sort((a, b) => a - b)].join("-");
}

export function selectionValue(ids) {
    return normalizeIds(ids, { max: MAX_DIRECT_SELECTION }).join(",") || undefined;
}

export function navigationPatch(patch, companyIds) {
    const next = { nv: NAVIGATION_VERSION, cids: canonicalCompanyValue(companyIds), ...patch };
    for (const [key, value] of Object.entries(next)) {
        if (value === undefined || value === null || value === "" || value === false) {
            next[key] = undefined;
        } else if (key === "selection" && Array.isArray(value)) {
            next[key] = selectionValue(value);
        } else if (
            ["groupBy", "orderBy", "panel", "rows", "columnsBy", "pivot_order"].includes(key) &&
            typeof value !== "string"
        ) {
            next[key] = stableJson(value);
        }
    }
    return next;
}

export function installCanonicalRouter() {
    if (installed) {
        return;
    }
    installed = true;
    const stateToUrl = router.stateToUrl.bind(router);
    const urlToState = router.urlToState.bind(router);
    let shadowAction = router.current.action;
    let portableShadow = Object.fromEntries(
        [...PORTABLE_STATE_KEYS, "ws"]
            .filter((key) => router.current[key] !== undefined)
            .map((key) => [key, router.current[key]])
    );
    const actionPath = (action) => {
        if (action === undefined) {
            return undefined;
        }
        return new URL(stateToUrl({ action }), browser.location.origin).pathname;
    };
    browser.addEventListener(
        "popstate",
        (event) => {
            const visibleState = urlToState(new URL(browser.location.href));
            const nextState = event.state?.nextState;
            if (Number(visibleState.nv) !== NAVIGATION_VERSION || !nextState) {
                return;
            }
            // Odoo's hidden globalState is useful as a fast local snapshot, but
            // it must never override a canonical history entry.  Removing it
            // before the core popstate listener runs makes the visible URL the
            // restoration source while retaining the normal action stack.
            delete nextState.globalState;
            for (const actionState of nextState.actionStack || []) {
                delete actionState.globalState;
            }
            browser.sessionStorage.removeItem("current_action");
            browser.sessionStorage.removeItem("current_state");
            for (const key of [...PORTABLE_STATE_KEYS, "ws"]) {
                if (visibleState[key] === undefined) {
                    delete nextState[key];
                } else {
                    nextState[key] = visibleState[key];
                }
            }
        },
        { capture: true }
    );
    for (const method of ["pushState", "replaceState"]) {
        const original = router[method].bind(router);
        router[method] = (nextState, options) => {
            if (
                Number(router.current.nv) === NAVIGATION_VERSION ||
                Number(nextState.nv) === NAVIGATION_VERSION
            ) {
                nextState = { ...nextState };
                const visibleState = urlToState(new URL(browser.location.href));
                const nextAction =
                    nextState.actionStack?.at(-1)?.action ??
                    nextState.action ??
                    router.current.action;
                if (
                    portableActionAliasDepth > 0 ||
                    actionPath(nextAction) === actionPath(visibleState.action)
                ) {
                    nextState.nv ??= visibleState.nv ?? NAVIGATION_VERSION;
                    nextState.cids ??= visibleState.cids;
                    for (const key of [...PORTABLE_STATE_KEYS, "ws"]) {
                        if (portableWriteDepth === 0 || !Object.hasOwn(nextState, key)) {
                            const sourceValue =
                                key !== "ws" &&
                                portableActionAliasDepth > 0 &&
                                visibleState.ws
                                    ? portableShadow[key]
                                    : visibleState[key];
                            if (sourceValue === undefined) {
                                delete nextState[key];
                            } else {
                                nextState[key] = sourceValue;
                            }
                        }
                    }
                }
                delete nextState.globalState;
                if (nextState.actionStack) {
                    nextState.actionStack = nextState.actionStack.map((actionState) => {
                        const sanitized = { ...actionState };
                        delete sanitized.globalState;
                        return sanitized;
                    });
                }
                browser.sessionStorage.removeItem("current_state");
            }
            return original(nextState, options);
        };
    }

    router.stateToUrl = (state) => {
        const serializable = { ...state };
        // The action service sometimes builds a route from a fresh object instead
        // of merging the current query.  Keep the canonical envelope at the
        // router boundary so menu, breadcrumb and record transitions cannot
        // accidentally turn a canonical workspace back into a session-only URL.
        serializable.nv ??= router.current.nv ?? NAVIGATION_VERSION;
        serializable.cids ??=
            router.current.cids ?? urlToState(new URL(browser.location.href)).cids;
        const sameAction =
            portableActionAliasDepth > 0 ||
            (
                actionPath(serializable.action) !== undefined &&
                actionPath(serializable.action) === actionPath(shadowAction)
            );
        if (sameAction) {
            const visibleState = urlToState(new URL(browser.location.href));
            const visibleSameAction =
                actionPath(visibleState.action) === actionPath(serializable.action);
            for (const key of [...PORTABLE_STATE_KEYS, "ws"]) {
                if (visibleSameAction && portableWriteDepth === 0) {
                    if (visibleState[key] === undefined) {
                        delete serializable[key];
                    } else {
                        serializable[key] = visibleState[key];
                    }
                } else if (!Object.hasOwn(serializable, key)) {
                    if (visibleSameAction && visibleState[key] !== undefined) {
                        serializable[key] = visibleState[key];
                    } else if (portableShadow[key] !== undefined) {
                        serializable[key] = portableShadow[key];
                    }
                }
            }
        } else {
            portableShadow = {};
        }
        for (const key of [...PORTABLE_STATE_KEYS, "ws"]) {
            if (Object.hasOwn(serializable, key)) {
                if (serializable[key] === undefined) {
                    delete portableShadow[key];
                } else {
                    portableShadow[key] = serializable[key];
                }
            }
        }
        shadowAction = serializable.action;
        if (serializable.ws) {
            for (const key of [...EXPANDED_PORTABLE_KEYS, ...WORKSPACE_ONLY_KEYS]) {
                delete serializable[key];
            }
            serializable.action = "usl-workspace";
            delete serializable.model;
            delete serializable.resId;
            delete serializable.active_id;
            delete serializable.actionStack;
        }
        return canonicalizeUrl(stateToUrl(serializable));
    };
    router.urlToState = (url) => {
        const state = urlToState(url);
        shadowAction = state.action;
        portableShadow = Object.fromEntries(
            [...PORTABLE_STATE_KEYS, "ws"]
                .filter((key) => state[key] !== undefined)
                .map((key) => [key, state[key]])
        );
        return state;
    };
}

installCanonicalRouter();
