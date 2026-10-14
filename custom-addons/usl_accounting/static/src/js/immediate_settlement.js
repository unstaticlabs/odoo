/** @odoo-module **/

import { AccountPaymentField } from "@account/components/account_payment_field/account_payment_field";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { formatMonetary } from "@web/views/fields/formatters";
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
        this.dialog = useService("dialog");
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


    async postAndMatchOutstandingCredit(moveId, lineId) {
        if (this.immediateSettlementState.pendingLineId) {
            return;
        }
        this.immediateSettlementState.pendingLineId = lineId;
        this.immediateSettlementState.pendingAction = "post_match";
        const release = () => {
            this.immediateSettlementState.pendingLineId = false;
            this.immediateSettlementState.pendingAction = false;
        };
        try {
            if (await this.props.record.isDirty()) {
                release();
                this.notification.add(_t("Save your bill changes before reviewing Post and match."), { type: "warning" });
                return;
            }
            const preview = await this.orm.call(this.props.record.resModel,
                "get_draft_post_match_preview", [moveId, lineId], {});
            const amount = formatMonetary(preview.amount, { currencyId: preview.currency_id });
            const body = [
                _t("This will post the bill and then match the selected payment."),
                _t("Bill: %s", preview.bill_name),
                _t("Supplier: %s", preview.supplier_name),
                _t("Company: %s", preview.company_name),
                _t("Total: %s (%s)", amount, preview.currency_name),
                _t("Bill date: %s", preview.bill_date || _t("Not set")),
                _t("Accounting date: %s", preview.accounting_date),
                ...(preview.current_accounting_date !== preview.accounting_date
                    ? [_t("Odoo will move the accounting date from %s because of the lock dates.", preview.current_accounting_date)] : []),
                preview.numbering_note,
                _t("Payment: %s", preview.payment_label),
                _t("Journal entry: %s", preview.payment_entry),
                _t("If matching fails, the bill will remain posted. Review its available payments before trying again."),
            ].join("\n\n");
            this.dialog.add(ConfirmationDialog, {
                title: _t("Post bill and match payment?"), size: "md", body,
                confirmLabel: _t("Post and match"), cancelLabel: _t("Cancel"),
                cancel: release,
                confirm: async () => {
                    try {
                        const result = await this.orm.call(this.props.record.resModel,
                            "js_post_and_match_outstanding_line", [moveId, lineId], {
                                confirmed: true, expected_write_date: preview.bill_write_date,
                                expected_confirmation: preview.confirmation_key,
                            });
                        this.notification.add(result.message, {
                            type: result.status === "matched" ? "success" : "warning",
                            sticky: result.status !== "matched",
                        });
                        await this.props.record.model.root.load();
                    } catch (error) {
                        this.notification.add([error?.data?.message,
                            _t("The outcome could not be confirmed. Refresh the bill and check whether it was posted or matched before trying again.")].filter(Boolean).join("\n\n"),
                            { type: "danger", sticky: true });
                    } finally {
                        release();
                    }
                },
            });
        } catch (error) {
            release();
            this.notification.add(error?.data?.message ||
                _t("The confirmation could not be loaded. Refresh the bill and review its available payments."),
                { type: "danger" });
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
