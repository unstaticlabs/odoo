import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { UrlField, urlField } from "@web/views/fields/url/url_field";

export class ExpenseReceiptEmailField extends UrlField {
    static template = "usl_accounting.ExpenseReceiptEmailField";

    get label() {
        return _t("Receipt email");
    }
}

// Native link semantics preserve unsaved expense edits and browser keyboard use.
registry.category("fields").add("expense_receipt_email", {
    ...urlField,
    component: ExpenseReceiptEmailField,
    extractProps(args) {
        return { ...urlField.extractProps(args), websitePath: true };
    },
});
