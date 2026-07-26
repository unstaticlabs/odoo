/** @odoo-module **/

import { localization } from "@web/core/l10n/localization";
import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";
import { DateTimeField } from "@web/views/fields/datetime/datetime_field";
import {
    formatDate as nativeFormatDate,
    formatDateTime as nativeFormatDateTime,
} from "@web/views/fields/formatters";

export function europeanDateFormatter(value, options = {}) {
    return nativeFormatDate(value, { ...options, numeric: true });
}
europeanDateFormatter.extractOptions = nativeFormatDate.extractOptions;

export function europeanDateTimeFormatter(value, options = {}) {
    return nativeFormatDateTime(value, { ...options, numeric: true });
}
europeanDateTimeFormatter.extractOptions = nativeFormatDateTime.extractOptions;

const formatters = registry.category("formatters");
formatters.add("date", europeanDateFormatter, { force: true });
formatters.add("datetime", europeanDateTimeFormatter, { force: true });

patch(DateTimeField.prototype, {
    getFormattedValue(valueIndex) {
        return super.getFormattedValue(valueIndex, true);
    },
});

/**
 * Keep an English interface without inheriting the ambiguous US month-first
 * presentation used by Luxon's human-readable date formatters.
 *
 * Numeric dates continue to use res.lang.date_format. Other supported
 * languages already use their native locale; French is day-first by default.
 */
export const europeanDateFormatService = {
    dependencies: ["localization"],
    start() {
        if (localization.code === "en_US") {
            luxon.Settings.defaultLocale = "en-GB";
        }
    },
};

registry.category("services").add("rebuild_european_date_format", europeanDateFormatService);
