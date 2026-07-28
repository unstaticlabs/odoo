import { expect, test } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    defineModels,
    fields,
    models,
    mountView,
} from "@web/../tests/web_test_helpers";

class AccountMove extends models.Model {
    _name = "account.move";

    invoice_outstanding_credits_debits_widget = fields.Json();

    _records = [
        {
            id: 1,
            invoice_outstanding_credits_debits_widget: {
                content: [
                    {
                        id: 42,
                        move_id: 84,
                        move_name: "BNK1/25-26/0241",
                        amount: 82.8,
                        currency_id: 1,
                        date: "2026-06-10",
                        can_assign: false,
                        is_best_match: true,
                        match_reason:
                            "Exact amount · Same currency · Date within 7 days · Native payment",
                        match_summary:
                            "Exact amount · Date within 7 days",
                    },
                ],
                move_id: 1,
                outstanding: true,
                title: "Suggested existing payments",
            },
        },
        {
            id: 2,
            invoice_outstanding_credits_debits_widget: {
                content: [
                    {
                        id: 43,
                        move_id: 85,
                        move_name: "BNK1/25-26/0242",
                        amount: 82.8,
                        currency_id: 1,
                        date: "2026-06-10",
                        can_assign: true,
                        match_reason: "Exact amount · Same currency",
                        match_summary: "Exact amount",
                    },
                ],
                move_id: 2,
                outstanding: true,
                title: "Suggested existing payments",
            },
        },
        {
            id: 3,
            invoice_outstanding_credits_debits_widget: {
                content: [
                    {
                        id: 44,
                        move_id: 86,
                        move_name: "BNK1/25-26/0298",
                        amount: 166.8,
                        currency_id: 1,
                        date: "2026-07-16",
                        can_assign: true,
                        is_best_match: true,
                        is_bank_statement_candidate: true,
                        match_confidence: "medium",
                        match_reason:
                            "Exact amount · Date 1 day from due date · Assigned partner Wrong Supplier differs from bill supplier IWG",
                        match_summary:
                            "Exact amount · Date 1 day from due date",
                        partner_reassignment_required: true,
                        assigned_partner_name: "Wrong Supplier",
                        account_reassignment_required: true,
                        source_account_name: "471000 Suspense",
                        target_account_name: "401100 Payable",
                    },
                ],
                move_id: 3,
                outstanding: true,
                title: "Suggested existing payments",
            },
        },
    ];
}

defineMailModels();
defineModels([AccountMove]);

test("draft bill payment suggestion keeps matching details outside the native row", async () => {
    await mountView({
        type: "form",
        resModel: "account.move",
        resId: 1,
        arch: `
            <form>
                <field
                    name="invoice_outstanding_credits_debits_widget"
                    widget="payment"
                />
            </form>
        `,
    });

    expect("tr.o_rebuild_payment_suggestion").toHaveCount(1);
    expect("tr.o_rebuild_payment_suggestion_detail").toHaveCount(1);
    expect(
        "tr.o_rebuild_payment_suggestion .open_account_move .badge"
    ).toHaveCount(0);
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_best"
    ).toHaveText("Best match");
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_evidence"
    ).toHaveText(
        "Exact amount · Date within 7 days"
    );
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_source"
    ).toHaveText("Evidence");
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_source"
    ).toHaveAttribute(
        "title",
        "Exact amount · Same currency · Date within 7 days · Native payment"
    );
    expect("tr.o_rebuild_payment_suggestion [aria-disabled='true']").toHaveText(
        "Available after posting"
    );
    expect(".outstanding_credit_assign").toHaveCount(0);
});

test("posted bill keeps Odoo's native Add matching action", async () => {
    await mountView({
        type: "form",
        resModel: "account.move",
        resId: 2,
        arch: `
            <form>
                <field
                    name="invoice_outstanding_credits_debits_widget"
                    widget="payment"
                />
            </form>
        `,
    });

    expect("#outstanding").toHaveClass("text-nowrap");
    expect(".outstanding_credit_assign").toHaveCount(1);
    expect(".outstanding_credit_assign").toHaveText("Add");
    expect(".outstanding_credit_assign").toHaveAttribute(
        "title",
        "Add this existing payment to the bill and reconcile the available amount."
    );
    expect("[aria-disabled='true']").toHaveCount(0);
});

test("bank suggestion discloses partner and account reassignment", async () => {
    await mountView({
        type: "form",
        resModel: "account.move",
        resId: 3,
        arch: `
            <form>
                <field
                    name="invoice_outstanding_credits_debits_widget"
                    widget="payment"
                />
            </form>
        `,
    });

    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_partner_change"
    ).toHaveText(
        "When added, the bank transaction will use the bill supplier instead of Wrong Supplier."
    );
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_account_change"
    ).toHaveText(
        "When added, move the outstanding amount from suspense to the bill payable account, then reconcile it."
    );
    expect("tr.o_rebuild_payment_suggestion_detail .badge").toHaveCount(0);
    expect(".outstanding_credit_assign").toHaveText("Add");
    expect(".outstanding_credit_assign").toHaveAttribute(
        "title",
        "Add this bank transaction to the bill. Odoo will use the bill supplier, move the outstanding amount to the payable account, and reconcile the available amount."
    );
});
