import { expect, test } from "@odoo/hoot";

import { AccountingReportAction } from "../src/js/accounting_report_action";

function reportWith(lines) {
    const report = Object.create(AccountingReportAction.prototype);
    report.state = { data: { lines } };
    return report;
}

test("ledger hides empty optional evidence columns", () => {
    const report = reportWith([
        { currency: "", matching_number: "" },
        { currency: false, matching_number: false },
    ]);

    expect(report.showForeignCurrency).toBe(false);
    expect(report.showMatching).toBe(false);
});

test("ledger reveals foreign currency and matching evidence when present", () => {
    const report = reportWith([
        { currency: "USD", matching_number: "" },
        { currency: "", matching_number: "P123" },
    ]);

    expect(report.showForeignCurrency).toBe(true);
    expect(report.showMatching).toBe(true);
});

test("matching evidence opens its exact company-scoped journal items", async () => {
    const report = reportWith([]);
    report.state.data.company_id = 7;
    report.orm = {
        async call(model, method, args) {
            expect.step("load matching group");
            expect(model).toBe("account.move.line");
            expect(method).toBe("action_rebuild_open_matching_number");
            expect(args).toEqual(["P123", 7]);
            return {
                type: "ir.actions.act_window",
                name: "Matching P123",
            };
        },
    };
    report.actionService = {
        async doAction(action) {
            expect.step("open matching group");
            expect(action.name).toBe("Matching P123");
        },
    };

    await report.openMatchingItems({ matching_number: "P123" });

    expect.verifySteps([
        "load matching group",
        "open matching group",
    ]);
});
