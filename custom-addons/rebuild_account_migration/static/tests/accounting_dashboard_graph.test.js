import { expect, test } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    defineModels,
    fields,
    models,
    mountView,
} from "@web/../tests/web_test_helpers";

import {
    getAccessibleGraphLabel,
    getAccessibleMonthlyValues,
} from "../src/js/accounting_dashboard_graph";
import { updateButtonBoxLayout } from "../src/js/accounting_home_action";

const graphData = [
    {
        key: "Net posted amount — refunds deducted",
        values: [
            {
                label: "avr. 2026",
                type: "past",
                value: 80000,
                formatted_value: "80 000,00 €",
            },
            {
                label: "mai 2026",
                type: "past",
                value: 0,
                formatted_value: "0,00 €",
            },
        ],
    },
];

class Partner extends models.Model {
    graph_data = fields.Text({ string: "Graph Data" });
    graph_type = fields.Selection({
        string: "Graph Type",
        selection: [["bar", "Bar"]],
    });

    _records = [
        {
            id: 1,
            graph_data: JSON.stringify(graphData),
            graph_type: "bar",
        },
    ];
}

defineModels([Partner]);
defineMailModels();

test.tags("desktop");

test("accounting overview keeps all seven concise shortcuts visible", () => {
    const buttonBox = {
        additionalButtons: ["invoices", "bills", "expenses", "alerts"],
        env: { isSmall: false },
        isFull: true,
        isSlotVisible: (slot) => slot.isVisible,
        props: {
            class: "oe_button_box o_usl_overview_shortcuts",
            slots: Object.fromEntries(
                ["review", "bank", "match", "invoices", "bills", "expenses", "alerts"].map(
                    (name) => [name, { isVisible: true }]
                )
            ),
        },
        visibleButtons: ["review", "bank", "match"],
    };

    updateButtonBoxLayout(buttonBox, 4);

    expect(buttonBox.visibleButtons).toEqual([
        "review",
        "bank",
        "match",
        "invoices",
        "bills",
        "expenses",
        "alerts",
    ]);
    expect(buttonBox.additionalButtons).toEqual([]);
    expect(buttonBox.isFull).toBe(false);

    buttonBox.env.isSmall = true;
    updateButtonBoxLayout(buttonBox, 4);

    expect(buttonBox.visibleButtons).toEqual(["review", "bank", "match"]);
    expect(buttonBox.additionalButtons).toEqual([
        "invoices",
        "bills",
        "expenses",
        "alerts",
    ]);
    expect(buttonBox.isFull).toBe(true);
});

test("monthly journal values have an accessible text equivalent", async () => {
    expect(getAccessibleMonthlyValues(graphData)).toHaveLength(2);
    expect(getAccessibleGraphLabel(graphData)).toBe(
        "Net posted amount — refunds deducted"
    );

    await mountView({
        type: "kanban",
        resModel: "partner",
        arch: /* xml */ `
            <kanban>
                <field name="graph_type"/>
                <templates>
                    <t t-name="card">
                        <field name="graph_data"
                               t-att-graph_type="record.graph_type.raw_value"
                               widget="dashboard_graph"/>
                    </t>
                </templates>
            </kanban>`,
    });

    expect(".o_dashboard_graph canvas").toHaveAttribute(
        "aria-label",
        getAccessibleGraphLabel(graphData)
    );
    const descriptionId = document
        .querySelector(".o_dashboard_graph canvas")
        .getAttribute("aria-describedby");
    expect(descriptionId).toMatch(/^o_usl_journal_monthly_values_/);
    expect(`.o_usl_journal_monthly_values#${descriptionId}`).toHaveCount(1);
    expect(".o_usl_journal_monthly_values").toHaveClass("visually-hidden");
    expect(".o_usl_journal_monthly_values li").toHaveCount(2);
    expect(".o_usl_journal_monthly_values li:eq(0)").toHaveText(
        /avr\. 2026.*80 000,00 €/
    );
});
