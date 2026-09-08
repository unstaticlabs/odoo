import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export function attentionIconClass(level, { advisory = false } = {}) {
    if (level === "warning") {
        return "fa fa-exclamation-triangle text-warning";
    }
    // A settled line can carry an advisory note as well as the preserved
    // accounting it always carries.  The note is what the reviewer came to
    // read, so it decides the icon.
    return advisory ? "fa fa-info-circle text-secondary" : "fa fa-lock text-secondary";
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
        return attentionIconClass(this.level, {
            advisory: Boolean(this.props.record.data.batch_warning_reason),
        });
    }
}

registry.category("fields").add("expense_batch_attention", {
    component: ExpenseBatchAttentionField,
    supportedTypes: ["selection"],
});
