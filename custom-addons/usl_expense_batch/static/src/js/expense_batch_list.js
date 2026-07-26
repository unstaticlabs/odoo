import { ExpenseListController } from "@hr_expense/views/list";
import { patch } from "@web/core/utils/patch";

patch(ExpenseListController.prototype, {
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
                await this.model.root.load();
            },
        });
    },
});
