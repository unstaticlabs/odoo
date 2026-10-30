import { useBus } from "@web/core/utils/hooks";
import { registry } from "@web/core/registry";
import { SelectionField, selectionField } from "@web/views/fields/selection/selection_field";
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

// Serialize the complete choice across rows, including rapid successive clicks.
// Native child onchanges cannot reliably return sibling edits to an x2many list.
const pendingChoices = new WeakMap();
export class PurchaseAttachmentUseField extends SelectionField {
    setup() {
        super.setup();
        useBus(this.props.record.model.bus, "NEED_LOCAL_CHANGES", ({ detail }) => {
            const pending = pendingChoices.get(this.props.record.model.root);
            if (pending) {
                detail.proms.push(pending);
            }
        });
    }

    onChange(value) {
        const record = this.props.record;
        const review = record.model.root;
        if (review.resModel !== "usl.purchase.mail.grouping.review" || this.props.name !== "use") {
            return super.onChange(value);
        }
        const apply = async () => {
            if (value === "main") {
                for (const sibling of review.data.line_ids.records) {
                    if (sibling !== record && sibling.data.use === "main") {
                        await sibling.update({ use: "supporting" });
                    }
                }
            }
            await record.update({ use: value ?? false }, { save: this.props.autosave });
        };
        const pending = (pendingChoices.get(review) || Promise.resolve()).then(apply, apply);
        pendingChoices.set(review, pending);
        return pending;
    }
}
registry.category("fields").add("usl_purchase_attachment_use", {
    ...selectionField,
    component: PurchaseAttachmentUseField,
});
