import { expect, test } from "@odoo/hoot";
import { mountWithCleanup } from "@web/../tests/web_test_helpers";
import { BadgeTag } from "@web/core/tags_list/badge_tag";

import { namedColorBadgeStyle } from "../src/js/named_color_badges";

test("opaque CSS colors receive a readable matching badge style", async () => {
    await mountWithCleanup(BadgeTag, { props: { text: "Gold" } });

    expect(".o_tag").toHaveStyle({
        backgroundColor: "rgb(255, 215, 0)",
        color: "rgb(17, 24, 39)",
    });
});

test("dark colors use a light foreground", () => {
    expect(namedColorBadgeStyle("Black")).toInclude("color: #FFFFFF");
});

test("ordinary labels and context-dependent colors keep native badge styling", () => {
    expect(namedColorBadgeStyle("Ready for review")).toBe("");
    expect(namedColorBadgeStyle("transparent")).toBe("");
    expect(namedColorBadgeStyle("currentColor")).toBe("");
    expect(namedColorBadgeStyle("inherit")).toBe("");
});
