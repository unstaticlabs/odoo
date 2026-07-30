/** @odoo-module **/

import { AccountPaymentField } from "@account/components/account_payment_field/account_payment_field";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { useState } from "@odoo/owl";

patch(AccountPaymentField.prototype, {
    setup() {
        super.setup(...arguments);
        this.immediateSettlementState = useState({
            pendingLineId: false,
            pendingAction: false,
        });
        this.notification = useService("notification");
    },

    isSettlementRowPending(lineId) {
        return this.immediateSettlementState.pendingLineId === lineId;
    },

    isSettlementActionPending(lineId, action) {
        return (
            this.immediateSettlementState.pendingLineId === lineId &&
            this.immediateSettlementState.pendingAction === action
        );
    },

    async runOutstandingSettlementAction(moveId, lineId, action, method = false) {
        if (this.immediateSettlementState.pendingLineId) {
            return;
        }
        this.immediateSettlementState.pendingLineId = lineId;
        this.immediateSettlementState.pendingAction = action;
        try {
            if (method) {
                await this.orm.call(
                    this.props.record.resModel,
                    method,
                    [moveId, lineId],
                    {}
                );
                await this.props.record.model.root.load();
            } else {
                await this.assignOutstandingCredit(moveId, lineId);
            }
        } catch (error) {
            this.notification.add(
                error?.data?.message ||
                    error?.message ||
                    _t("The payment action could not be completed. Refresh and try again."),
                { type: "danger" }
            );
        } finally {
            this.immediateSettlementState.pendingLineId = false;
            this.immediateSettlementState.pendingAction = false;
        }
    },

    async settleOutstandingCredit(moveId, lineId) {
        return this.runOutstandingSettlementAction(
            moveId,
            lineId,
            "settle",
            "js_settle_outstanding_line"
        );
    },

    async usePaymentRateOutstandingCredit(moveId, lineId) {
        return this.runOutstandingSettlementAction(
            moveId,
            lineId,
            "payment_rate",
            "js_use_payment_rate_outstanding_line"
        );
    },

    async addOutstandingCredit(moveId, lineId) {
        return this.runOutstandingSettlementAction(moveId, lineId, "add");
    },
});
