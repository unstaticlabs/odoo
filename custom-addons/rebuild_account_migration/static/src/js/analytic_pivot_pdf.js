/** @odoo-module **/

import { analyticPivotView } from "@analytic/views/pivot/pivot_view";
import { AnalyticPivotRenderer } from "@analytic/views/pivot/pivot_renderer";
import { registry } from "@web/core/registry";
import { download } from "@web/core/network/download";
import { user } from "@web/core/user";


export class UslAnalyticPivotRenderer extends AnalyticPivotRenderer {
    onDownloadPdfClicked() {
        const sorted = this.model.metaData.sortedColumn;
        const payload = {
            row_axes: [...this.model.metaData.fullRowGroupBys],
            column_axes: [...this.model.metaData.fullColGroupBys],
            measures: [...this.model.metaData.activeMeasures],
            domain: this.model.searchParams.domain,
            context: {
                lang: this.model.searchParams.context.lang,
                tz: this.model.searchParams.context.tz,
            },
            order: sorted
                ? { measure: sorted.measure, direction: sorted.order }
                : {},
            company_id: user.activeCompany.id,
        };
        download({
            url: "/usl/accounting/analytic-pivot/pdf",
            data: {
                data: new Blob([JSON.stringify(payload)], {
                    type: "application/json",
                }),
            },
        });
    }
}


export const uslAnalyticPivotView = {
    ...analyticPivotView,
    Renderer: UslAnalyticPivotRenderer,
    buttonTemplate: "rebuild_account_migration.AnalyticPivotButtons",
};

registry.category("views").add("usl_analytic_pivot", uslAnalyticPivotView);
