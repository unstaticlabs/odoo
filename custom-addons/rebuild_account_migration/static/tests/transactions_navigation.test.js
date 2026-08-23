import { expect, test } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
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

    name = fields.Char();

    _records = [
        {
            id: 4773,
            name: "BILL/25-26/07/0003",
        },
    ];
}

class AccountBankStatementLine extends models.Model {
    _name = "account.bank.statement.line";

    payment_ref = fields.Char();
    rebuild_linked_move_id = fields.Many2one({ relation: "account.move" });

    _records = [
        {
            id: 3046,
            payment_ref: "IWG FRANCE MANAGEMENT",
            rebuild_linked_move_id: 4773,
        },
    ];
}

defineMailModels();
defineModels([AccountMove, AccountBankStatementLine]);

test("Transactions linked document opens the related account move", async () => {
    onRpc("get_formview_action", ({ args, model }) => {
        expect.step(`${model}.get_formview_action`);
        expect(model).toBe("account.move");
        expect(args).toEqual([[4773]]);
        return {
            type: "ir.actions.act_window",
            res_model: "account.move",
            res_id: 4773,
            views: [[false, "form"]],
            target: "current",
        };
    });
    mockService("action", {
        doAction(action) {
            expect.step("open account.move");
            expect(action.res_model).toBe("account.move");
            expect(action.res_id).toBe(4773);
        },
    });

    await mountView({
        type: "list",
        resModel: "account.bank.statement.line",
        arch: `
            <list create="0" edit="0" delete="0">
                <field name="payment_ref"/>
                <field name="rebuild_linked_move_id" widget="many2one"/>
            </list>
        `,
    });

    expect("a.o_form_uri").toHaveCount(1);
    expect("a.o_form_uri").toHaveAttribute("href", "/odoo/account.move/4773");
    await contains("a.o_form_uri").click();
    expect.verifySteps([
        "account.move.get_formview_action",
        "open account.move",
    ]);
});
