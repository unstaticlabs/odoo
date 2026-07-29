import { expect, test } from "@odoo/hoot";
import { defineModels, fields, models, mountView } from "@web/../tests/web_test_helpers";

class UslDocument extends models.Model {
    _name = "usl.document";
    name = fields.Char();
    paperless_id = fields.Integer();
    review_state = fields.Selection({
        selection: [
            ["needs_attention", "Needs attention"],
            ["reviewed", "Reviewed"],
        ],
    });
    availability_state = fields.Selection({
        selection: [
            ["available", "Available"],
            ["missing", "Missing"],
        ],
    });
    _records = [];
}

defineModels([UslDocument]);

test("document card states remain explicit", async () => {
    UslDocument._records = [
        {
            id: 1,
            name: "Supplier evidence",
            paperless_id: 42,
            review_state: "needs_attention",
            availability_state: "available",
        },
    ];
    await mountView({
        type: "kanban",
        resModel: "usl.document",
        arch: `
            <kanban>
                <field name="name"/>
                <field name="review_state"/>
                <templates>
                    <t t-name="card">
                        <field name="name"/>
                        <span t-if="record.review_state.raw_value === 'needs_attention'">Needs attention</span>
                    </t>
                </templates>
            </kanban>
        `,
    });
    expect(".o_kanban_record").toHaveCount(1);
    expect(".o_kanban_record").toHaveText(/Supplier evidence/);
    expect(".o_kanban_record").toHaveText(/Needs attention/);
});

