import { AccountMoveFormController, AccountMoveFormView } from "@account/components/account_move_form/account_move_form";
import { registry } from "@web/core/registry";
import { AttachDocumentWidget, attachDocumentWidget } from "@web/views/widgets/attach_document/attach_document";

const receiptActions = new Set([
    "action_accept_linked_receipt",
    "action_attach_linked_receipt",
    "action_dismiss_linked_receipt",
    "action_open_linked_receipt_website",
    "action_retry_linked_receipt",
    "action_review_linked_receipt",
    "action_scan_existing_receipt_emails",
]);

async function isCleanSavedBill(record) {
    return record.resModel === "account.move" && record.resId && !record.isNew && !(await record.isDirty());
}

export class LinkedBillReceiptFormController extends AccountMoveFormController {
    async beforeExecuteActionButton(clickParams) {
        // The original document can contain the date needed to complete an
        // imported draft. Retrieving it does not save or post that draft.
        if (clickParams.type === "object" && receiptActions.has(clickParams.name)
            && await isCleanSavedBill(this.model.root)) {
            return true;
        }
        return super.beforeExecuteActionButton(clickParams);
    }
}

export class LinkedBillReceiptAttachDocumentWidget extends AttachDocumentWidget {
    async beforeOpen() {
        if (await isCleanSavedBill(this.props.record)) {
            return true;
        }
        return super.beforeOpen();
    }
}

registry.category("views").add("usl_linked_bill_receipt_form", {
    ...AccountMoveFormView,
    Controller: LinkedBillReceiptFormController,
});
registry.category("view_widgets").add("usl_linked_bill_receipt_upload", {
    ...attachDocumentWidget,
    component: LinkedBillReceiptAttachDocumentWidget,
});
