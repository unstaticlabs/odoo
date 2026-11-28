import { expect, test } from "@odoo/hoot";
import { contains, defineModels, fields, models, mountView, onRpc, patchWithCleanup, serverState, webModels } from "@web/../tests/web_test_helpers";
import { defineAnalyticModels } from "@analytic/../tests/analytic_test_helpers";
import { cookie } from "@web/core/browser/cookie";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { formView } from "@web/views/form/form_view";

defineAnalyticModels();

class TransferLine extends models.Model {
    _name = "usl.bill.company.transfer.line";
    company_id = fields.Many2one({ relation: "res.company" });
    scope_company_ids = fields.Json();
    analytic_distribution = fields.Json();
    analytic_precision = fields.Integer();
    _records = [{ id: 1, company_id: 2, scope_company_ids: [2, 1], analytic_distribution: { 11: 100 }, analytic_precision: 2 }];
}
class TransferWizard extends models.Model {
    _name = "usl.bill.company.transfer";
    target_company_id = fields.Many2one({ relation: "res.company" });
    operator_company_ids = fields.Json();
    _records = [{ id: 1, operator_company_ids: [1, 2] }];
}

defineModels([TransferLine, TransferWizard]);

test.tags("mobile");
test("the native destination dialog lists an authorized inactive company", async () => {
    serverState.companies = [{ id: 1, name: "Blue Studio" }, { id: 2, name: "Amber Studio" }];
    webModels.ResCompany._views = {
        kanban: '<kanban><templates><t t-name="card"><field name="display_name"/></t></templates></kanban>',
    };
    cookie.set("cids", "1");
    onRpc("res.company", "web_search_read", ({ kwargs }) => {
        expect(kwargs.context.allowed_company_ids).toEqual([1, 2]);
        expect(kwargs.domain).toInclude(["id", "!=", 1]);
        return { length: 1, records: [{ id: 2, display_name: "Amber Studio" }] };
    });
    await mountView({
        type: "form", resModel: "usl.bill.company.transfer", resId: 1,
        arch: `<form><field name="operator_company_ids" invisible="1"/>
            <field name="target_company_id" widget="usl_bill_transfer_company"
                context="{'allowed_company_ids': operator_company_ids}" domain="[('id', '!=', 1)]"/>
            </form>`,
    });
    await contains("[name='target_company_id'] input").click();
    await contains(".o_dialog .o_kanban_record").click();
    expect("[name='target_company_id'] input").toHaveValue("Amber Studio");
    expect(user.context.allowed_company_ids).toEqual([1]);
});

test.tags("desktop");
test("an open line survives identical search props; changed filters and explicit reload still apply", async () => {
    const loads = [];
    patchWithCleanup(formView.Model.prototype, { async load(params) { loads.push(params); } });
    const Model = registry.category("views").get("usl_bill_company_transfer").Model;
    const model = Object.create(Model.prototype);
    const line = { isInEdition: false };
    Object.defineProperty(model, "root", { value: { data: { mapping_ids: { records: [line] } } } });
    const search = { context: { allowed_company_ids: [1] }, domain: [], groupBy: [], orderBy: [] };
    await model.load(search);
    line.isInEdition = true;
    await model.load({ ...search });
    expect(loads).toHaveLength(1);
    await model.load({ ...search, domain: [["id", "=", 2]] });
    await model.load();
    line.isInEdition = false;
    await model.load(search);
    expect(loads).toHaveLength(4);
});

for (const type of ["form", "list"]) {
    test.tags("desktop");
    test(`transfer ${type} resolves the source label and searches destination accounts with only the source active`, async () => {
        serverState.companies = [{ id: 1, name: "Blue Studio" }, { id: 2, name: "Amber Studio" }];
        cookie.set("cids", "1");
        const scope = [2, 1];
        const accounts = [
            { id: 11, name: "Blue recording", company: 1 },
            { id: 12, name: "Amber recording", company: 2 },
            { id: 13, name: "Shared recording", company: false },
            { id: 14, name: "Green recording", company: 3 },
        ];
        onRpc("account.analytic.plan", "get_relevant_plans", ({ kwargs }) => {
            expect(kwargs.context.allowed_company_ids).toEqual(scope);
            expect(kwargs.company_id).toBe(2);
            return [{ id: 1, name: "Recording", color: 1, applicability: "optional", all_account_count: 3, column_name: "account_id" }];
        });
        onRpc("account.analytic.account", "read", ({ args, kwargs }) => {
            expect(kwargs.context.allowed_company_ids).toEqual(scope);
            return accounts.filter((a) => args[0].includes(a.id)).map((a) => ({
                id: a.id, display_name: a.name, root_plan_id: [1, "Recording"], color: 1,
            }));
        });
        onRpc("account.analytic.account", "web_name_search", ({ kwargs }) => {
            expect(kwargs.context.allowed_company_ids).toEqual(scope);
            expect(kwargs.domain).toInclude(["root_plan_id", "=", 1]);
            expect(kwargs.domain).toInclude(["company_id", "parent_of", 2]);
            expect(kwargs.domain).toInclude(["company_id", "=", false]);
            return accounts.filter((a) => !a.company || a.company === 2).map((a) => ({
                id: a.id, display_name: a.name, __formatted_display_name: a.name,
            }));
        });
        const arch = `<field name="company_id" invisible="1"/><field name="scope_company_ids" invisible="1"/>\n            <field name="analytic_distribution" widget="usl_bill_transfer_analytic"/>`;
        await mountView({ type, resModel: "usl.bill.company.transfer.line", resId: 1,
            arch: type === "form" ? `<form>${arch}</form>` : `<list editable="bottom">${arch}</list>` });
        expect(user.context.allowed_company_ids).toEqual([1]);
        expect(".o_tag_badge_text").toHaveText(/Blue recording/);
        if (type === "list") {
            await contains(".o_data_row .o_field_analytic_distribution").click();
        }
        await contains(".o_field_analytic_distribution .o_input_dropdown").click();
        await contains(".analytic_distribution_popup .o_field_x2many_list_row_add").click();
        await contains(".analytic_distribution_popup tr[name='line_1'] input#account_id").click();
        expect(".o-autocomplete--dropdown-menu").toHaveText(/Amber recording/);
        expect(".o-autocomplete--dropdown-menu").toHaveText(/Shared recording/);
        expect(".o-autocomplete--dropdown-menu").not.toHaveText(/Blue recording|Green recording/);
        expect(user.context.allowed_company_ids).toEqual([1]);
    });
}
