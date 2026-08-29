import { mailModels } from "@mail/../tests/mail_test_helpers";
import { expect, test } from "@odoo/hoot";
import { hover, queryOne } from "@odoo/hoot-dom";
import { runAllTimers } from "@odoo/hoot-mock";
import {
    contains,
    defineModels,
    fields,
    models,
    mountView,
} from "@web/../tests/web_test_helpers";

import "../src/bank_record_preview";

class AccountBankStatementLine extends models.Model {
    _name = "account.bank.statement.line";

    display_name = fields.Char();

    _records = [
        {
            id: 42,
            display_name: "BNK1/2026/0042",
        },
    ];
}

class PlatformPayout extends models.Model {
    _name = "usl.platform.billing.payout";

    name = fields.Char();
    bank_statement_line_ids = fields.Many2many({
        relation: "account.bank.statement.line",
    });
    bank_transaction_preview = fields.Json();

    _records = [
        {
            id: 7,
            name: "CreatorHub — JULY-001",
            bank_statement_line_ids: [42],
            bank_transaction_preview: [
                {
                    id: 42,
                    display_name: "BNK1/2026/0042",
                    date: "20/07/2026",
                    journal: "Bank",
                    label: "CreatorHub payout JULY-001",
                    partner: "CreatorHub",
                    amount: "80.00 €",
                    reconciled: false,
                },
            ],
        },
    ];
}

class BankCandidate extends models.Model {
    _name = "usl.platform.billing.bank.import.wizard.line";

    selected = fields.Boolean();

    _records = Array.from({ length: 24 }, (_, index) => ({
        id: index + 1,
        selected: false,
    }));
}

defineModels({
    ...mailModels,
    AccountBankStatementLine,
    PlatformPayout,
    BankCandidate,
});

test("hovering a payout row previews its linked bank record", async () => {
    await mountView({
        type: "list",
        resModel: "usl.platform.billing.payout",
        arch: `
            <list>
                <field name="name"/>
                <field name="bank_statement_line_ids"
                       widget="platform_billing_bank_record_preview"/>
            </list>
        `,
    });

    expect(".o_data_row").toHaveAttribute(
        "data-tooltip-template",
        "usl_platform_billing.BankRecordPreviewTooltip"
    );
    await hover(".o_data_row");
    await runAllTimers();

    expect(".o-tooltip").toHaveText(/Linked bank transaction/);
    expect(".o-tooltip").toHaveText(/BNK1\/2026\/0042/);
    expect(".o-tooltip").toHaveText(/CreatorHub payout JULY-001/);
    expect(".o-tooltip").toHaveText(/80.00 €/);
    expect(".o-tooltip").toHaveText(/Open/);
});

test("bank candidate selection updates in place", async () => {
    await mountView({
        type: "list",
        resModel: "usl.platform.billing.bank.import.wizard.line",
        arch: `
            <list editable="bottom">
                <field name="selected" widget="boolean_icon"
                       options="{'icon': 'fa-check'}"/>
            </list>
        `,
    });
    const lastRow = queryOne(".o_data_row:last-child");

    await contains(".o_data_row:last-child [name='selected'] button").click();

    expect(queryOne(".o_data_row:last-child")).toBe(lastRow);
    expect(".o_data_row:last-child [name='selected'] button").toHaveClass(
        "btn-primary"
    );
});
