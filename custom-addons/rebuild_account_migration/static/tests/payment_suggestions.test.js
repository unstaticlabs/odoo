import { expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { Deferred } from "@web/core/utils/concurrency";
import {
    contains,
    defineModels,
    fields,
    mockService,
    models,
    mountView,
    onRpc,
} from "@web/../tests/web_test_helpers";

class AccountMove extends models.Model {
    _name = "account.move";

    invoice_outstanding_credits_debits_widget = fields.Json();
    invoice_payments_widget = fields.Json();

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
        {
            id: 4,
            invoice_outstanding_credits_debits_widget: {
                content: [
                    {
                        id: 45,
                        move_id: 87,
                        move_name: "BNK1/25-26/0300",
                        amount: 5.03,
                        currency_id: 1,
                        date: "2026-07-20",
                        can_assign: true,
                        can_immediate_settle: true,
                        immediate_settlement_confidence: "high",
                        immediate_settlement_reason:
                            "Match the exact document amount against the actual bank amount and record the resulting company-currency settlement difference.",
                        immediate_settlement_preview:
                            "Settle $5.00 against €4.40 · records €0.02 FX loss",
                        immediate_settlement_synthetic_preview:
                            "Odoo estimated payment: $5.03",
                        show_immediate_settlement_reason: true,
                    },
                ],
                move_id: 4,
                outstanding: true,
                title: "Outstanding debits",
            },
        },
        {
            id: 5,
            invoice_outstanding_credits_debits_widget: {
                content: [
                    {
                        id: 46,
                        move_id: 88,
                        move_name: "BNK1/25-26/0301",
                        amount: 5,
                        currency_id: 1,
                        date: "2026-07-20",
                        can_assign: true,
                        can_immediate_settle: false,
                        immediate_settlement_reason:
                            "The bank transaction is 8 days from the document, above the 3-day exact-settlement policy.",
                        show_immediate_settlement_reason: true,
                    },
                ],
                move_id: 5,
                outstanding: true,
                title: "Outstanding debits",
            },
        },
        {
            id: 6,
            invoice_payments_widget: {
                content: [
                    {
                        id: 47,
                        move_id: 89,
                        name: "Exact foreign-amount settlement",
                        amount: 5,
                        currency_id: 1,
                        date: "2026-07-20",
                        partial_id: 99,
                        is_exchange: false,
                        is_refund: false,
                        is_immediate_settlement: true,
                        settlement_summary: "$5.00 · €0.02 FX loss",
                        executed_pair:
                            "$5.00 from the document = €4.40 reported on the bank statement",
                        synthetic_estimate: "$5.03",
                        carrying_value: "€4.38",
                        settlement_difference_label: "€0.02 FX loss",
                        exchange_account_name: "656000 Commercial FX loss",
                        exchange_move_names: "EXCH/2026/00001",
                        executed_rate: 0.88,
                        reference_rate: 0.874,
                        provenance:
                            "EUR amount from bank statement; foreign amount from selected document residual",
                        journal_name: "Shine EUR",
                        ref: "IMS/2026/00001",
                    },
                ],
                outstanding: false,
                title: "Less Payment",
                exchange_info: { line_ids: [] },
            },
        },
        {
            id: 7,
            invoice_outstanding_credits_debits_widget: {
                content: [
                    {
                        id: 48,
                        move_id: 90,
                        move_name: "BNK1/25-26/0302",
                        amount: 5,
                        currency_id: 1,
                        date: "2026-07-20",
                        can_assign: true,
                        can_immediate_settle: true,
                        immediate_settlement_confidence: "normal",
                        immediate_settlement_reason:
                            "Match the exact document amount against the actual bank amount and record the resulting company-currency settlement difference.",
                        immediate_settlement_preview:
                            "Settle $5.00 against €4.38 · no settlement difference",
                        immediate_settlement_synthetic_preview:
                            "Odoo estimated payment: $5.00",
                        show_immediate_settlement_reason: true,
                    },
                ],
                move_id: 7,
                outstanding: true,
                title: "Outstanding debits",
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
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_best"
    ).toHaveAttribute(
        "title",
        "Exact amount · Same currency · Date within 7 days · Native payment"
    );
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_evidence"
    ).toHaveText(
        "Exact amount · Date within 7 days"
    );
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_source"
    ).toHaveCount(0);
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

test("bank suggestion keeps changes in the Add helper", async () => {
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
        "tr.o_rebuild_payment_suggestion_detail .badge"
    ).toHaveCount(2);
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_kind"
    ).toHaveText("Bank transaction");
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_change"
    ).toHaveCount(0);
    expect(".outstanding_credit_assign").toHaveText("Add");
    expect(".outstanding_credit_assign").toHaveAttribute(
        "title",
        "Add this bank transaction to the bill. Odoo will use the bill supplier, move the outstanding amount to the payable account, and reconcile the available amount."
    );
});

test("eligible suggestion keeps Settle recommended beside native Add", async () => {
    onRpc("js_settle_outstanding_line", ({ args, model }) => {
        expect.step("settle");
        expect(model).toBe("account.move");
        expect(args).toEqual([4, 45]);
        return { settlement_id: 7 };
    });
    await mountView({
        type: "form",
        resModel: "account.move",
        resId: 4,
        arch: `
            <form>
                <field
                    name="invoice_outstanding_credits_debits_widget"
                    widget="payment"
                />
            </form>
        `,
    });

    expect(".immediate_settlement_assign").toHaveCount(1);
    expect(".immediate_settlement_assign").toHaveText("Settle");
    expect(".immediate_settlement_assign").toHaveClass("btn-primary");
    expect(".immediate_settlement_assign").toHaveAttribute(
        "title",
        "Match the exact document amount against the actual bank amount and record the resulting company-currency settlement difference."
    );
    expect(".o_rebuild_payment_suggestion_settlement_preview").toHaveText(
        "Settle $5.00 against €4.40 · records €0.02 FX loss"
    );
    expect(".o_rebuild_payment_suggestion_settlement_estimate").toHaveText(
        "Odoo estimated payment: $5.03"
    );
    expect(".outstanding_credit_assign").toHaveCount(1);
    expect(".o_immediate_settlement_actions").toHaveClass("flex-wrap");
    await contains(".immediate_settlement_assign").click();
    expect.verifySteps(["settle"]);
});

test("zero company-currency difference is explained without an FX claim", async () => {
    await mountView({
        type: "form",
        resModel: "account.move",
        resId: 7,
        arch: `
            <form>
                <field
                    name="invoice_outstanding_credits_debits_widget"
                    widget="payment"
                />
            </form>
        `,
    });

    expect(".immediate_settlement_assign").toHaveClass("btn-outline-primary");
    expect(".o_rebuild_payment_suggestion_settlement_preview").toHaveText(
        "Settle $5.00 against €4.38 · no settlement difference"
    );
});

test("blocked payment keeps only Add and a plain-language reason", async () => {
    await mountView({
        type: "form",
        resModel: "account.move",
        resId: 5,
        arch: `
            <form>
                <field
                    name="invoice_outstanding_credits_debits_widget"
                    widget="payment"
                />
            </form>
        `,
    });

    expect(".immediate_settlement_assign").toHaveCount(0);
    expect(".outstanding_credit_assign").toHaveCount(1);
    expect(".o_rebuild_payment_suggestion_settlement_blocker").toHaveText(
        "Settle unavailable"
    );
    expect(".o_rebuild_payment_suggestion_settlement_blocker").toHaveAttribute(
        "title",
        "The bank transaction is 8 days from the document, above the 3-day exact-settlement policy."
    );
});

test("Settle prevents duplicate clicks and reports a server error", async () => {
    const deferred = new Deferred();
    mockService("notification", {
        add(message, { type }) {
            expect.step(`${type}:${message}`);
        },
    });
    onRpc("js_settle_outstanding_line", async () => {
        await deferred;
        throw new Error("RPC error");
    });
    await mountView({
        type: "form",
        resModel: "account.move",
        resId: 4,
        arch: `
            <form>
                <field
                    name="invoice_outstanding_credits_debits_widget"
                    widget="payment"
                />
            </form>
        `,
    });

    await contains(".immediate_settlement_assign").click();
    expect(".immediate_settlement_assign").not.toBeEnabled();
    expect(".immediate_settlement_assign").toHaveAttribute("aria-busy", "true");
    expect(".immediate_settlement_assign .spinner-border").toHaveCount(1);
    deferred.resolve();
    await expect.waitForSteps(["danger:RPC error"]);
    await animationFrame();
    expect(".immediate_settlement_assign").toBeEnabled();
});

test("settled payment trace distinguishes bank facts from Odoo's estimate", async () => {
    await mountView({
        type: "form",
        resModel: "account.move",
        resId: 6,
        arch: `
            <form>
                <field name="invoice_payments_widget" widget="payment"/>
            </form>
        `,
    });

    expect(".o_payment_label").toHaveText(
        /Settled · \$5.00 · €0.02 FX loss on/
    );
    await contains(".js_payment_info").click();
    expect(".account_payment_popover").toHaveText(/Settlement pair:/);
    expect(".account_payment_popover").toHaveText(
        /\$5.00 from the document = €4.40 reported on the bank statement/
    );
    expect(".account_payment_popover").toHaveText(/Discarded Odoo estimate:/);
    expect(".account_payment_popover").toHaveText(/\$5.03/);
    expect(".account_payment_popover").toHaveText(/€0.02 FX loss/);
    expect(".account_payment_popover").toHaveText(/656000 Commercial FX loss/);
    expect(".account_payment_popover").toHaveText(/EXCH\/2026\/00001/);
});
