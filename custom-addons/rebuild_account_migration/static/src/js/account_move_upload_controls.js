import { AccountMoveListController } from "@account/views/account_move_list/account_move_list_controller";
import { AccountMoveKanbanController } from "@account/views/account_move_kanban/account_move_kanban_controller";
import { user } from "@web/core/user";
import { patch } from "@web/core/utils/patch";
import { onWillStart } from "@odoo/owl";

function restrictUploadButtonToAccountant() {
    onWillStart(async () => {
        this.showUploadButton =
            this.showUploadButton &&
            (await user.hasGroup("account.group_account_invoice"));
    });
}

patch(AccountMoveListController.prototype, {
    setup() {
        super.setup();
        restrictUploadButtonToAccountant.call(this);
    },
});

patch(AccountMoveKanbanController.prototype, {
    setup() {
        super.setup();
        restrictUploadButtonToAccountant.call(this);
    },
});
