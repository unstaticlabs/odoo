import { beforeEach, describe, expect, test } from "@odoo/hoot";
import { queryAllAttributes, queryAllTexts } from "@odoo/hoot-dom";
import { clearRegistry, contains, mockService, mountWithCleanup, serverState } from "@web/../tests/web_test_helpers";

import { registry } from "@web/core/registry";
import { UserMenu } from "@web/webclient/user_menu/user_menu";

import "@usl_docs/js/help_menu";

const userMenuRegistry = registry.category("user_menuitems");

describe.current.tags("desktop");

let helpItem;

beforeEach(() => {
    serverState.partnerName = "Reader";
    helpItem = userMenuRegistry.get("support");
    clearRegistry(userMenuRegistry);
    userMenuRegistry.add("support", helpItem);
});

async function helpHref(currentController) {
    mockService("action", { currentController });
    await mountWithCleanup(UserMenu);
    await contains("button.dropdown-toggle").click();
    const items = queryAllTexts(".dropdown-menu .dropdown-item");
    expect(items).toEqual(["Help"]);
    return queryAllAttributes(".dropdown-menu .dropdown-item", "href")[0];
}

test("Help opens the guide on the current screen", async () => {
    const href = await helpHref({
        action: { id: 42, path: "expenses", xml_id: "hr_expense.action_x", res_model: "hr.expense" },
        props: { resModel: "hr.expense" },
    });
    expect(href).toBe("/usl/user-docs?action=expenses&xmlid=hr_expense.action_x&model=hr.expense");
});

test("Help falls back to the action's XML id", async () => {
    expect(await helpHref({ action: { id: 42, xml_id: "hr_expense.action_x" }, props: {} })).toBe(
        "/usl/user-docs?action=hr_expense.action_x"
    );
});

test("Help falls back to the action's database id", async () => {
    expect(await helpHref({ action: { id: 42 }, props: {} })).toBe("/usl/user-docs?action=42");
});

test("Help opens the guide's home without a current action", async () => {
    expect(await helpHref(null)).toBe("/usl/user-docs");
});
