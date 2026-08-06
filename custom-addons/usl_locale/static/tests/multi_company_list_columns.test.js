import { expect, test } from "@odoo/hoot";

import {
    applyCompanyColumnDefaults,
    companyColumnDefault,
    isCompanyColumn,
} from "../src/js/multi_company_list_columns";

const fields = {
    company_id: { relation: "res.company", type: "many2one" },
    company_ids: { relation: "res.company", type: "many2many" },
    partner_id: { relation: "res.partner", type: "many2one" },
};

test("company columns are identified by relation instead of field name", () => {
    expect(isCompanyColumn({ type: "field", name: "company_id" }, fields)).toBe(true);
    expect(isCompanyColumn({ type: "field", name: "company_ids" }, fields)).toBe(true);
    expect(isCompanyColumn({ type: "field", name: "partner_id" }, fields)).toBe(false);
    expect(isCompanyColumn({ type: "button", name: "company_id" }, fields)).toBe(false);
});

test("company columns default to the active scope", () => {
    expect(companyColumnDefault(1)).toBe(false);
    expect(companyColumnDefault(2)).toBe(true);
    expect(companyColumnDefault(4)).toBe(true);
});

test("the active scope supplies defaults without overriding a saved choice", () => {
    const columns = [
        { type: "field", name: "company_id", optional: "hide" },
        { type: "field", name: "partner_id", optional: "show" },
    ];
    expect(applyCompanyColumnDefaults({}, columns, fields, 1, false)).toEqual({
        company_id: false,
    });
    expect(applyCompanyColumnDefaults({}, columns, fields, 2, false)).toEqual({
        company_id: true,
    });
    expect(
        applyCompanyColumnDefaults({ company_id: false }, columns, fields, 2, true)
    ).toEqual({ company_id: false });
});
