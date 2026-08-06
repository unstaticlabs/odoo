import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export function attentionIconClass(level) {
    return level === "warning"
        ? "fa fa-exclamation-triangle text-warning"
        : "fa fa-lock text-secondary";
}

export class ExpenseBatchAttentionField extends Component {
    static template = "usl_expense_batch.ExpenseBatchAttentionField";
    static props = { ...standardFieldProps };

    get level() {
        return this.props.record.data[this.props.name];
    }

    get message() {
        return this.props.record.data.batch_attention_message || "";
    }

    get iconClass() {
        return attentionIconClass(this.level);
    }
}

registry.category("fields").add("expense_batch_attention", {
    component: ExpenseBatchAttentionField,
    supportedTypes: ["selection"],
});
