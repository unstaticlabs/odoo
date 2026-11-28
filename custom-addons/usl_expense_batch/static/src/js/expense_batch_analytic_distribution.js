import {
    AnalyticDistribution,
    analyticDistribution,
} from "@analytic/components/analytic_distribution/analytic_distribution";
import { registry } from "@web/core/registry";

class ExpenseBatchAnalyticDistribution extends AnalyticDistribution {
    setup() {
        super.setup();
        this.lineRecords = new Map();
        this.pendingClose = null;
    }

    recordProps(line) {
        const props = super.recordProps(line);
        props.hooks.onRootLoaded = (record) => this.lineRecords.set(line.id, record);
        return props;
    }

    closeAnalyticEditor() {
        if (!this.pendingClose) {
            // The account pickers use their own records. Their pending updates
            // must finish before the batch form serializes the distribution.
            const changes = this.state.formattedData.map((line) =>
                this.lineRecords.get(line.id)?.getChanges()
            );
            this.pendingClose = Promise.all(changes)
                .then(() => this.save())
                .then(() => {
                    this.state.showDropdown = false;
                    this.lineRecords.clear();
                })
                .finally(() => {
                    this.pendingClose = null;
                });
        }
        return this.pendingClose;
    }

    commitChanges() {
        return this.isDropdownOpen ? this.closeAnalyticEditor() : this.pendingClose;
    }
}

registry.category("fields").add("usl_expense_batch_analytic_distribution", {
    ...analyticDistribution,
    component: ExpenseBatchAnalyticDistribution,
    additionalClasses: ["o_field_analytic_distribution"],
});
