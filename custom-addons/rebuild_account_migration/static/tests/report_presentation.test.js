import { expect, getFixture, test } from "@odoo/hoot";

import { AccountingReportAction } from "../src/js/accounting_report_action";

const { DateTime } = luxon;

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

test("display unit scales company-currency amounts and labels dynamically", () => {
    const report = reportWith([]);
    report.state.data.locale = "fr-FR";
    report.state.data.currency = { name: "CHF", symbol: "CHF" };
    report.state.data.display_unit = {
        key: "thousands",
        factor: 1000,
        short_label: "kCHF",
    };
    report.state.data.amount_rounding = {
        key: "cents",
        decimal_places: 2,
        label: "Au centime",
    };

    expect(report.displayUnitFactor).toBe(1000);
    expect(report.displayUnitLabel).toBe("kCHF");
    expect(report.formatAmount(123456.78)).toBe("123,46");
    expect(
        report.filterOptionLabel("display_unit", {
            value: "millions",
            label: "Millions",
        }),
    ).toBe("Millions (MCHF)");
    expect(
        report.summaryCardLabel({
            label: "Résultat net de l’exercice",
            type: "currency",
        }),
    ).toBe("Résultat net de l’exercice (kCHF)");
});

test("whole-euro presentation rounds consistently without changing scale", () => {
    const report = reportWith([]);
    report.state.data.locale = "fr-FR";
    report.state.data.display_unit = {
        key: "units",
        factor: 1,
        short_label: "€",
    };
    report.state.data.amount_rounding = {
        key: "whole",
        decimal_places: 0,
        label: "À l’euro",
    };

    expect(report.formatAmount(125.5)).toBe("126");
    expect(report.formatAmount(-125.5)).toBe("-126");
});

test("checkbox filters send booleans to the shared report state", async () => {
    const report = reportWith([]);
    let changes;
    report.load = async (nextChanges) => {
        changes = nextChanges;
    };

    await report.onFilterChange({
        target: {
            name: "hide_zero_accounts",
            type: "checkbox",
            checked: true,
        },
    });

    expect(changes).toEqual({ hide_zero_accounts: true });
});

test("export refreshes transient line identifiers before further navigation", () => {
    const report = reportWith([{ id: 10, label: "Ancienne ligne" }]);
    report.state.filters = { hide_zero_accounts: false };
    let actionState;
    report.props = {
        updateActionState: (state) => {
            actionState = state;
        },
    };

    report.applyExportReportPayload({
        wizard_id: 42,
        filters: { hide_zero_accounts: true },
        lines: [{ id: 11, label: "Ligne exportée" }],
    });

    expect(report.state.data.lines[0].id).toBe(11);
    expect(report.state.filters).toEqual({ hide_zero_accounts: true });
    expect(actionState).toEqual({ resId: 42 });
});

test("report date filters serialize day-first Odoo dates", async () => {
    const report = reportWith([]);
    report.state.data.locale = "fr-FR";
    report.state.filters = {
        date_from: "2026-07-28",
        date_to: "2026-08-09",
    };
    let changes;
    report.load = async (nextChanges) => {
        changes = nextChanges;
    };

    expect(report.periodLabel).toBe("28/07/2026 — 09/08/2026");
    expect(report.dateFilterValue("date_from").toFormat("dd/MM/yyyy")).toBe(
        "28/07/2026",
    );
    await report.onDateFilterChange(
        "date_from",
        DateTime.fromISO("2026-08-09"),
    );

    expect(changes).toEqual({
        date_from: "2026-08-09",
        period_preset: "custom",
    });
});

test("report workspace and document theme stay presentation-driven", () => {
    const report = reportWith([]);
    report.reportType = "profit_loss";
    report.state.data.document = {
        primary_color: "#111111",
        section_background_color: "#E9ECEF",
        section_text_color: "#111111",
        muted_color: "#666666",
    };

    expect(report.reportWorkspaceClass).toBe(
        "o_usl_report_workspace o_usl_report_workspace_portrait",
    );
    expect(report.documentThemeStyle).toInclude(
        "--usl-report-section-bg:#E9ECEF",
    );
    expect(report.documentThemeStyle).toInclude(
        "--usl-report-section-text:#111111",
    );

    report.reportType = "general_ledger";
    expect(report.reportWorkspaceClass).toBe(
        "o_usl_report_workspace o_usl_report_workspace_landscape",
    );
});

test.tags("desktop");
test("report action owns vertical scrolling without clipping the paper", () => {
    const fixture = getFixture();
    fixture.innerHTML = `
        <div class="o_web_client" style="height: 240px">
            <div class="o_action_manager" style="height: 240px">
                <div class="o_action o_usl_accounting_report"
                     style="display: flex; height: 240px">
                    <div class="o_usl_report_workspace">
                        <div style="height: 800px">Long accounting statement</div>
                    </div>
                </div>
            </div>
        </div>
    `;
    const action = fixture.querySelector(".o_usl_accounting_report");
    const workspace = fixture.querySelector(".o_usl_report_workspace");

    expect(getComputedStyle(workspace).flexShrink).toBe("0");
    expect(action.scrollHeight).toBeGreaterThan(action.clientHeight);
    action.scrollTop = 120;
    expect(action.scrollTop).toBe(120);
});
