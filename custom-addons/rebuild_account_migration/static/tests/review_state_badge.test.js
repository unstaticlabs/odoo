import { expect, test } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains } from "@web/../tests/web_test_helpers";
import {
    defineModels,
    fields,
    models,
    mountView,
    onRpc,
} from "@web/../tests/web_test_helpers";

class AccountMove extends models.Model {
    _name = "account.move";

    review_state = fields.Selection({
        selection: [
            ["todo", "To Review"],
            ["reviewed", "Reviewed"],
            ["supervised", "Supervised"],
            ["anomaly", "Anomaly"],
        ],
    });

    _records = [
        { id: 1, review_state: false },
        { id: 2, review_state: "reviewed" },
    ];
}

defineModels([AccountMove]);
defineMailModels();
onRpc("has_group", () => true);
onRpc("has_access", () => true);

const reviewField = `
    <field name="review_state"
           widget="account_review_state_selection_badge"
           options="{
               False: {'can_edit': true},
               'todo': {'decoration': 'info'},
               'reviewed': {'decoration': 'success'},
               'supervised': {'decoration': 'success'},
               'anomaly': {'decoration': 'danger'}
           }"/>
`;

test("unset review state explains its purpose on a form", async () => {
    await mountView({
        type: "form",
        resModel: "account.move",
        resId: 1,
        arch: `<form>${reviewField}</form>`,
    });

    const button = ".o_account_review_state_selection_badge_button";
    expect(button).toHaveText("Set review status");
    expect(button).toHaveClass("o_account_review_state_selection_badge_unset");
    expect(button).toHaveAttribute("aria-label", "Review status: not set");
    expect(button).toHaveAttribute(
        "title",
        "No review status is set. Select to mark this entry for review or record its review outcome."
    );

    await contains(button).click();
    expect(".o_account_review_state_selection_badge_dropdown_item:eq(0)").toHaveText(
        "Clear review status"
    );
    expect(".o_account_review_state_selection_badge_dropdown_item:eq(1)").toHaveText("To Review");
});

test("list view labels unset and completed review states", async () => {
    await mountView({
        type: "list",
        resModel: "account.move",
        arch: `<list>${reviewField}</list>`,
    });

    expect(".o_account_review_state_selection_badge_button").toHaveCount(2);
    expect(
        ".o_account_review_state_selection_badge_button:eq(0)"
    ).toHaveText("No review status");
    expect(
        ".o_account_review_state_selection_badge_button:eq(1)"
    ).toHaveText("Reviewed");
});
