import { beforeEach, expect, test } from "@odoo/hoot";
import { queryAll, queryOne } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { mountWithCleanup, serverState } from "@web/../tests/web_test_helpers";

import { cookie } from "@web/core/browser/cookie";
import { MobileSwitchCompanyMenu } from "@web/webclient/burger_menu/mobile_switch_company_menu/mobile_switch_company_menu";
import { SwitchCompanyMenu } from "@web/webclient/switch_company_menu/switch_company_menu";
import {
    applyCompanyTheme,
    clearCompanyTheme,
    companyColorIndicatorStyle,
    companyThemeForeground,
    normalizeCompanyColor,
} from "../src/js/company_theme";

beforeEach(() => {
    cookie.set("cids", "3-2");
    serverState.companies = [
        {
            id: 3,
            name: "Unstatic Labs",
            sequence: 1,
            parent_id: false,
            child_ids: [],
            usl_ui_theme_color: "#2D7D68",
        },
        {
            id: 2,
            name: "USL Media",
            sequence: 2,
            parent_id: false,
            child_ids: [],
            usl_ui_theme_color: "#B85C38",
        },
    ];
});

test("company colors are normalized and malformed payloads fail safely", () => {
    expect(normalizeCompanyColor("#1a2b3c")).toBe("#1A2B3C");
    expect(normalizeCompanyColor(undefined)).toBe("#4E5AA8");
    expect(normalizeCompanyColor("not-a-color")).toBe("#4E5AA8");
    expect(companyColorIndicatorStyle({ usl_ui_theme_color: "invalid" })).toBe(
        "--usl-company-indicator:#4E5AA8"
    );
});

test("foreground selection remains readable on light and dark colors", () => {
    expect(companyThemeForeground("#FFFFFF")).toBe("#111827");
    expect(companyThemeForeground("#000000")).toBe("#FFFFFF");
    expect(companyThemeForeground("#2D7D68")).toBe("#FFFFFF");
});

test("the theme writes only company-scoped CSS variables", () => {
    const root = document.createElement("div");
    expect(applyCompanyTheme(root, { usl_ui_theme_color: "#FFFFFF" })).toEqual({
        color: "#FFFFFF",
        foreground: "#111827",
    });
    expect(root.dataset.uslCompanyTheme).toBe("active");
    expect(root.style.getPropertyValue("--usl-company-color")).toBe("#FFFFFF");
    expect(root.style.getPropertyValue("--usl-company-foreground")).toBe("#111827");
});

test("multi-company scope uses the neutral Odoo color", () => {
    const root = document.createElement("div");
    applyCompanyTheme(root, { usl_ui_theme_color: "#2D7D68" });

    expect(applyCompanyTheme(root, { usl_ui_theme_color: "#2D7D68" }, 2)).toEqual({
        color: "#714B67",
        foreground: "#FFFFFF",
    });
    expect(root.dataset.uslCompanyTheme).toBe("neutral");
    expect(root.style.getPropertyValue("--usl-company-color")).toBe("#714B67");
    expect(root.style.getPropertyValue("--usl-company-foreground")).toBe("#FFFFFF");

    applyCompanyTheme(root, { usl_ui_theme_color: "#2D7D68" });
    clearCompanyTheme(root);
    expect(root).not.toHaveAttribute("data-usl-company-theme");
});

test("the company selector shows colors and the broader company scope", async () => {
    await mountWithCleanup(SwitchCompanyMenu);

    expect("button.o_switch_company_menu .oe_topbar_name").toHaveText("Unstatic Labs");
    expect("button.o_switch_company_menu .usl_company_scope_count").toHaveText("+1");
    expect("button.o_switch_company_menu").toHaveAttribute(
        "title",
        "Unstatic Labs is primary for new records. 2 companies are selected for viewing."
    );
    expect("button.o_switch_company_menu .usl_company_color_dot").toHaveCount(0);

    queryOne("button.o_switch_company_menu").click();
    await animationFrame();
    expect(".o_switch_company_menu_items .usl_company_color_dot").toHaveCount(2);
    expect(
        queryAll(".o_switch_company_menu_items .usl_company_color_dot").map((dot) =>
            dot.style.getPropertyValue("--usl-company-indicator")
        )
    ).toEqual(["#2D7D68", "#B85C38"]);
    expect(".usl_company_scope_summary .text-truncate").toHaveText("Unstatic Labs");
    expect(".usl_company_scope_summary .badge").toHaveText("+1");
});

test("the mobile company selector keeps the active company and scope visible", async () => {
    await mountWithCleanup(MobileSwitchCompanyMenu);

    expect(".o_burger_menu_companies > .w-100 .usl_company_color_dot").toHaveCount(0);
    expect(".o_burger_menu_companies > .w-100 .text-truncate").toHaveText("Unstatic Labs");
    expect(".o_burger_menu_companies > .w-100 .badge").toHaveText("+1");
    expect(".o_burger_menu_companies > .w-100").toHaveAttribute(
        "title",
        "Unstatic Labs is primary for new records. 2 companies are selected for viewing."
    );
});
