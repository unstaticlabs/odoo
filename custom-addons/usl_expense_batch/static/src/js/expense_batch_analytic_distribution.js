import {
    AnalyticDistribution,
    analyticDistribution,
} from "@analytic/components/analytic_distribution/analytic_distribution";
import { registry } from "@web/core/registry";
import { useBus } from "@web/core/utils/hooks";
import { Field } from "@web/views/fields/field";

export const EXPENSE_BATCH_DISCARDED = "USL_EXPENSE_BATCH_DISCARDED";

class ExpenseBatchAnalyticAccountField extends Field {
    get fieldComponentProps() {
        const props = super.fieldComponentProps;
        if (this.env.isSmall && props.record.fields[props.name].relation === "account.analytic.account") {
            // Selecting an account requires its name. The default mobile view also reads its balance.
            return {
                ...props,
                context: {
                    ...props.context,
                    kanban_view_ref: "usl_expense_batch.view_analytic_account_mobile_select",
                },
            };
        }
        return props;
    }
}

class ExpenseBatchAnalyticDistribution extends AnalyticDistribution {
    static components = { ...AnalyticDistribution.components, Field: ExpenseBatchAnalyticAccountField };

    setup() {
        super.setup();
        this.lineRecords = new Map();
        this.pendingClose = null;
        this.discardRefresh = null;
        useBus(this.props.record.model.bus, EXPENSE_BATCH_DISCARDED, ({ detail }) => {
            // Native Discard awaits pending form changes and restores the record.
            // The picker can still display its optimistic selection when the
            // restored value matches the one it observed before the edit.
            this.discardRefresh ??= this.jsonToData(this.props.record.data[this.props.name]).finally(() => {
                this.discardRefresh = null;
            });
            detail.promises.push(this.discardRefresh);
        });
    }

    get editingRecord() {
        // The native parser can save a cleaned value when an account is
        // missing. Restoring the display after Discard must stay read-only.
        return !this.discardRefresh && super.editingRecord;
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
