import { expect, test } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { clickSave, contains, defineModels, fields, models, mountView, onRpc } from "@web/../tests/web_test_helpers";
import "../src/task_github_properties";

const legacyUrl = "https://github.com/example/demo/issues/42";

class ReferenceProject extends models.Model {
    _name = "reference.project";
    name = fields.Char();
    definitions = fields.PropertiesDefinition();
    _records = [{ id: 1, name: "Synthetic plan", definitions: [
        { name: "qa_github", type: "char", string: "GitHub", view_in_cards: true },
        { name: "qa_sources", type: "char", string: "Sources" },
    ] }];
}

class ReferenceTask extends models.Model {
    _name = "reference.task";
    name = fields.Char();
    github_url = fields.Char();
    project_id = fields.Many2one({ relation: "reference.project" });
    properties = fields.Properties({ definition_record: "project_id", definition_record_field: "definitions" });
    _records = [{ id: 1, name: "Synthetic task", project_id: 1, github_url: legacyUrl,
        properties: { qa_github: legacyUrl, qa_sources: "Original source" } }];
}

defineMailModels();
defineModels([ReferenceProject, ReferenceTask]);

async function mountTaskForm() {
    return mountView({ type: "form", resModel: "reference.task", resId: 1,
        arch: `<form><sheet><group><field name="name"/><field name="github_url" widget="url"/>
            <field name="project_id"/></group><field name="properties" widget="usl_task_properties"/></sheet></form>` });
}

test("matching legacy is hidden without losing its value when another property is edited", async () => {
    onRpc("reference.task", "web_save", ({ args }) => {
        const values = args[1].properties;
        expect(values.find((p) => p.name === "qa_github").value).toBe(legacyUrl);
        expect(values.find((p) => p.name === "qa_sources").value).toBe("Updated source");
        expect.step("full properties retained");
    });
    await mountTaskForm();
    expect(".o_field_widget[name='properties']").toHaveClass("o_field_properties");
    expect("[property-name='qa_github']").toHaveCount(0);
    expect(".o_field_widget[name='github_url'] a").toHaveAttribute("href", legacyUrl);
    await contains("[property-name='qa_sources'] input").edit("Updated source");
    await clickSave();
    expect.verifySteps(["full properties retained"]);
});

test("conflicting legacy remains visible readonly while canonical is editable", async () => {
    ReferenceTask._records[0].github_url = "https://github.com/example/demo/pull/43";
    await mountTaskForm();
    expect("[property-name='qa_github']").toHaveText(/Legacy GitHub/);
    expect("[property-name='qa_github']").toHaveText(new RegExp(legacyUrl));
    expect("[property-name='qa_github'] input").toHaveCount(0);
    expect(".o_field_widget[name='github_url'] input").toHaveCount(1);
    expect("[property-name='qa_sources'] input").toHaveCount(1);
});

test("unmigrated and invalid matching values are retained readonly", async () => {
    ReferenceTask._records[0].github_url = "Not a URL";
    ReferenceTask._records[0].properties.qa_github = "Not a URL";
    await mountTaskForm();
    expect("[property-name='qa_github']").toHaveText(/Legacy GitHub/);
    expect("[property-name='qa_github'] input").toHaveCount(0);
    await contains(".o_field_widget[name='github_url'] input").edit("");
    expect("[property-name='qa_github']").toHaveCount(1);
});

test("Kanban keeps a compact native link and omits only the verified legacy duplicate", async () => {
    await mountView({ type: "kanban", resModel: "reference.task",
        arch: `<kanban><templates><t t-name="card"><field name="name"/>
            <field name="github_url" widget="url" text="GitHub"/>
            <field name="project_id" invisible="1"/>
            <field name="properties" widget="usl_task_properties"/></t></templates></kanban>` });
    expect(".o_kanban_record .o_field_widget[name='github_url'] a").toHaveText("GitHub");
    expect(".o_kanban_record .o_field_widget[name='github_url'] a").toHaveAttribute("href", legacyUrl);
    expect(".o_card_property_field").toHaveCount(0);
});
