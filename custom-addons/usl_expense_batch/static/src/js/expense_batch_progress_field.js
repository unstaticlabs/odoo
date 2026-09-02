import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const PROGRESS_STATES = [
    ["draft", "Draft", "bg-secondary"],
    ["submitted", "Submitted", "bg-warning"],
    ["approved", "Approved", "bg-info"],
    ["posted", "Posted", "bg-primary"],
    ["in_payment", "In payment", "bg-primary-subtle"],
    ["paid", "Paid", "bg-success"],
    ["refused", "Refused", "bg-danger"],
];

export function parseExpenseProgressBreakdown(value) {
    if (!value) {
        return {};
    }
    try {
        const parsed = JSON.parse(value);
        return parsed && typeof parsed === "object" && !Array.isArray(parsed)
            ? parsed
            : {};
    } catch {
        return {};
    }
}

export function expenseProgressSegments(value) {
    const breakdown = parseExpenseProgressBreakdown(value);
    return PROGRESS_STATES.flatMap(([key, label, colorClass]) => {
        const count = Number(breakdown[key] || 0);
        return Number.isFinite(count) && count > 0
            ? [{ key, label, colorClass, count }]
            : [];
    });
}

export class ExpenseBatchProgressField extends Component {
    static template = "usl_expense_batch.ExpenseBatchProgressField";
    static props = { ...standardFieldProps };

    get summary() {
        return this.props.record.data[this.props.name] || "";
    }

    get segments() {
        return expenseProgressSegments(
            this.props.record.data.expense_progress_breakdown,
        );
    }

    get total() {
        return this.segments.reduce((sum, segment) => sum + segment.count, 0);
    }

    get hasMixedStates() {
        return this.segments.length > 1;
    }

    get accessibleSummary() {
        return _t("%s expenses: %s", this.total, this.summary);
    }
}

registry.category("fields").add("expense_batch_progress", {
    component: ExpenseBatchProgressField,
    supportedTypes: ["char"],
});
