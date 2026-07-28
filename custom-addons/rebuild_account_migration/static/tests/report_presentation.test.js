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

test("display unit scales company-currency amounts and labels dynamically", () => {
    const report = reportWith([]);
    report.state.data.locale = "fr-FR";
    report.state.data.currency = { name: "CHF", symbol: "CHF" };
    report.state.data.display_unit = {
        key: "thousands",
        factor: 1000,
        short_label: "kCHF",
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
