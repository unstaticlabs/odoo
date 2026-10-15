/** @odoo-module **/

import { AccountPaymentField } from "@account/components/account_payment_field/account_payment_field";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { formatMonetary } from "@web/views/fields/formatters";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";

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

    async showPostMatchNativeAction(action) {
        try {
            await this.action.doAction(action);
        } catch {
            this.notification.add(_t("The accounting result is unchanged, but Odoo's additional setup dialog could not be opened. Reload the bill to review it."),
                { type: "warning", sticky: true });
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
                _t("Odoo may ask for its native additional posting confirmation."),
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
                    let nativeConfirmationOpen = false;
                    try {
                        const result = await this.orm.call(this.props.record.resModel,
                            "js_post_and_match_outstanding_line", [moveId, lineId], {
                                confirmed: true, expected_write_date: preview.bill_write_date,
                                expected_confirmation: preview.confirmation_key,
                            });
                        if (result.status === "posting_confirmation_required" && result.native_action) {
                            let closed = false;
                            await this.action.doAction(result.native_action, {
                                onClose: async (info) => {
                                    if (closed) {
                                        return;
                                    }
                                    closed = true;
                                    let matched = false;
                                    try {
                                        if (info?.usl_post_match_validated && info.bill_id === preview.bill_id &&
                                            info.posted_ids?.includes(preview.bill_id)) {
                                            const [bill] = await this.orm.call(this.props.record.resModel,
                                                "read", [moveId, ["state", "company_id", "commercial_partner_id", "currency_id", "amount_total"]], {});
                                            if (typeof preview.reviewed_match_key !== "string" || preview.reviewed_match_key.length !== 64 ||
                                                !bill || bill.id !== preview.bill_id || bill.state !== "posted" ||
                                                bill.company_id[0] !== preview.company_id ||
                                                bill.commercial_partner_id[0] !== preview.supplier_id ||
                                                bill.currency_id[0] !== preview.currency_id || bill.amount_total !== preview.amount) {
                                                this.notification.add(_t("The posting outcome changed. Review the bill and its available payments before matching."),
                                                    { type: "warning", sticky: true });
                                            } else {
                                                await this.orm.call(this.props.record.resModel, "js_assign_outstanding_line",
                                                    [moveId, lineId], { expected_reviewed_match_key: preview.reviewed_match_key });
                                                matched = true;
                                                this.notification.add(_t("The bill was posted and its payment was matched."), { type: "success" });
                                            }
                                        } else if (info?.usl_post_match_validated) {
                                            this.notification.add(_t("The bill was not posted; its payment has not been matched. Review the native posting confirmation."),
                                                { type: "warning", sticky: true });
                                        }
                                        // Cancel/X only refreshes. A concurrently posted bill is
                                        // never permission to match after this user cancelled.
                                        await this.props.record.model.root.load();
                                    } catch (error) {
                                        this.notification.add(matched
                                            ? _t("The bill was posted and its payment was matched, but this view could not be refreshed. Reload the bill to review its current status.")
                                            : [error?.data?.message,
                                                _t("The payment outcome could not be confirmed. Refresh the bill and check its current status before trying again.")].filter(Boolean).join("\n\n"),
                                            { type: "warning", sticky: true });
                                    } finally {
                                        release();
                                        if (info?.native_action) {
                                            // The action service awaits onClose before removing
                                            // its dialog. Open the optional native action only
                                            // after that close completes, avoiding reentrancy.
                                            browser.setTimeout(() => this.showPostMatchNativeAction(info.native_action), 0);
                                        }
                                    }
                                },
                            });
                            nativeConfirmationOpen = true;
                            return;
                        }
                        this.notification.add(result.message, {
                            type: result.status === "matched" ? "success" : "warning",
                            sticky: result.status !== "matched",
                        });
                        try {
                            await this.props.record.model.root.load();
                        } catch {
                            this.notification.add(
                                result.state === "posted"
                                    ? _t("The bill was posted, but this view could not be refreshed. Reload the bill to review its current payment status.")
                                    : _t("This view could not be refreshed. Reload the bill to review its posting and payment status."),
                                { type: "warning", sticky: true }
                            );
                        }
                        if (result.native_action) {
                            await this.showPostMatchNativeAction(result.native_action);
                        }
                    } catch (error) {
                        this.notification.add([error?.data?.message,
                            _t("The outcome could not be confirmed. Refresh the bill and check whether it was posted or matched before trying again.")].filter(Boolean).join("\n\n"),
                            { type: "danger", sticky: true });
                    } finally {
                        if (!nativeConfirmationOpen) {
                            release();
                        }
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
