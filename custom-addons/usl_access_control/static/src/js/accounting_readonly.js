import { BillGuide } from "@account/components/bill_guide/bill_guide";
import { user } from "@web/core/user";
import { patch } from "@web/core/utils/patch";
import { onWillStart } from "@odoo/owl";

patch(BillGuide.prototype, {
    setup() {
        super.setup();
        this.uslReadonlyAccounting = false;
        onWillStart(async () => {
            this.uslReadonlyAccounting =
                (await user.hasGroup("account.group_account_readonly")) &&
                !(await user.hasGroup("account.group_account_user"));
        });
    },
});
