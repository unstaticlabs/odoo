import { useEffect } from "@odoo/owl";
import { useRef } from "@web/owl2/utils";
import { registry } from "@web/core/registry";
import {
    Many2ManyTagsField,
    many2ManyTagsField,
} from "@web/views/fields/many2many_tags/many2many_tags_field";

export class PlatformBillingBankRecordPreview extends Many2ManyTagsField {
    static template = "usl_platform_billing.BankRecordPreviewField";

    setup() {
        super.setup();
        this.previewAnchor = useRef("previewAnchor");
        useEffect(
            (anchor, previewKey) => {
                const row = anchor?.closest(".o_data_row");
                const transactions = this.transactions;
                if (!row || !transactions.length || !previewKey) {
                    return;
                }
                const attributes = {
                    "data-tooltip-template":
                        "usl_platform_billing.BankRecordPreviewTooltip",
                    "data-tooltip-info": JSON.stringify({ transactions }),
                    "data-tooltip-position": "left",
                    "data-tooltip-delay": "250",
                };
                const previous = Object.fromEntries(
                    Object.keys(attributes).map((name) => [
                        name,
                        row.getAttribute(name),
                    ])
                );
                row.classList.add("o_usl_platform_billing_bank_preview_row");
                for (const [name, value] of Object.entries(attributes)) {
                    row.setAttribute(name, value);
                }
                return () => {
                    row.classList.remove("o_usl_platform_billing_bank_preview_row");
                    for (const [name, value] of Object.entries(previous)) {
                        if (value === null) {
                            row.removeAttribute(name);
                        } else {
                            row.setAttribute(name, value);
                        }
                    }
                };
            },
            () => [this.previewAnchor.el, JSON.stringify(this.transactions)]
        );
    }

    get transactions() {
        return this.props.record.data.bank_transaction_preview || [];
    }
}

export const platformBillingBankRecordPreview = {
    ...many2ManyTagsField,
    component: PlatformBillingBankRecordPreview,
    fieldDependencies: [
        ...(many2ManyTagsField.fieldDependencies || []),
        { name: "bank_transaction_preview", type: "json" },
    ],
};

registry
    .category("fields")
    .add("platform_billing_bank_record_preview", platformBillingBankRecordPreview);
