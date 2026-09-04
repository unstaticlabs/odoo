import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { listView } from "@web/views/list/list_view";
import { ListRenderer } from "@web/views/list/list_renderer";
import { Component, onWillStart, useState } from "@odoo/owl";

export const BATCH_QUICK_FILTERS = [
    { name: "all", label: _t("All") },
    { name: "open_batches", label: _t("Open") },
    { name: "needs_information", label: _t("Needs information") },
    { name: "my_batches", label: _t("My batches") },
    { name: "exceptions", label: _t("Exceptions") },
];

export function activeQuickFilterName(searchItems, query) {
    const quickNames = new Set(BATCH_QUICK_FILTERS.map(({ name }) => name));
    for (const queryElement of query) {
        const item = searchItems[queryElement.searchItemId];
        if (quickNames.has(item?.name)) {
            return item.name;
        }
    }
    return "all";
}

export function quickFilterItemIds(searchItems) {
    const quickNames = new Set(BATCH_QUICK_FILTERS.map(({ name }) => name));
    return Object.values(searchItems)
        .filter((item) => quickNames.has(item.name))
        .map((item) => item.id);
}

export class ExpenseBatchQuickFilters extends Component {
    static template = "usl_expense_batch.ExpenseBatchQuickFilters";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.state = useState({ counts: {} });
        onWillStart(async () => {
            this.state.counts = await this.orm.call(
                "usl.expense.batch",
                "get_batch_dashboard_counts",
                [],
            );
        });
    }

    get filters() {
        return BATCH_QUICK_FILTERS;
    }

    isActive(name) {
        return activeQuickFilterName(
            this.env.searchModel.searchItems,
            this.env.searchModel.query,
        ) === name;
    }

    applyFilter(name) {
        const searchModel = this.env.searchModel;
        const activeIds = new Set(
            searchModel.query.map(({ searchItemId }) => searchItemId),
        );
        for (const itemId of quickFilterItemIds(searchModel.searchItems)) {
            if (activeIds.has(itemId)) {
                searchModel.toggleSearchItem(itemId);
            }
        }
        if (name !== "all") {
            const item = Object.values(searchModel.searchItems).find(
                (candidate) => candidate.name === name,
            );
            if (item) {
                searchModel.toggleSearchItem(item.id);
            }
        }
    }
}

export class ExpenseBatchListRenderer extends ListRenderer {
    static components = {
        ...ListRenderer.components,
        ExpenseBatchQuickFilters,
    };
    static template = "usl_expense_batch.ExpenseBatchListRenderer";
}

registry.category("views").add("usl_expense_batch_list", {
    ...listView,
    Renderer: ExpenseBatchListRenderer,
});
