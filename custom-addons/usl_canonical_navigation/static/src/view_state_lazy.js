import { router } from "@web/core/browser/router";
import { user } from "@web/core/user";
import { patch } from "@web/core/utils/patch";
import { GraphModel } from "@web/views/graph/graph_model";
import { PivotModel } from "@web/views/pivot/pivot_model";
import { navigationPatch, writePortableRoute } from "./navigation_state";

function commit(patchValue, { history = "push" } = {}) {
    const method = history === "replace" ? "replaceState" : "pushState";
    writePortableRoute(() =>
        router[method](
            navigationPatch(
                patchValue,
                user.activeCompanies.map((company) => company.id)
            ),
            { sync: true }
        )
    );
}

function parseArray(value) {
    if (!value) {
        return undefined;
    }
    try {
        const parsed = typeof value === "string" ? JSON.parse(value) : value;
        return Array.isArray(parsed) ? parsed : undefined;
    } catch {
        return undefined;
    }
}

function parsePivotOrder(value) {
    if (!value) {
        return undefined;
    }
    try {
        const parsed = typeof value === "string" ? JSON.parse(value) : value;
        if (
            !parsed ||
            typeof parsed !== "object" ||
            typeof parsed.measure !== "string" ||
            !["asc", "desc"].includes(parsed.order) ||
            !Array.isArray(parsed.groupId) ||
            !parsed.groupId.every(Array.isArray)
        ) {
            return undefined;
        }
        return {
            groupId: parsed.groupId,
            measure: parsed.measure,
            order: parsed.order,
        };
    } catch {
        return undefined;
    }
}

function pivotRoute(model) {
    return {
        rows: model.metaData.rowGroupBys,
        columnsBy: model.metaData.colGroupBys,
        measures: model.metaData.activeMeasures.slice().sort().join(","),
        pivot_order: model.metaData.sortedColumn || undefined,
    };
}

function graphRoute(model) {
    return {
        graph: model.metaData.mode,
        measures: model.metaData.measure,
        stacked: model.metaData.stacked ? "1" : undefined,
        cumulated: model.metaData.cumulated ? "1" : undefined,
    };
}

patch(GraphModel.prototype, {
    setup(params) {
        const route = router.current;
        const measures = String(route.measures || "").split(",").filter(Boolean);
        const restored = {
            ...params,
            measure: measures[0] || params.measure,
            mode: ["bar", "line", "pie"].includes(route.graph) ? route.graph : params.mode,
            stacked:
                route.stacked === undefined ? params.stacked : Boolean(Number(route.stacked)),
            cumulated:
                route.cumulated === undefined ? params.cumulated : Boolean(Number(route.cumulated)),
        };
        super.setup(restored);
    },

    async load() {
        const result = await super.load(...arguments);
        commit(graphRoute(this), { history: "replace" });
        return result;
    },

    async updateMetaData() {
        const result = await super.updateMetaData(...arguments);
        commit(graphRoute(this));
        return result;
    },
});

patch(PivotModel.prototype, {
    setup(params) {
        const rows = parseArray(router.current.rows);
        const columns = parseArray(router.current.columnsBy);
        const measures = String(router.current.measures || "").split(",").filter(Boolean);
        const sortedColumn = parsePivotOrder(router.current.pivot_order);
        const restored = {
            ...params,
            metaData: {
                ...params.metaData,
                rowGroupBys: rows || params.metaData.rowGroupBys,
                colGroupBys: columns || params.metaData.colGroupBys,
                activeMeasures: measures.length ? measures : params.metaData.activeMeasures,
                sortedColumn: sortedColumn || params.metaData.sortedColumn,
            },
        };
        super.setup(restored);
    },

    async load() {
        const result = await super.load(...arguments);
        commit(pivotRoute(this), { history: "replace" });
        return result;
    },

    async addGroupBy() {
        const result = await super.addGroupBy(...arguments);
        commit(pivotRoute(this));
        return result;
    },

    closeGroup() {
        const result = super.closeGroup(...arguments);
        commit(pivotRoute(this));
        return result;
    },

    async flip() {
        const result = await super.flip(...arguments);
        commit(pivotRoute(this));
        return result;
    },

    async toggleMeasure() {
        const result = await super.toggleMeasure(...arguments);
        commit(pivotRoute(this));
        return result;
    },

    sortRows() {
        const result = super.sortRows(...arguments);
        commit(pivotRoute(this));
        return result;
    },
});
