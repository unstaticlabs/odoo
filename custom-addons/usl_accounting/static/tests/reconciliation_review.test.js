import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { expect, test, waitFor } from "@odoo/hoot";
import { contains, defineModels, fields, models, mountView, onRpc } from "@web/../tests/web_test_helpers";
import { reviewGroups } from "../src/js/reconciliation_review";

class AccountAccount extends models.Model {
    _name = "account.account";
    name = fields.Char();
    _records = [{ id: 11, name: "[QA review] Receivable" }];
}
class AccountMoveLine extends models.Model {
    _name = "account.move.line";
    account_id = fields.Many2one({ relation: "account.account" });
    amount_residual = fields.Float({ aggregator: "sum" });
    _records = [{ id: 1, account_id: 11, amount_residual: 60 }, { id: 2, account_id: 11, amount_residual: 40 }];
}
class Review extends models.Model { _name = "account.reconciliation.review"; }
defineMailModels();
defineModels([AccountAccount, AccountMoveLine, Review]);

function status(state = "unreviewed", revision = 0) {
    return { scope_key: "scope", state, revision, observed_balance: "observed-remaining", amounts: [{ currency_id: 1, currency: "EUR", baseline: state === "unreviewed" ? null : 100, current: 100, delta: state === "unreviewed" ? null : 0 }],
        reviewer: "[QA review] Accountant", reviewed_at: "2026-10-17 10:00:00", domain: [], group_domain: [],
        company_ids: [1], companies: ["[QA review] Company"], presentation: {}, can_review: true };
}

test("folded group review uses one batch and never expands the native row", async () => {
    onRpc("account.reconciliation.review", "get_review_status", ({ args }) => {
        expect.step("batch");
        expect(args[1]).toEqual([[["account_id", 11]]]);
        return [status()];
    });
    onRpc("account.reconciliation.review", "mark_reviewed", ({ args }) => {
        expect.step("mark");
        expect(args[1]).toEqual([["account_id", 11]]);
        expect(args[2]).toBe(0);
        expect(args[3]).toBe("observed-remaining");
        return status("reviewed", 1);
    });
    await mountView({ resModel: "account.move.line", type: "list", groupBy: ["account_id"],
        arch: '<list js_class="usl_reconciliation_review"><field name="account_id"/><field name="amount_residual" sum="Remaining"/></list>' });
    await contains(".o_reconciliation_review_mark").click();
    expect(".o_group_open").toHaveCount(0);
    await contains(".o_reconciliation_review_details .btn-primary").click();
    expect(".o_reconciliation_review_mark .text-success").toHaveCount(1);
    expect(".o_group_open").toHaveCount(0);
    expect.verifySteps(["batch", "mark"]);
});

test("group descriptors preserve stable account, partner and resolved month paths", () => {
    const nested = { isGrouped: true, groupBy: ["date:month"], groups: [
        { serverValue: "2026-10-01", isFolded: true },
    ] };
    const partner = { serverValue: 22, isFolded: false, list: nested };
    const account = { serverValue: 11, isFolded: false, list: { isGrouped: true, groupBy: ["partner_id", "date:month"], groups: [partner] } };
    const descriptors = reviewGroups({ isGrouped: true, groupBy: ["account_id", "partner_id", "date:month"], groups: [account] });
    expect(descriptors.map(({ path }) => path)).toEqual([
        [["account_id", 11]], [["account_id", 11], ["partner_id", 22]],
        [["account_id", 11], ["partner_id", 22], ["date:month", "2026-10-01"]],
    ]);
});

test("acknowledging a warning preserves the reviewed baseline and never shows green", async () => {
    const changed = { ...status("changed", 1), amounts: [{ currency_id: 1, currency: "EUR", baseline: 100, current: 125, delta: 25 }] };
    onRpc("account.reconciliation.review", "get_review_status", () => [changed]);
    onRpc("account.reconciliation.review", "dismiss_variance", ({ args }) => {
        expect(args[2]).toBe(1);
        expect(args[3]).toBe("observed-remaining");
        expect.step("acknowledge");
        return { ...changed, state: "acknowledged", revision: 2, acknowledged_by: "[QA review] Colleague", acknowledged_at: "2026-10-17 11:00:00" };
    });
    await mountView({ resModel: "account.move.line", type: "list", groupBy: ["account_id"],
        arch: '<list js_class="usl_reconciliation_review"><field name="account_id"/><field name="amount_residual" sum="Remaining"/></list>' });
    await contains(".o_reconciliation_review_mark").click();
    expect(".o_reconciliation_review_details").toHaveText(/125/);
    await contains(".o_reconciliation_review_details .btn-secondary").click();
    expect(".o_reconciliation_review_mark .text-success").toHaveCount(0);
    expect(".o_reconciliation_review_mark .fa-minus-circle").toHaveCount(1);
    await contains(".o_reconciliation_review_mark").click();
    expect(".o_reconciliation_review_details").toHaveText(/Colleague/);
    expect(".o_reconciliation_review_details").toHaveText(/100/);
    expect.verifySteps(["acknowledge"]);
});

test("a failed status read offers retry without showing a stale green mark", async () => {
    let attempts = 0;
    onRpc("account.reconciliation.review", "get_review_status", () => {
        if (++attempts === 1) throw new Error("[QA review] Temporary status failure");
        return [status("reviewed", 1)];
    });
    await mountView({ resModel: "account.move.line", type: "list", groupBy: ["account_id"],
        arch: '<list js_class="usl_reconciliation_review"><field name="account_id"/><field name="amount_residual" sum="Remaining"/></list>' });
    await waitFor(".o_reconciliation_review_mark .text-danger");
    expect(".o_reconciliation_review_mark .text-success").toHaveCount(0);
    await contains(".o_reconciliation_review_mark").click();
    await waitFor(".o_reconciliation_review_mark .text-success");
    expect(attempts).toBe(2);
});

test("the native empty grouped list does not request or display review marks", async () => {
    onRpc("account.reconciliation.review", "get_review_status", () => expect.step("unexpected-status"));
    await mountView({ resModel: "account.move.line", type: "list", domain: [["id", "=", -1]], groupBy: ["account_id"],
        arch: '<list js_class="usl_reconciliation_review"><field name="account_id"/><field name="amount_residual" sum="Remaining"/></list>' });
    expect(".o_reconciliation_review_mark").toHaveCount(0);
    expect.verifySteps([]);
});

test("a stale save reloads current warning without claiming a successful review", async () => {
    let reads = 0;
    onRpc("account.reconciliation.review", "get_review_status", () => {
        reads++;
        return [reads === 1 ? status("reviewed", 1) : { ...status("changed", 1), amounts: [{ currency_id: 1, currency: "EUR", baseline: 100, current: 125, delta: 25 }] }];
    });
    onRpc("account.reconciliation.review", "mark_reviewed", () => {
        throw new Error("[QA review] Remaining changed before save");
    });
    await mountView({ resModel: "account.move.line", type: "list", groupBy: ["account_id"],
        arch: '<list js_class="usl_reconciliation_review"><field name="account_id"/><field name="amount_residual" sum="Remaining"/></list>' });
    await contains(".o_reconciliation_review_mark").click();
    await contains(".o_reconciliation_review_details .btn-primary").click();
    await waitFor(".o_reconciliation_review_mark .fa-exclamation-triangle");
    expect(".o_reconciliation_review_mark .text-success").toHaveCount(0);
    expect(".o_reconciliation_review_details").toHaveCount(0);
    expect(reads).toBe(2);
});
