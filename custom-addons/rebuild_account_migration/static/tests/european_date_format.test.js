import { beforeEach, expect, test } from "@odoo/hoot";
import { patchWithCleanup } from "@web/../tests/web_test_helpers";

import { localization } from "@web/core/l10n/localization";
import { registry } from "@web/core/registry";
import { formatDate, formatDateTime } from "@web/views/fields/formatters";
import {
    europeanDateFormatService,
    europeanDateFormatter,
    europeanDateTimeFormatter,
} from "../src/js/european_date_format";

const { DateTime, Settings } = luxon;

beforeEach(() => {
    patchWithCleanup(localization, {
        code: "en_US",
        dateFormat: "dd/MM/yyyy",
        dateTimeFormat: "dd/MM/yyyy HH:mm:ss",
    });
    patchWithCleanup(Settings, { defaultLocale: "en-US" });
});

test("English US users receive unambiguous European dates everywhere", () => {
    europeanDateFormatService.start();

    const value = DateTime.fromISO("2026-06-10T14:30:00");
    expect(formatDate(value, { numeric: true })).toBe("10/06/2026");
    expect(formatDateTime(value, { numeric: true })).toMatch(/^10\/06\/2026 /);
    expect(formatDate(value)).toBe("10 Jun 2026");
    expect(formatDateTime(value)).toMatch(/^10 Jun 2026, /);
});

test("shared field renderers always include an unambiguous complete date", () => {
    const value = DateTime.fromISO("2026-06-10T14:30:00");

    expect(europeanDateFormatter(value)).toBe("10/06/2026");
    expect(europeanDateTimeFormatter(value)).toMatch(/^10\/06\/2026 /);
    expect(registry.category("formatters").get("date")).toBe(europeanDateFormatter);
    expect(registry.category("formatters").get("datetime")).toBe(europeanDateTimeFormatter);
});

test("the service preserves the locale of non-US languages", () => {
    localization.code = "fr_FR";
    Settings.defaultLocale = "fr-FR";

    europeanDateFormatService.start();

    expect(Settings.defaultLocale).toBe("fr-FR");
});
