import { expect, test } from "@odoo/hoot";
import { browser } from "@web/core/browser/browser";
import { router } from "@web/core/browser/router";
import { redirect } from "@web/core/utils/urls";

import {
    canonicalCompanyValue,
    canonicalizeUrl,
    companyTransitionPatch,
    normalizeIds,
    parsePanelState,
    parseCompanyIds,
    portableRouteMatchesAction,
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

test("scoped app routes normalize without recursively parsing the visible route", () => {
    redirect(
        "/scoped_app/action-base.action_partner_form?nv=1&cids=1&view_type=list"
    );
    const scopedUrl = new URL(browser.location.href);

    expect(router.urlToState(scopedUrl)).toMatchObject({
        action: "base.action_partner_form",
        cids: 1,
        nv: 1,
        view_type: "list",
    });
    expect(scopedUrl.pathname).toBe("/odoo/action-base.action_partner_form");
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

test("company transitions clear scoped selections and retarget reports", () => {
    expect(
        companyTransitionPatch(
            {
                report: "trial_balance",
                company: 1,
                period: "custom",
                selection: "4,8",
                ws: "private-workspace",
            },
            [2]
        )
    ).toEqual({
        nv: 1,
        cids: "2",
        report: "trial_balance",
        company: 2,
        period: "custom",
        selection: undefined,
        ws: undefined,
    });
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

test("repeated serialization of a new action never adopts the previous action state", () => {
    router.urlToState(
        new URL(
            "https://odoo.example.test/odoo/customers" +
                "?nv=1&cids=1&domain=%5B%5B%22is_company%22%2C%22%3D%22%2Ctrue%5D%5D" +
                "&groupBy=%5B%22country_id%22%5D&columns=email%2Cphone"
        )
    );
    const transitionState = {
        action: "expenses",
        nv: 1,
        cids: 1,
        domain: '[["is_company","=",true]]',
        groupBy: '["country_id"]',
        columns: "email,phone",
    };

    expect(router.stateToUrl(transitionState)).toBe("/odoo/expenses?nv=1&cids=1");
    expect(router.stateToUrl(transitionState)).toBe("/odoo/expenses?nv=1&cids=1");
});

test("normal action navigation strips portable state already merged by core", () => {
    redirect(
        "/odoo/customers?nv=1&cids=1" +
            "&domain=%5B%5B%22is_company%22%2C%22%3D%22%2Ctrue%5D%5D" +
            "&groupBy=%5B%22country_id%22%5D&columns=email%2Cphone"
    );
    router.pushState(
        {
            action: "expenses",
            nv: 1,
            cids: 1,
            domain: '[["is_company","=",true]]',
            groupBy: '["country_id"]',
            columns: "email,phone",
        },
        { sync: true }
    );
    const destination = new URL(browser.location.href);

    expect(destination.pathname).toBe("/odoo/expenses");
    expect(destination.search).toBe("?nv=1&cids=1");
});

test("portable state is consumed only by the action that owns its route", () => {
    const customerRoute = {
        nv: 1,
        action: 55,
        domain: '[["is_company","=",true]]',
    };
    const customers = {
        actionId: 55,
        actionXmlId: "base.action_partner_form",
        resModel: "res.partner",
    };
    const expenses = {
        actionId: 421,
        actionXmlId: "hr_expense.hr_expense_actions_my_all",
        resModel: "hr.expense",
    };

    expect(portableRouteMatchesAction(customerRoute, customers)).toBe(true);
    expect(portableRouteMatchesAction(customerRoute, expenses)).toBe(false);
    expect(
        portableRouteMatchesAction(
            { nv: 1, action: "expenses" },
            {
                ...expenses,
                currentAction: { id: 421, path: "expenses" },
            }
        )
    ).toBe(true);
    expect(
        portableRouteMatchesAction(
            { nv: 1, model: "res.partner" },
            { resModel: "res.partner" }
        )
    ).toBe(true);
});
