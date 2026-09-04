import { patch } from "@web/core/utils/patch";
import { uniqueId } from "@web/core/utils/functions";
import { JournalDashboardGraphField } from "@web/views/fields/journal_dashboard_graph/journal_dashboard_graph_field";

export function getAccessibleMonthlyValues(data) {
    return (data?.[0]?.values || []).filter(
        (point) => point.label && point.formatted_value
    );
}

export function getAccessibleGraphLabel(data) {
    const graph = data?.[0];
    const values = getAccessibleMonthlyValues(data);
    if (!graph?.key || !values.length) {
        return undefined;
    }
    return graph.key;
}

patch(JournalDashboardGraphField.prototype, {
    setup() {
        super.setup(...arguments);
        this.accessibleGraphDescriptionId = uniqueId("o_usl_journal_monthly_values_");
    },

    get accessibleMonthlyValues() {
        return getAccessibleMonthlyValues(this.data);
    },

    get accessibleGraphLabel() {
        return getAccessibleGraphLabel(this.data);
    },

    getBarChartConfig() {
        const config = super.getBarChartConfig(...arguments);
        config.options.plugins.tooltip.callbacks = {
            label: (context) => {
                const point = this.data[0].values[context.dataIndex];
                const value = point.formatted_value || context.formattedValue;
                return `${context.dataset.label}: ${value}`;
            },
        };
        return config;
    },
});
