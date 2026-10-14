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
    name = fields.Char();

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
                        bank_label: "Synthetic office equipment payment",
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
                        partner_evidence:
                            "Assigned partner Wrong Supplier differs from document partner IWG",
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
                        is_best_match: true,
                        is_bank_statement_candidate: true,
                        match_reason:
                            "Reference match · Larger available payment · 1-day gap from due date · Bank transaction · Assigned partner matches Cloudflare",
                        match_summary:
                            "Reference match · Larger available payment · 1-day gap from due date",
                        partner_evidence:
                            "Assigned partner matches Cloudflare",
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
                        is_best_match: true,
                        is_bank_statement_candidate: true,
                        match_reason:
                            "Reference match · Larger available payment · 8-day gap from document · Bank transaction · Assigned partner matches Cloudflare",
                        match_summary:
                            "Reference match · Larger available payment",
                        partner_evidence:
                            "Assigned partner matches Cloudflare",
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
                        is_best_match: true,
                        is_bank_statement_candidate: true,
                        match_reason:
                            "Reference match · Bank transaction · Assigned partner matches Cloudflare",
                        match_summary: "Reference match · Exact amount",
                        partner_evidence:
                            "Assigned partner matches Cloudflare",
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
                        is_best_match: true,
                        is_bank_statement_candidate: true,
                        match_reason:
                            "Reference match · Bank transaction · Assigned partner matches Cloudflare",
                        match_summary: "Reference match · Larger available payment",
                        partner_evidence:
                            "Assigned partner matches Cloudflare",
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

AccountMove._records.push({
    id: 10, name: "QA saved bill",
    invoice_outstanding_credits_debits_widget: {
        content: [{ id: 51, move_id: 93, move_name: "QA/BANK/001", amount: 24,
            currency_id: 1, can_assign: false, can_post_match: true }],
        move_id: 10, outstanding: true, title: "Suggested payments",
    },
});

defineMailModels();
defineModels([AccountMove]);

test("bank suggestion shows its label and keeps native entry navigation", async () => {
    onRpc("action_open_business_doc", ({ args }) => {
        expect.step("open-bank-entry");
        expect(args).toEqual([86]);
        return { type: "ir.actions.act_window", res_model: "account.move", res_id: 86 };
    });
    mockService("action", { doAction: () => {} });
    await mountView({
        type: "form", resModel: "account.move", resId: 3,
        arch: '<form><field name="invoice_outstanding_credits_debits_widget" widget="payment"/></form>',
    });
    expect(".open_account_move").toHaveText("Synthetic office equipment payment");
    expect(".open_account_move").toHaveAttribute("title", "BNK1/25-26/0298");
    expect(".open_account_move").toHaveAttribute("aria-label", "Synthetic office equipment payment — BNK1/25-26/0298");
    await contains(".open_account_move").click();
    expect.verifySteps(["open-bank-entry"]);
});

test("a bank suggestion without a label keeps the entry identifier", async () => {
    await mountView({
        type: "form", resModel: "account.move", resId: 4,
        arch: '<form><field name="invoice_outstanding_credits_debits_widget" widget="payment"/></form>',
    });
    expect(".open_account_move").toHaveText("BNK1/25-26/0300");
});

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
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_context"
    ).toHaveText(/Best match\s*Exact amount\s*Date within 7 days/);
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_best"
    ).toHaveText("Best match");
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_evidence"
    ).toHaveCount(2);
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_context"
    ).toHaveAttribute(
        "title",
        "Highest-ranked payment. Match: Exact amount; Date within 7 days. Source: existing payment."
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
    expect(".o_rebuild_payment_suggestion_context").toHaveAttribute(
        "title",
        "Alternative payment. Match: Exact amount. Source: existing payment."
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
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_context"
    ).toHaveText(
        /Best match\s*Bank transaction\s*Exact amount\s*Date 1 day from due date/
    );
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_best"
    ).toHaveText("Best match");
    expect(
        "tr.o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_kind"
    ).toHaveText("Bank transaction");
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
    expect(".o_rebuild_payment_suggestion_facts").toHaveCount(0);
    expect(".o_rebuild_payment_suggestion_recommendation").toHaveCount(0);
    expect(".o_rebuild_payment_suggestion_review").toHaveCount(0);
    expect(".o_rebuild_payment_suggestion_best").toHaveText("Best match");
    expect(".o_rebuild_payment_suggestion_kind").toHaveText("Bank transaction");
    expect(".o_rebuild_payment_suggestion_context").toHaveText(
        /Best match\s*Bank transaction\s*Reference match\s*Larger available payment\s*1-day gap from due date/
    );
    expect(".outstanding_credit_assign").toHaveCount(1);
    expect(".outstanding_credit_assign").toHaveAttribute(
        "title",
        "Use Odoo's $5.03 estimate. This may leave a $0.03 difference."
    );
    expect(".o_rebuild_payment_suggestion_estimate_label").toHaveCount(0);
    expect(".o_rebuild_payment_suggestion_context").toHaveAttribute(
        "title",
        "Highest-ranked payment. Match: Reference match; Larger available payment; 1-day gap from due date. Source: bank transaction. Partner: Assigned partner matches Cloudflare."
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
    expect(".o_rebuild_payment_suggestion_recommendation").toHaveCount(0);
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
    expect(".o_rebuild_payment_suggestion_recommendation").toHaveCount(0);
    expect(".o_rebuild_payment_suggestion_review").toHaveCount(0);
    expect(".o_rebuild_payment_suggestion_best").toHaveText("Best match");
    expect(".o_rebuild_payment_suggestion_context").toHaveText(
        /Best match\s*Bank transaction\s*Reference match\s*Larger available payment/
    );
    expect(".o_rebuild_payment_suggestion_context").toHaveAttribute(
        "title",
        "Highest-ranked payment. Match: Reference match; Larger available payment. Source: bank transaction. Partner: Assigned partner matches Cloudflare. Review: Use payment rate is limited to 3 days; this transaction is 8 days from the document."
    );
});

test("conflicting facts keep Add with the warning in compact match evidence", async () => {
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
    expect(".o_rebuild_payment_suggestion_review").toHaveCount(0);
    expect(".o_rebuild_payment_suggestion_best").toHaveText("Best match");
    expect(".o_rebuild_payment_suggestion_context").toHaveText(
        /Best match\s*Bank transaction\s*Reference match\s*Larger available payment/
    );
    expect(".o_rebuild_payment_suggestion_context").toHaveAttribute(
        "title",
        "Highest-ranked payment. Match: Reference match; Larger available payment. Source: bank transaction. Partner: Assigned partner matches Cloudflare. Review: The bank or integration foreign amount conflicts with the document. Review it in Bank Matching."
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
    expect(".o_rebuild_payment_suggestion_facts").toHaveCount(0);
    expect(".o_rebuild_payment_suggestion_recommendation").toHaveCount(0);
    expect(".o_rebuild_payment_suggestion_review").toHaveCount(0);
    expect(".o_rebuild_payment_suggestion_best").toHaveText("Best match");
    expect(".o_rebuild_payment_suggestion_context").toHaveText(
        /Best match\s*Bank transaction\s*Reference match\s*Larger available payment\s*1-day gap from due date/
    );
});


const draftPostPreview = {
    bill_name: "QA draft bill", supplier_name: "QA supplier", company_name: "QA company",
    amount: 24, currency_id: 1, currency_name: "EUR", bill_date: "2026-10-01",
    accounting_date: "2026-10-14", current_accounting_date: "2026-10-01",
    numbering_note: "Odoo assigns the bill number when posting.",
    payment_label: "QA supplier payment", payment_entry: "QA/BANK/001",
    bill_write_date: "2026-10-14 00:00:00", confirmation_key: "qa-fingerprint",
};

async function mountDraftPostMatch() {
    return mountView({ type: "form", resModel: "account.move", resId: 10,
        arch: '<form><field name="name"/><field name="invoice_outstanding_credits_debits_widget" widget="payment"/></form>',
    });
}

test("Post and match previews posting and cancel never mutates", async () => {
    onRpc("get_draft_post_match_preview", ({ args }) => {
        expect(args).toEqual([10, 51]);
        expect.step("preview");
        return draftPostPreview;
    });
    onRpc("js_post_and_match_outstanding_line", () => expect.step("unexpected-mutation"));
    await mountDraftPostMatch();
    expect(".outstanding_credit_assign").toHaveCount(0);
    await contains(".o_draft_post_match").click();
    expect(".modal-body").toHaveText(/QA draft bill.*QA supplier.*QA company.*EUR.*2026-10-14.*lock dates.*QA supplier payment.*remain posted/s);
    expect(".o_draft_post_match").toHaveAttribute("disabled");
    await contains(".modal-footer button:contains(Cancel)").click();
    expect(".modal").toHaveCount(0);
    expect(".o_draft_post_match").not.toHaveAttribute("disabled");
    expect.verifySteps(["preview"]);
});

test("Post and match sends reviewed facts once and reports posted unmatched honestly", async () => {
    const pending = new Deferred();
    onRpc("get_draft_post_match_preview", () => draftPostPreview);
    onRpc("js_post_and_match_outstanding_line", ({ args, kwargs }) => {
        expect(args).toEqual([10, 51]);
        expect(kwargs.confirmed).toBe(true);
        expect(kwargs.expected_confirmation).toBe("qa-fingerprint");
        expect(kwargs.expected_write_date).toBe("2026-10-14 00:00:00");
        expect.step("post-once");
        return pending;
    });
    mockService("notification", { add: (message, options) => {
        expect(message).toBe("The bill was posted, but its payment could not be matched.");
        expect(options.type).toBe("warning");
        expect(options.sticky).toBe(true);
        expect.step("posted-unmatched");
    } });
    await mountDraftPostMatch();
    await contains(".o_draft_post_match").click();
    await contains(".modal-footer button:contains(Post and match)").click();
    expect(".modal-footer button").toHaveAttribute("disabled");
    pending.resolve({ status: "posted_unmatched", message: "The bill was posted, but its payment could not be matched." });
    await animationFrame();
    expect(".modal").toHaveCount(0);
    expect.verifySteps(["post-once", "posted-unmatched"]);
});

test("an uncertain posting response asks for readback and never retries", async () => {
    onRpc("get_draft_post_match_preview", () => draftPostPreview);
    onRpc("js_post_and_match_outstanding_line", () => {
        expect.step("one-request");
        throw new Error("Network interrupted");
    });
    mockService("notification", { add: (message, options) => {
        expect(message).toMatch(/Refresh the bill and check whether it was posted or matched/);
        expect(options.type).toBe("danger");
        expect(options.sticky).toBe(true);
        expect.step("readback");
    } });
    await mountDraftPostMatch();
    await contains(".o_draft_post_match").click();
    await contains(".modal-footer button:contains(Post and match)").click();
    expect(".modal").toHaveCount(0);
    expect.verifySteps(["one-request", "readback"]);
});


test("Post and match never saves dirty bill edits or requests a preview", async () => {
    onRpc("get_draft_post_match_preview", () => expect.step("unexpected-preview"));
    onRpc("write", () => expect.step("unexpected-save"));
    mockService("notification", { add: (message, options) => {
        expect(message).toBe("Save your bill changes before reviewing Post and match.");
        expect(options.type).toBe("warning");
        expect.step("save-first");
    } });
    await mountDraftPostMatch();
    await contains(".o_field_widget[name='name'] input").edit("QA unsaved change");
    await contains(".o_draft_post_match").click();
    expect(".modal").toHaveCount(0);
    expect(".o_field_widget[name='name'] input").toHaveValue("QA unsaved change");
    expect(".o_draft_post_match").not.toHaveAttribute("disabled");
    expect.verifySteps(["save-first"]);
});


test("a confirmed result survives a failed view refresh without claiming an unknown outcome", async () => {
    let completed = false;
    onRpc("get_draft_post_match_preview", () => draftPostPreview);
    onRpc("js_post_and_match_outstanding_line", () => {
        completed = true;
        expect.step("posted-matched");
        return { status: "matched", message: "The bill was posted and its payment was matched." };
    });
    onRpc("web_read", () => {
        if (completed) {
            throw new Error("Synthetic view refresh failure");
        }
    });
    mockService("notification", { add: (message, options) => {
        if (options.type === "success") {
            expect(message).toBe("The bill was posted and its payment was matched.");
            expect.step("known-result");
        } else {
            expect(message).toBe("The bill was posted, but this view could not be refreshed. Reload the bill to review its current payment status.");
            expect(options.type).toBe("warning");
            expect(options.sticky).toBe(true);
            expect.step("refresh-view");
        }
    } });
    await mountDraftPostMatch();
    await contains(".o_draft_post_match").click();
    await contains(".modal-footer button:contains(Post and match)").click();
    expect(".modal").toHaveCount(0);
    expect.verifySteps(["posted-matched", "known-result", "refresh-view"]);
});
