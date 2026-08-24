import {expect, test} from "@odoo/hoot";

import {
    clamp,
    contrastForeground,
    editableItemValues,
    pointToPlacement,
    roleTint,
} from "../src/js/editor_utils.esm";

test("placement is expressed as zoom-independent page percentages", () => {
    const page = {left: 100, top: 50, width: 800, height: 1000};
    expect(pointToPlacement(page, 500, 550, 20, 5)).toEqual({
        position_x: 50,
        position_y: 50,
    });
    expect(pointToPlacement(page, 1000, 1200, 20, 5)).toEqual({
        position_x: 80,
        position_y: 95,
    });
});

test("field geometry is clamped to page bounds", () => {
    expect(clamp(-2, 0, 10)).toBe(0);
    expect(clamp(12, 0, 10)).toBe(10);
    expect(clamp(4, 0, 10)).toBe(4);
});

test("role colors use a stable translucent tint", () => {
    expect(roleTint("#E86A8D")).toBe("rgba(232, 106, 141, 0.16)");
    expect(contrastForeground("#FCD12A")).toBe("#17202A");
    expect(contrastForeground("#000000")).toBe("#FFFFFF");
});

test("history snapshots keep only editable field values", () => {
    expect(editableItemValues({
        id: 42,
        name: "Signature",
        kind: "signature",
        field_id: 3,
        role_id: 5,
        required: true,
        placeholder: false,
        page: 2,
        position_x: "10.5",
        position_y: 20,
        width: 25,
        height: 8,
    })).toEqual({
        field_id: 3,
        role_id: 5,
        required: true,
        placeholder: "",
        page: 2,
        position_x: 10.5,
        position_y: 20,
        width: 25,
        height: 8,
    });
});
