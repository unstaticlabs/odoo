import {animationFrame, click, expect, test} from "@odoo/hoot";
import {defineMailModels} from "@mail/../tests/mail_test_helpers";
import {defineModels, fields, models, mountView} from "@web/../tests/web_test_helpers";

class ReconcilePresentationLine extends models.Model {
    _name = "rebuild.reconcile.presentation.line";

    reconcile_data_info = fields.Json();
    currency_id = fields.Many2one({relation: "res.currency"});
    company_currency_id = fields.Many2one({relation: "res.currency"});
    foreign_currency_id = fields.Many2one({relation: "res.currency"});
    manual_reference = fields.Char();
    manual_delete = fields.Boolean();

    _records = [
        {
            id: 1,
            reconcile_data_info: {
                data: [
                    {
                        reference: "reconcile_auxiliary;1",
                        id: false,
                        account_id: [1, "512001 Banque"],
                        partner_id: false,
                        date: "2026-07-28",
                        name: "Bank line",
                        amount: 100,
                        debit: 100,
                        credit: 0,
                        kind: "liquidity",
                        currency_id: 1,
                        line_currency_id: 1,
                        currency_amount: 100,
                    },
                    {
                        reference: "reconcile_auxiliary;2",
                        id: false,
                        account_id: [2, "471000 Suspense"],
                        partner_id: false,
                        date: "2026-07-28",
                        name: "Counterpart",
                        amount: -100,
                        debit: 0,
                        credit: 100,
                        kind: "other",
                        currency_id: 1,
                        line_currency_id: 1,
                        currency_amount: -100,
                    },
                ],
            },
            currency_id: 1,
            company_currency_id: 1,
        },
    ];
}

class Currency extends models.Model {
    _name = "res.currency";

    name = fields.Char();
    symbol = fields.Char();
    position = fields.Selection({
        selection: [
            ["after", "After"],
            ["before", "Before"],
        ],
    });
    inverse_rate = fields.Float();

    _records = [
        {
            id: 1,
            name: "EUR",
            symbol: "€",
            position: "after",
            inverse_rate: 1,
        },
    ];
}

defineModels([Currency, ReconcilePresentationLine]);
defineMailModels();

test("transaction presentation reuses OCA lines without reconciliation mutations", async () => {
    await mountView({
        type: "form",
        resId: 1,
        resIds: [1],
        resModel: "rebuild.reconcile.presentation.line",
        arch: `
            <form>
                <field
                    name="reconcile_data_info"
                    widget="rebuild_reconcile_data_presentation"
                    readonly="1"
                />
                <field name="currency_id" invisible="1"/>
                <field name="company_currency_id" invisible="1"/>
                <field name="foreign_currency_id" invisible="1"/>
                <field name="manual_reference"/>
                <field name="manual_delete"/>
            </form>
        `,
    });

    expect("[name='reconcile_data_info'] .o_reconcile_widget_line").toHaveCount(2);
    expect(
        "[name='reconcile_data_info'] .o_reconcile_widget_line:first-child"
    ).toHaveText(/512001 Banque/);
    expect(
        "[name='reconcile_data_info'] .o_reconcile_widget_line:last-child"
    ).toHaveText(/471000 Suspense/);
    expect("[name='reconcile_data_info'] .fa-trash-o").toHaveCount(0);
    expect("[name='reconcile_data_info'] thead th").toHaveCount(6);

    expect("[name='manual_reference'] input").toHaveValue("");
    await click("[name='reconcile_data_info'] .o_reconcile_widget_line:last-child");
    await animationFrame();
    expect("[name='manual_reference'] input").toHaveValue("");
    expect("[name='manual_delete'] input").not.toBeChecked();
});
