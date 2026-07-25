import { ExpenseKanbanController } from "@hr_expense/views/kanban";
import { ExpenseListController } from "@hr_expense/views/list";
import { Domain } from "@web/core/domain";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

const expenseUploadNavigation = () => ({
    async onChangeFileInput() {
        try {
            await this._onChangeFileInput([...this.fileInput.el.files]);
            if (this.uploadsProcessing === 1) {
                const actionName = _t("Expenses");
                const currentAction = this.actionService.currentController.action;
                let domain = [["id", "in", this.createdExpenseIds]];
                const options = {};
                if (currentAction.name === actionName) {
                    domain = Domain.or([domain, currentAction.domain]).toList();
                    options.stackPosition = "replaceCurrentAction";
                }
                await this.actionService.doAction(
                    {
                        name: actionName,
                        res_model: "hr.expense",
                        type: "ir.actions.act_window",
                        views: [
                            [false, this.env.config.viewType],
                            [false, "form"],
                        ],
                        domain,
                        context: this.props.context,
                    },
                    options,
                );
            }
        } finally {
            this.uploadsProcessing--;
        }
    },
});

patch(ExpenseListController.prototype, expenseUploadNavigation());
patch(ExpenseKanbanController.prototype, expenseUploadNavigation());
