import { expect, test } from "@odoo/hoot";
import {
    contains,
    defineModels,
    fields,
    models,
    mountView,
} from "@web/../tests/web_test_helpers";

import "../src/js/company_color_field";

class Company extends models.Model {
    _name = "res.company";

    usl_ui_theme_color = fields.Char();
    usl_resolved_ui_theme_color = fields.Char();

    _records = [
        {
            id: 1,
            usl_ui_theme_color: false,
            usl_resolved_ui_theme_color: "#2F6F8F",
        },
    ];
}

defineModels([Company]);

test("automatic company colors display their resolved swatch and remain editable", async () => {
    await mountView({
        type: "form",
        resModel: "res.company",
        resId: 1,
        arch: /* xml */ `
            <form>
                <field name="usl_ui_theme_color" widget="usl_company_color"/>
            </form>`,
    });

    expect(".o_field_color").toHaveStyle({ backgroundColor: "rgb(47, 111, 143)" });
    expect(".o_field_color input").toHaveValue("#2f6f8f");

    await contains(".o_field_color input", { visible: false }).edit("#abcdef");

    expect(".o_field_color").toHaveStyle({ backgroundColor: "rgb(171, 205, 239)" });
    expect(".o_field_color input").toHaveValue("#abcdef");
});
