import { expect, resize, test } from "@odoo/hoot";
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
                        can_use_payment_rate: true,
                        immediate_settlement_reason:
                            "Use the document's exact $5.00. Odoo records €0.02 FX loss.",
                        payment_rate_settlement_reason:
                            "Value the document at the bank's €4.40 rate and match $5.00. No FX.",
                        recommended_settlement_action: "payment_rate",
                        settlement_facts:
                            "Bank €4.40 · Bill $5.00",
                        settlement_recommendation:
                            "Recommended: Use payment rate · no FX",
                        add_action_helper:
                            "Use Odoo's $5.03 estimate. This may leave a $0.03 difference.",
                        amount_is_odoo_estimate: true,
                        odoo_estimate_label: "Odoo estimate",
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
                        can_immediate_settle: true,
                        can_use_payment_rate: false,
                        immediate_settlement_reason:
                            "Use the document's exact $5.00. Odoo records €0.02 FX loss. Check: 8 days after the document.",
                        payment_rate_settlement_reason:
                            "Use payment rate is limited to 3 days; this transaction is 8 days from the document.",
                        recommended_settlement_action: "settle",
                        settlement_facts:
                            "Bank €4.40 · Bill $5.00",
                        settlement_recommendation:
                            "Recommended: Settle · €0.02 FX loss",
                        add_action_helper:
                            "Use Odoo's $5.03 estimate. This may leave a $0.03 difference.",
                        amount_is_odoo_estimate: true,
                        odoo_estimate_label: "Odoo estimate",
                        settlement_review_reason:
                            "Use payment rate is limited to 3 days; this transaction is 8 days from the document.",
                        show_settlement_review: true,
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
                        can_immediate_settle: false,
                        can_use_payment_rate: true,
                        immediate_settlement_reason:
                            "Add already uses the exact foreign amount for this bank transaction.",
                        payment_rate_settlement_reason:
                            "Value the document at the bank's €4.38 rate and match $5.00. No FX.",
                        recommended_settlement_action: "payment_rate",
                        settlement_facts:
                            "Bank €4.38 · Bill $5.00",
                        settlement_recommendation:
                            "Recommended: Use payment rate · no FX",
                        add_action_helper:
                            "Use Odoo's existing $5.00 candidate.",
                        amount_is_odoo_estimate: false,
                        odoo_estimate_label: "Odoo estimate",
                    },
                ],
                move_id: 7,
                outstanding: true,
                title: "Outstanding debits",
            },
        },
        {
            id: 8,
            invoice_payments_widget: {
                content: [
                    {
                        id: 49,
                        move_id: 91,
                        name: "Payment-rate settlement",
                        amount: 5,
                        currency_id: 1,
                        date: "2026-07-20",
                        partial_id: 100,
                        is_exchange: false,
                        is_refund: false,
                        is_immediate_settlement: true,
                        is_payment_rate_settlement: true,
                        is_document_reprice: true,
                        settlement_summary: "$5.00 · Bill €4.40 · no FX",
                        settlement_method: "Use payment rate",
                        executed_pair:
                            "$5.00 from the document = €4.40 reported on the bank statement",
                        synthetic_estimate: "$5.03",
                        carrying_value: "€4.38",
                        original_document_value: "€4.38",
                        repriced_document_value: "€4.40",
                        document_revaluation_label: "€0.02",
                        original_invoice_currency_rate: 1.141553,
                        applied_invoice_currency_rate: 1.136364,
                        executed_rate: 0.88,
                        reference_rate: 0.874,
                        provenance:
                            "EUR amount from bank statement; foreign amount from selected document residual",
                        journal_name: "Shine EUR",
                        ref: "IMS/2026/00002",
                    },
                ],
                outstanding: false,
                title: "Less Payment",
                exchange_info: { line_ids: [] },
            },
        },
        {
            id: 9,
            invoice_outstanding_credits_debits_widget: {
                content: [
                    {
                        id: 50,
                        move_id: 92,
                        move_name: "BNK1/25-26/0303",
                        amount: 5.03,
                        currency_id: 1,
                        date: "2026-07-20",
                        can_assign: true,
                        can_immediate_settle: false,
                        can_use_payment_rate: false,
                        immediate_settlement_reason:
                            "The bank or integration foreign amount conflicts with the document. Review it in Bank Matching.",
                        payment_rate_settlement_reason:
                            "The bank or integration foreign amount conflicts with the document. Review it in Bank Matching.",
                        settlement_review_reason:
                            "The bank or integration foreign amount conflicts with the document. Review it in Bank Matching.",
                        show_settlement_review: true,
                    },
                ],
                move_id: 9,
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
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_evidence"
    ).toHaveText(
        "Best match · Exact amount · Date within 7 days"
    );
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_evidence"
    ).toHaveAttribute(
        "title",
        "Exact amount · Same currency · Date within 7 days · Native payment"
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
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_evidence"
    ).toHaveText("Best match · Exact amount · Date 1 day from due date");
    expect("tr.o_rebuild_payment_suggestion_detail .badge").toHaveCount(0);
    expect(".outstanding_credit_assign").toHaveText("Add");
    expect(".outstanding_credit_assign").toHaveAttribute(
        "title",
        "Add this bank transaction to the bill. Odoo will use the bill supplier, move the outstanding amount to the payable account, and reconcile the available amount."
    );
});

test("immediate suggestion shows all three actions and recommends payment rate", async () => {
    onRpc("js_assign_outstanding_line", ({ args, model }) => {
        expect.step("add");
        expect(model).toBe("account.move");
        expect(args).toEqual([4, 45]);
        return true;
    });
    onRpc("js_settle_outstanding_line", ({ args, model }) => {
        expect.step("settle");
        expect(model).toBe("account.move");
        expect(args).toEqual([4, 45]);
        return { settlement_id: 7 };
    });
    onRpc("js_use_payment_rate_outstanding_line", ({ args, model }) => {
        expect.step("payment-rate");
        expect(model).toBe("account.move");
        expect(args).toEqual([4, 45]);
        return { settlement_id: 8 };
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
    expect(".immediate_settlement_assign").toHaveClass("btn-outline-primary");
    expect(".immediate_settlement_assign").toHaveAttribute(
        "title",
        "Use the document's exact $5.00. Odoo records €0.02 FX loss."
    );
    expect(".payment_rate_assign").toHaveCount(1);
    expect(".payment_rate_assign").toHaveText("Use payment rate");
    expect(".payment_rate_assign").toHaveClass("btn-primary");
    expect(".payment_rate_assign").toHaveAttribute(
        "title",
        "Value the document at the bank's €4.40 rate and match $5.00. No FX."
    );
    expect(".o_rebuild_payment_suggestion_facts").toHaveText(
        "Bank €4.40 · Bill $5.00"
    );
    expect(".o_rebuild_payment_suggestion_recommendation").toHaveText(
        "Recommended: Use payment rate · no FX"
    );
    expect(".outstanding_credit_assign").toHaveCount(1);
    expect(".outstanding_credit_assign").toHaveAttribute(
        "title",
        "Use Odoo's $5.03 estimate. This may leave a $0.03 difference."
    );
    expect(".o_rebuild_payment_suggestion_estimate_label").toHaveText(
        "Odoo estimate"
    );
    expect(".o_immediate_settlement_actions").toHaveClass("flex-wrap");
    expect(".o_immediate_settlement_actions > :nth-child(1)").toHaveText("Add");
    expect(".o_immediate_settlement_actions > :nth-child(2)").toHaveText("Settle");
    expect(".o_immediate_settlement_actions > :nth-child(3)").toHaveText(
        "Use payment rate"
    );
    await contains(".outstanding_credit_assign").click();
    await contains(".immediate_settlement_assign").click();
    await contains(".payment_rate_assign").click();
    expect.verifySteps(["add", "settle", "payment-rate"]);
});

test("exact native candidate hides Settle but keeps payment-rate provenance", async () => {
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

    expect(".immediate_settlement_assign").toHaveCount(0);
    expect(".payment_rate_assign").toHaveCount(1);
    expect(".payment_rate_assign").toHaveClass("btn-primary");
    expect(".o_rebuild_payment_suggestion_recommendation").toHaveText(
        "Recommended: Use payment rate · no FX"
    );
});

test("delayed payment recommends Settle and keeps payment-rate warning in helper", async () => {
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

    expect(".immediate_settlement_assign").toHaveCount(1);
    expect(".immediate_settlement_assign").toHaveClass("btn-primary");
    expect(".payment_rate_assign").toHaveCount(0);
    expect(".outstanding_credit_assign").toHaveCount(1);
    expect(".immediate_settlement_assign").toHaveAttribute(
        "title",
        "Use the document's exact $5.00. Odoo records €0.02 FX loss. Check: 8 days after the document."
    );
    expect(".o_rebuild_payment_suggestion_recommendation").toHaveText(
        "Recommended: Settle · €0.02 FX loss"
    );
    expect(".o_rebuild_payment_suggestion_review").toHaveCount(1);
    expect(".o_rebuild_payment_suggestion_review").toHaveAttribute(
        "title",
        "Use payment rate is limited to 3 days; this transaction is 8 days from the document."
    );
});

test("conflicting facts keep Add with one unobtrusive review indicator", async () => {
    await mountView({
        type: "form",
        resModel: "account.move",
        resId: 9,
        arch: `
            <form>
                <field
                    name="invoice_outstanding_credits_debits_widget"
                    widget="payment"
                />
            </form>
        `,
    });

    expect(".outstanding_credit_assign").toHaveCount(1);
    expect(".immediate_settlement_assign").toHaveCount(0);
    expect(".payment_rate_assign").toHaveCount(0);
    expect(".o_rebuild_payment_suggestion_review").toHaveCount(1);
    expect(".o_rebuild_payment_suggestion_review").toHaveText("Review");
    expect(".o_rebuild_payment_suggestion_review").toHaveAttribute(
        "title",
        "The bank or integration foreign amount conflicts with the document. Review it in Bank Matching."
    );
});

test("pending payment-rate action disables the full row and reports an error", async () => {
    const deferred = new Deferred();
    mockService("notification", {
        add(message, { type }) {
            expect.step(`${type}:${message}`);
        },
    });
    onRpc("js_use_payment_rate_outstanding_line", async () => {
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

    await contains(".payment_rate_assign").click();
    expect(".immediate_settlement_assign").not.toBeEnabled();
    expect(".payment_rate_assign").not.toBeEnabled();
    expect(".outstanding_credit_assign").toHaveClass("disabled");
    expect(".payment_rate_assign").toHaveAttribute("aria-busy", "true");
    expect(".payment_rate_assign .spinner-border").toHaveCount(1);
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

test("payment-rate trace shows document repricing without technical allocation", async () => {
    await mountView({
        type: "form",
        resModel: "account.move",
        resId: 8,
        arch: `
            <form>
                <field name="invoice_payments_widget" widget="payment"/>
            </form>
        `,
    });

    expect(".o_payment_label").toHaveText(
        /Payment rate · \$5.00 · Bill €4.40 · no FX on/
    );
    await contains(".js_payment_info").click();
    expect(".account_payment_popover").toHaveText(/Method:\s*Use payment rate/);
    expect(".account_payment_popover").toHaveText(
        /Original document value:\s*€4.38/
    );
    expect(".account_payment_popover").toHaveText(
        /Payment-rate document value:\s*€4.40/
    );
    expect(".account_payment_popover").toHaveText(
        /Document revaluation:\s*€0.02/
    );
    expect(".account_payment_popover").toHaveText(/Original document rate:\s*1.141553/);
    expect(".account_payment_popover").toHaveText(/Applied document rate:\s*1.136364/);
    expect(".account_payment_popover").not.toHaveText(/Economic adjustment:/);
    expect(".account_payment_popover").not.toHaveText(/Adjusted accounts:/);
    expect(".account_payment_popover").not.toHaveText(/Exchange account:/);
    expect(".account_payment_popover").not.toHaveText(/Native exchange entry:/);
});

test("all three actions remain visible in a compact mobile layout", async () => {
    await resize({ width: 375, height: 667 });
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

    expect(".o_immediate_settlement_actions").toHaveClass("flex-wrap");
    expect(".outstanding_credit_assign").toHaveCount(1);
    expect(".immediate_settlement_assign").toHaveCount(1);
    expect(".payment_rate_assign").toHaveCount(1);
    expect(".o_rebuild_payment_suggestion_facts").toHaveCount(1);
    expect(".o_rebuild_payment_suggestion_recommendation").toHaveCount(1);
});
