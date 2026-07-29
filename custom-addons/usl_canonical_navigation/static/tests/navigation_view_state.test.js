import { animationFrame, expect, test } from "@odoo/hoot";
import {
    defineModels,
    fields,
    mockService,
    models,
    mountView,
    webModels,
} from "@web/../tests/web_test_helpers";
import { router, startRouter } from "@web/core/browser/router";
import { redirect } from "@web/core/utils/urls";

import "../src/view_state";

class NavigationPartner extends models.Model {
    _name = "navigation.partner";

    name = fields.Char();
    email = fields.Char();
    phone = fields.Char();

    _records = [
        { id: 1, name: "Navigation One", email: "one@example.test", phone: "01" },
        { id: 2, name: "Navigation Two", email: "two@example.test", phone: "02" },
        { id: 3, name: "Navigation Three", email: "three@example.test", phone: "03" },
        { id: 4, name: "Navigation Four", email: "four@example.test", phone: "04" },
    ];
}

class NavigationWorkspace extends models.Model {
    _name = "usl.navigation.workspace";

    validate_state() {
        return { status: "ok" };
    }

    create_workspace() {
        return {
            public_id: "00000000-0000-0000-0000-000000000001",
            url: "/odoo/usl-workspace?ws=00000000-0000-0000-0000-000000000001",
        };
    }
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([
    NavigationPartner,
    NavigationWorkspace,
    ResCompany,
    ResPartner,
    ResUsers,
]);

test("canonical list URL restores page, selection, and optional columns", async () => {
    const domain = encodeURIComponent('[["name","ilike","Navigation"]]');
    redirect(
        `/odoo/navigation-partners?nv=1&cids=1&domain=${domain}` +
            "&columns=email&offset=2&limit=2&selection=3%2C4"
    );
    mockService("canonical_navigation", {
        blocked: false,
        async ensurePortable() {},
    });
    startRouter();

    await mountView({
        resModel: "navigation.partner",
        type: "list",
        arch: `
            <list>
                <field name="name"/>
                <field name="email" optional="show"/>
                <field name="phone" optional="show"/>
            </list>
        `,
        searchViewArch: "<search/>",
    });
    await animationFrame();

    expect(".o_data_row").toHaveCount(2);
    expect(".o_data_row:eq(0)").toHaveText(/Navigation Three/);
    expect(".o_data_row:eq(1)").toHaveText(/Navigation Four/);
    expect(".o_data_row .o_list_record_selector input:checked").toHaveCount(2);
    expect("th[data-name='email']").toHaveCount(1);
    expect("th[data-name='phone']").toHaveCount(0);
    expect(router.current.offset).toBe(2);
    expect(router.current.selection).toBe("3,4");
});
