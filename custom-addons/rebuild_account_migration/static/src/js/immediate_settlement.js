/** @odoo-module **/

import { AccountPaymentField } from "@account/components/account_payment_field/account_payment_field";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { useState } from "@odoo/owl";

patch(AccountPaymentField.prototype, {
    setup() {
        super.setup(...arguments);
        this.immediateSettlementState = useState({ pendingLineId: false });
        this.notification = useService("notification");
    },

    isImmediateSettlementPending(lineId) {
        return this.immediateSettlementState.pendingLineId === lineId;
    },

    async settleOutstandingCredit(moveId, lineId) {
        if (this.immediateSettlementState.pendingLineId) {
            return;
        }
        this.immediateSettlementState.pendingLineId = lineId;
        try {
            await this.orm.call(
                this.props.record.resModel,
                "js_settle_outstanding_line",
                [moveId, lineId],
                {}
            );
            await this.props.record.model.root.load();
        } catch (error) {
            this.notification.add(
                error?.data?.message ||
                    error?.message ||
                    _t("The payment could not be settled. Refresh and try again."),
                { type: "danger" }
            );
        } finally {
            this.immediateSettlementState.pendingLineId = false;
        }
    },
});
