import { expect, test } from "@odoo/hoot";
import { router } from "@web/core/browser/router";

import {
    canonicalCompanyValue,
    canonicalizeUrl,
    normalizeIds,
    parsePanelState,
    parseCompanyIds,
    stableJson,
    withPortableActionAlias,
} from "../src/navigation_state";

test("canonical URLs use a stable semantic parameter order", () => {
    expect(
        canonicalizeUrl(
            "/odoo/contacts?selection=9%2C2&debug=assets&domain=%5B%5D&cids=3-1&nv=1"
        )
    ).toBe("/odoo/contacts?nv=1&cids=3-1&domain=%5B%5D&selection=9%2C2&debug=assets");
});

test("unknown backward-compatible parameters sort after the contract", () => {
    expect(canonicalizeUrl("/odoo?z=2&nv=1&a=1")).toBe("/odoo?nv=1&a=1&z=2");
});

test("semantic JSON serialization sorts object keys without reordering arrays", () => {
    expect(stableJson({ z: 2, a: [{ desc: true, name: "date" }] })).toBe(
        '{"a":[{"desc":true,"name":"date"}],"z":2}'
    );
});

test("company and selection identifiers normalize deterministically", () => {
    expect(parseCompanyIds("3-1-2")).toEqual([3, 1, 2]);
    expect(parseCompanyIds("3-3")).toBe(null);
    expect(parseCompanyIds("3-x")).toBe(null);
    expect(canonicalCompanyValue([3, 2, 1])).toBe("3-1-2");
    expect(normalizeIds("9,2,9,bad")).toEqual([2, 9]);
});

test("search-panel state uses stable field names and semantic values", () => {
    expect(
        parsePanelState('{"root_id":"1","category_id":[12,2],"company_type":"company"}')
    ).toEqual({
        category_id: [2, 12],
        company_type: "company",
        root_id: "1",
    });
    expect(parsePanelState('{"not valid!":1}')).toBe(null);
    expect(parsePanelState('{"category_id":[1,1]}')).toBe(null);
    expect(parsePanelState("[]")).toBe(null);
});

test("action XML ID resolution retains the visible portable state", async () => {
    router.urlToState(
        new URL(
            "https://odoo.example.test/odoo/action-base.action_partner_form" +
                "?nv=1&cids=1&domain=%5B%5B%22customer_rank%22%2C%22%3E%22%2C0%5D%5D" +
                "&view_type=kanban"
        )
    );
    let normalized;
    await withPortableActionAlias(async () => {
        normalized = router.stateToUrl({ action: 123, nv: 1, cids: 1 });
    });
    expect(normalized).toBe(
        "/odoo/action-123?nv=1&cids=1&view_type=kanban" +
            "&domain=%5B%5B%22customer_rank%22%2C%22%3E%22%2C0%5D%5D"
    );
});

test("a genuinely different action does not inherit collection state", () => {
    router.urlToState(
        new URL(
            "https://odoo.example.test/odoo/customers" +
                "?nv=1&cids=1&selection=2%2C9&offset=80"
        )
    );
    expect(router.stateToUrl({ action: "vendors", nv: 1, cids: 1 })).toBe(
        "/odoo/vendors?nv=1&cids=1"
    );
});
