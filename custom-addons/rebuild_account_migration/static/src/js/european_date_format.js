/** @odoo-module **/

import { localization } from "@web/core/l10n/localization";
import { registry } from "@web/core/registry";

/**
 * Keep an English interface without inheriting ambiguous US month-first dates
 * or US accounting-number separators.
 *
 * Native compact dates omit the current year and include any other year.
 * Numeric input and explicit numeric formats continue to use
 * res.lang.date_format. Other supported languages already use their native
 * locale; French is day-first by default.
 */
export const europeanDateFormatService = {
    dependencies: ["localization"],
    start() {
        if (localization.code === "en_US") {
            luxon.Settings.defaultLocale = "en-GB";
            localization.decimalPoint = ",";
            localization.thousandsSep = "\u202f";
            localization.grouping = [3, 0];
        }
    },
};

registry.category("services").add("rebuild_european_date_format", europeanDateFormatService);
