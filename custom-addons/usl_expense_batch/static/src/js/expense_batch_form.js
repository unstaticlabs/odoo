import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { registry } from "@web/core/registry";
import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";
import { ListRenderer } from "@web/views/list/list_renderer";
import { listView } from "@web/views/list/list_view";
import { SelectCreateDialog } from "@web/views/view_dialogs/select_create_dialog";

export const EXPENSE_BATCH_ADD_STATES = ["draft", "approved", "posted"];

export function relationalId(value) {
    if (Array.isArray(value)) {
        return value[0];
    }
    return value?.id || value?.resId || value || false;
}

export function expenseBatchCandidateDomain(data) {
    return [
        ["employee_id", "=", relationalId(data.employee_id)],
        ["company_id", "=", relationalId(data.company_id)],
        ["expense_batch_id", "=", false],
        ["state", "in", EXPENSE_BATCH_ADD_STATES],
    ];
}

export class ExpenseBatchAddDialog extends SelectCreateDialog {
    static template = "usl_expense_batch.ExpenseBatchAddDialog";

    get viewProps() {
        return {
            ...super.viewProps,
            allowSelectors: true,
            allowOpenAction: false,
            type: "list",
        };
    }
}

export class ExpenseBatchCandidateListRenderer extends ListRenderer {
    get hasSelectors() {
        return this.props.allowSelectors;
    }
}

export const ExpenseBatchCandidateListView = {
    ...listView,
    Renderer: ExpenseBatchCandidateListRenderer,
};

export class ExpenseBatchFormController extends FormController {
    setup() {
        super.setup();
        this.dialogService = useService("dialog");
        this.notificationService = useService("notification");
    }

    async beforeExecuteActionButton(clickParams) {
        if (clickParams.name !== "action_open_add_expenses_wizard") {
            return super.beforeExecuteActionButton(...arguments);
        }

        const saved = await super.beforeExecuteActionButton(...arguments);
        if (saved === false) {
            return false;
        }

        const record = this.model.root;
        const batchId = record.resId;
        this.dialogService.add(ExpenseBatchAddDialog, {
            context: {
                ...this.props.context,
                list_view_ref:
                    "usl_expense_batch.view_expense_batch_add_candidate_list",
                search_view_ref:
                    "usl_expense_batch.view_expense_batch_add_candidate_search",
            },
            domain: expenseBatchCandidateDomain(record.data),
            multiSelect: true,
            noCreate: true,
            noContentHelp: _t(
                "No unbatched draft, approved, or posted expenses are available for this employee and company.",
            ),
            onSelected: async (expenseIds) => {
                await this.orm.call("usl.expense.batch", "add_expenses", [
                    [batchId],
                    expenseIds,
                ]);
                await this.model.load();
                this.notificationService.add(
                    _t(
                        "%s expense(s) added to this Batch.",
                        expenseIds.length,
                    ),
                    { type: "success" },
                );
            },
            resModel: "hr.expense",
            title: _t("Add expenses to %s", record.data.name),
        });
        return false;
    }
}

export const ExpenseBatchFormView = {
    ...formView,
    Controller: ExpenseBatchFormController,
};

registry.category("views").add("usl_expense_batch_form", ExpenseBatchFormView);
registry
    .category("views")
    .add("usl_expense_batch_candidate_list", ExpenseBatchCandidateListView);
