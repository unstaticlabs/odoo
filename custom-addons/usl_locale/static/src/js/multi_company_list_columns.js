/** @odoo-module **/

import { browser } from "@web/core/browser/browser";
import { user } from "@web/core/user";
import { patch } from "@web/core/utils/patch";
import { ListRenderer } from "@web/views/list/list_renderer";

export function isCompanyColumn(column, fields) {
    return column.type === "field" && fields[column.name]?.relation === "res.company";
}

export function companyColumnDefault(activeCompanyCount) {
    return activeCompanyCount > 1;
}

export function applyCompanyColumnDefaults(
    optionalActiveFields,
    columns,
    fields,
    activeCompanyCount,
    hasStoredPreference
) {
    if (hasStoredPreference) {
        return optionalActiveFields;
    }
    const showCompany = companyColumnDefault(activeCompanyCount);
    for (const column of columns) {
        if (column.optional && isCompanyColumn(column, fields)) {
            optionalActiveFields[column.name] = showCompany;
        }
    }
    return optionalActiveFields;
}

patch(ListRenderer.prototype, {
    processAllColumn(allColumns, list) {
        return super.processAllColumn(...arguments).map((column) =>
            isCompanyColumn(column, list.fields) && !column.optional
                ? { ...column, optional: "hide" }
                : column
        );
    },

    computeOptionalActiveFields() {
        const optionalActiveFields = super.computeOptionalActiveFields(...arguments);
        return applyCompanyColumnDefaults(
            optionalActiveFields,
            this.allColumns,
            this.props.list.fields,
            user.activeCompanies.length,
            browser.localStorage.getItem(this.keyOptionalFields) !== null
        );
    },
});
