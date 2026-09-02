import { ExpenseListController } from "@hr_expense/views/list";
import { patch } from "@web/core/utils/patch";

export const EXPENSE_BATCH_ELIGIBLE_STATES = new Set([
    "draft",
    "approved",
    "posted",
]);

export function canCreateExpenseBatch(records) {
    return (
        records.length > 0 &&
        records.every(
            (record) =>
                EXPENSE_BATCH_ELIGIBLE_STATES.has(record.data.state) &&
                !record.data.expense_batch_id
        )
    );
}

export function batchActionIsPrimary(records) {
    return canCreateExpenseBatch(records);
}

export async function refreshExpenseList(controller) {
    await controller.model.root.load();
    controller.render(true);
}

patch(ExpenseListController.prototype, {
    displayCreateExpenseBatch() {
        return canCreateExpenseBatch(this.model.root.selection);
    },

    batchActionIsPrimary() {
        return batchActionIsPrimary(this.model.root.selection);
    },

    async onOpenExpenseBatchWizard() {
        const expenseIds = this.model.root.selection.map((record) => record.resId);
        const action = await this.orm.call(
            "hr.expense",
            "action_open_expense_batch_wizard",
            [expenseIds],
            { context: this.props.context },
        );
        await this.actionService.doAction(action, {
            onClose: async () => {
                await refreshExpenseList(this);
            },
        });
    },
});
