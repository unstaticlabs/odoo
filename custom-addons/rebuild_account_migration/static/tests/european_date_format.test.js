import { beforeEach, expect, test } from "@odoo/hoot";
import { patchWithCleanup } from "@web/../tests/web_test_helpers";

import { localization } from "@web/core/l10n/localization";
import {
    formatDate,
    formatDateTime,
    formatFloat,
} from "@web/views/fields/formatters";
import { europeanDateFormatService } from "../src/js/european_date_format";

const { DateTime, Settings } = luxon;

beforeEach(() => {
    patchWithCleanup(localization, {
        code: "en_US",
        dateFormat: "dd/MM/yyyy",
        dateTimeFormat: "dd/MM/yyyy HH:mm:ss",
        decimalPoint: ".",
        grouping: [3, 0],
        thousandsSep: ",",
    });
    patchWithCleanup(Settings, { defaultLocale: "en-US" });
});

test("English US users receive unambiguous European dates everywhere", () => {
    europeanDateFormatService.start();

    const currentYearValue = DateTime.local().set({
        month: 6,
        day: 10,
        hour: 14,
        minute: 30,
    });
    const priorYearValue = currentYearValue.minus({ years: 1 });
    expect(formatDate(currentYearValue, { numeric: true })).toBe(
        `10/06/${currentYearValue.year}`
    );
    expect(formatDateTime(currentYearValue, { numeric: true })).toMatch(
        new RegExp(`^10/06/${currentYearValue.year} `)
    );
    expect(formatDate(currentYearValue)).toBe("10 Jun");
    expect(formatDate(priorYearValue)).toBe(`10 Jun ${priorYearValue.year}`);
    expect(formatDateTime(currentYearValue)).toMatch(/^10 Jun, /);
    expect(formatDateTime(priorYearValue)).toMatch(
        new RegExp(`^10 Jun ${priorYearValue.year}, `)
    );
    expect(formatFloat(15603.74, { digits: [16, 2] })).toBe("15 603,74");
});

test("the service preserves the locale of non-US languages", () => {
    localization.code = "fr_FR";
    Settings.defaultLocale = "fr-FR";

    europeanDateFormatService.start();

    expect(Settings.defaultLocale).toBe("fr-FR");
});
