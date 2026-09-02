import { beforeEach, expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    contains,
    defineModels,
    fields,
    models,
    mountView,
    onRpc,
} from "@web/../tests/web_test_helpers";

class ResGroups extends models.Model {
    _name = "res.groups";

    name = fields.Char();

    _records = [
        { id: 2, name: "User" },
        { id: 3, name: "Administrator" },
        { id: 4, name: "Project User" },
        { id: 5, name: "Project Administrator" },
    ];
}

function makeAgentRecords() {
    return [
        {
            id: 1,
            name: "QA Agent",
            delegated_group_ids: [3, 5],
            read_only_group_ids: [3, 5],
            view_group_hierarchy: {
                categories: [
                    { id: 1, name: "Sales", privilege_ids: [10] },
                    { id: 2, name: "Services", privilege_ids: [11] },
                ],
                privileges: {
                    10: { id: 10, name: "Sales", group_ids: [2, 3] },
                    11: { id: 11, name: "Project", group_ids: [4, 5] },
                },
                groups: {
                    2: { id: 2, name: "User" },
                    3: { id: 3, name: "Administrator" },
                    4: { id: 4, name: "User" },
                    5: { id: 5, name: "Administrator" },
                },
            },
        },
    ];
}

class UslAgent extends models.Model {
    _name = "usl.agent";

    name = fields.Char();
    delegated_group_ids = fields.Many2many({ relation: "res.groups" });
    read_only_group_ids = fields.Many2many({ relation: "res.groups" });
    view_group_hierarchy = fields.Json();

    _records = makeAgentRecords();
}

defineMailModels();
defineModels([ResGroups, UslAgent]);

beforeEach(() => {
    UslAgent._records = makeAgentRecords();
});

const arch = `
    <form>
        <sheet>
            <field name="view_group_hierarchy" invisible="1"/>
            <field name="read_only_group_ids" invisible="1"/>
            <field name="delegated_group_ids" widget="usl_agent_access"/>
        </sheet>
    </form>`;

test("bulk shortcuts prefill without saving and rows remain editable", async () => {
    let writeCount = 0;
    onRpc("usl.agent", "write", () => writeCount++);
    await mountView({ type: "form", resModel: "usl.agent", resId: 1, arch });

    expect("select").toHaveCount(2);
    expect("#usl_agent_access_10").toHaveValue("read:3");
    expect("#usl_agent_access_11").toHaveValue("read:5");

    await contains("button", { text: "Set all to read-only" }).click();
    await animationFrame();
    expect("#usl_agent_access_10").toHaveValue("read:3");

    await contains("button", { text: "Set all to highest access" }).click();
    await animationFrame();
    expect(writeCount).toBe(0);
    expect("#usl_agent_access_10").toHaveValue("write:3");

    await contains("#usl_agent_access_10").select("none");
    await animationFrame();
    expect(writeCount).toBe(0);
    expect("#usl_agent_access_10").toHaveValue("none");

    await contains(".o_form_button_cancel").click();
    await animationFrame();
    expect("#usl_agent_access_10").toHaveValue("read:3");
});

test("read-only shortcut updates every application without a reload", async () => {
    await mountView({ type: "form", resModel: "usl.agent", resId: 1, arch });

    await contains("button", { text: "Set all to highest access" }).click();
    await contains("button", { text: "Set all to read-only" }).click();
    await animationFrame();

    expect("select").toHaveCount(2);
    expect("#usl_agent_access_10").toHaveValue("read:3");
    expect("#usl_agent_access_11").toHaveValue("read:5");
    expect(".o_usl_agent_access").toHaveText(/Irreversible actions are always blocked/);
});

test("batch changes use normal save and discard semantics", async () => {
    let writeCount = 0;
    onRpc("usl.agent", "web_save", () => {
        writeCount++;
    });
    await mountView({ type: "form", resModel: "usl.agent", resId: 1, arch });

    await contains("button", { text: "Set all to highest access" }).click();
    await animationFrame();
    expect("#usl_agent_access_10").toHaveValue("write:3");
    expect(".o_form_button_save").toHaveCount(1);
    expect(writeCount).toBe(0);

    await contains(".o_form_button_cancel").click();
    await animationFrame();
    expect("#usl_agent_access_10").toHaveValue("read:3");
    expect(writeCount).toBe(0);

    await contains("button", { text: "Set all to highest access" }).click();
    await contains(".o_form_button_save").click();
    expect(writeCount).toBe(1);
});
