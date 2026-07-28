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

class AccountMoveLine extends models.Model {
    _name = "account.move.line";

    matching_number = fields.Char();
    rebuild_matching_color = fields.Integer();

    _records = [
        {
            id: 42,
            matching_number: "P123",
            rebuild_matching_color: 4,
        },
    ];
}

defineModels([AccountMoveLine]);
defineMailModels();

test("matching badge opens the journal items sharing its exact code", async () => {
    onRpc("action_rebuild_open_matching_items", ({ args, model }) => {
        expect.step("load matching group");
        expect(model).toBe("account.move.line");
        expect(args).toEqual([[42]]);
        return {
            type: "ir.actions.act_window",
            name: "Matching P123",
            res_model: "account.move.line",
            domain: [["matching_number", "=", "P123"]],
        };
    });
    mockService("action", {
        doAction(action) {
            expect.step("open matching group");
            expect(action.name).toBe("Matching P123");
        },
    });

    await mountView({
        type: "list",
        resModel: "account.move.line",
        arch: `
            <list>
                <field
                    name="matching_number"
                    widget="rebuild_matching_badge"
                    options="{'color_field': 'rebuild_matching_color'}"/>
                <field name="rebuild_matching_color" column_invisible="True"/>
            </list>
        `,
    });

    expect(".o_usl_matching_badge_button").toHaveText("P123");
    expect(".o_usl_matching_badge_button .badge").toHaveClass(
        "o_badge_color_4",
    );
    await contains(".o_usl_matching_badge_button").click();
    expect.verifySteps([
        "load matching group",
        "open matching group",
    ]);
});
