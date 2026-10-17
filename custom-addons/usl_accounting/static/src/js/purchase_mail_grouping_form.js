import { AccountMoveFormController } from "@account/components/account_move_form/account_move_form";
import { patch } from "@web/core/utils/patch";

patch(AccountMoveFormController.prototype, {
    async beforeExecuteActionButton(clickParams) {
        const record = this.model.root;
        // Review current persisted evidence before the original invoice date
        // is known. Pending edits and financial actions keep native saving.
        if (clickParams.type === "object" && clickParams.name === "action_review_mail_grouping"
            && record.resModel === "account.move" && record.resId && !record.isNew
            && ["in_invoice", "in_refund"].includes(record.data.move_type)
            && !(await record.isDirty())) {
            return true;
        }
        return super.beforeExecuteActionButton(clickParams);
    },
});
