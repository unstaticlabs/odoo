import {expect, test} from "@odoo/hoot";
import {translatedTerms, translationLoaded} from "@web/core/l10n/translation";

import {UslSignTemplateEditor} from "../src/js/configure_patch.esm";

const ROLE_ONE = {id: 1, name: "Customer", color: "#E86A8D"};
const ROLE_TWO = {id: 2, name: "Employee", color: "#3EA8F9"};
const TEXT_FIELD = {
    id: 10,
    name: "Text",
    kind: "text",
    icon: "fa-font",
    default_width: 20,
    default_height: 5,
};
const INITIALS_FIELD = {
    id: 11,
    name: "Initials",
    kind: "initials",
    icon: "fa-pencil-square-o",
    default_width: 14,
    default_height: 5,
};

function editorFixture() {
    const translationsWereLoaded = translatedTerms[translationLoaded];
    translatedTerms[translationLoaded] = true;
    const iframe = document.createElement("iframe");
    document.body.append(iframe);
    iframe.getBoundingClientRect = () => ({
        left: 0, top: 0, right: 200, bottom: 100, width: 200, height: 100,
    });
    const iframeDocument = iframe.contentDocument;
    const page = iframeDocument.createElement("div");
    page.className = "page";
    page.dataset.pageNumber = "1";
    page.getBoundingClientRect = () => ({
        left: 0, top: 0, right: 200, bottom: 100, width: 200, height: 100,
    });
    const item = {
        id: 42,
        name: "Text",
        field_id: TEXT_FIELD.id,
        role_id: ROLE_ONE.id,
        required: true,
        placeholder: "Agreement reference",
        page: 1,
        position_x: 10,
        position_y: 20,
        width: 20,
        height: 5,
    };
    const fixture = {
        iframe: {el: iframe},
        info: {
            readonly: false,
            revision: 1,
            fields: [TEXT_FIELD],
            roles: [ROLE_ONE, ROLE_TWO],
            items: {"42": item},
        },
        editor: {
            selectedItemId: false,
            selectedRoleId: ROLE_ONE.id,
            selectedFieldId: TEXT_FIELD.id,
            contextPlacement: false,
            pending: 0,
            saveStatus: "saved",
            error: false,
            conflict: false,
        },
        isEditable: true,
        items: {},
        activePaletteDrag: null,
        activeManipulation: null,
        cancelPaletteDrag: UslSignTemplateEditor.prototype.cancelPaletteDrag,
        cancelManipulation: UslSignTemplateEditor.prototype.cancelManipulation,
        runEditorAction: UslSignTemplateEditor.prototype.runEditorAction,
        placeField: UslSignTemplateEditor.prototype.placeField,
        role(roleId) {
            return this.info.roles.find((role) => role.id === Number(roleId));
        },
        field(fieldId) {
            return this.info.fields.find((field) => field.id === Number(fieldId));
        },
        roleLabel(role) {
            return role.name;
        },
        fieldIcon(editorItem) {
            return this.field(editorItem.field_id).icon;
        },
        refreshSelection: UslSignTemplateEditor.prototype.refreshSelection,
    };
    iframeDocument.body.append(page);
    return {
        fixture,
        item,
        page,
        cleanup() {
            iframe.remove();
            translatedTerms[translationLoaded] = translationsWereLoaded;
        },
    };
}

test("the whole PDF field starts movement and remains selectable", () => {
    const {fixture, item, page, cleanup} = editorFixture();
    const movements = [];
    fixture.startManipulation = (event, movedItem, mode) => {
        movements.push({target: event.target.className, itemId: movedItem.id, mode});
    };
    const element = UslSignTemplateEditor.prototype.renderField.call(fixture, item);
    const label = element.querySelector(".usl_sign_field_label");

    label.dispatchEvent(new MouseEvent("pointerdown", {button: 0, bubbles: true}));
    expect(movements).toEqual([
        {target: "usl_sign_field_label", itemId: item.id, mode: "move"},
    ]);

    label.dispatchEvent(new MouseEvent("click", {button: 0, bubbles: true}));
    expect(fixture.editor.selectedItemId).toBe(item.id);
    expect(element.classList.contains("usl_sign_selected")).toBe(true);
    expect(page.querySelectorAll(".o_sign_oca_field").length).toBe(1);
    cleanup();
});

test("right-clicking a PDF field deletes it without opening the page menu", async () => {
    const {fixture, item, cleanup} = editorFixture();
    const deleted = [];
    fixture.deleteField = (deletedItem) => deleted.push(deletedItem.id);
    const element = UslSignTemplateEditor.prototype.renderField.call(fixture, item);
    const event = new MouseEvent("contextmenu", {button: 2, bubbles: true, cancelable: true});

    element.dispatchEvent(event);
    await Promise.resolve();
    await Promise.resolve();

    expect(event.defaultPrevented).toBe(true);
    expect(deleted).toEqual([item.id]);
    expect(fixture.editor.selectedItemId).toBe(item.id);
    cleanup();
});

test("field movement safely clears missing and stale manipulation state", () => {
    const {fixture, item, page, cleanup} = editorFixture();
    const element = document.createElement("div");
    page.append(element);
    fixture.items[item.id] = element;

    for (const staleState of [null, false, {}]) {
        fixture.activeManipulation = staleState;
        expect(() => UslSignTemplateEditor.prototype.startManipulation.call(
            fixture,
            {
                button: 0,
                pointerId: 27,
                clientX: 20,
                clientY: 10,
                currentTarget: element,
                preventDefault() {},
                stopPropagation() {},
            },
            item,
            "move"
        )).not.toThrow();
        fixture.cancelManipulation();
    }
    cleanup();
});

test("pointer movement is normalized to page percentages and saved once", () => {
    const {fixture, item, page, cleanup} = editorFixture();
    let savedChange;
    fixture.updateField = (updatedItem, values, inverse) => {
        savedChange = {itemId: updatedItem.id, values, inverse};
    };
    const element = document.createElement("div");
    page.append(element);
    fixture.items[item.id] = element;

    UslSignTemplateEditor.prototype.startManipulation.call(
        fixture,
        {
            button: 0,
            clientX: 20,
            clientY: 10,
            currentTarget: element,
            preventDefault() {},
            stopPropagation() {},
        },
        item,
        "move"
    );
    document.dispatchEvent(new MouseEvent("pointermove", {clientX: 30, clientY: 20}));
    document.dispatchEvent(new MouseEvent("pointerup", {clientX: 30, clientY: 20}));

    expect(savedChange).toEqual({
        itemId: item.id,
        values: {position_x: 15, position_y: 30},
        inverse: {position_x: 10, position_y: 20},
    });
    expect(element.style.left).toBe("15%");
    expect(element.style.top).toBe("30%");
    cleanup();
});

test("palette drag crosses the same-origin iframe through the controlled pointer bridge", async () => {
    const {fixture, cleanup} = editorFixture();
    const created = [];
    fixture.createField = (values) => created.push(values);
    const button = document.createElement("button");
    document.body.append(button);

    UslSignTemplateEditor.prototype.onPalettePointerDown.call(
        fixture,
        {
            button: 0,
            clientX: 5,
            clientY: 5,
            pointerId: 7,
            currentTarget: button,
            preventDefault() {},
        },
        TEXT_FIELD
    );
    window.dispatchEvent(
        new PointerEvent("pointermove", {clientX: 55, clientY: 35, pointerId: 7})
    );
    window.dispatchEvent(
        new PointerEvent("pointerup", {clientX: 55, clientY: 35, pointerId: 7})
    );
    await Promise.resolve();
    await Promise.resolve();

    expect(created).toHaveLength(1);
    expect(created[0].field_id).toBe(TEXT_FIELD.id);
    expect(created[0].role_id).toBe(ROLE_ONE.id);
    expect(created[0].page).toBe(1);
    expect(created[0].position_x).toBeGreaterThan(0);
    expect(created[0].position_y).toBeGreaterThan(0);
    button.remove();
    cleanup();
});

test("starting a palette drag safely clears missing and stale drag state", () => {
    const {fixture, cleanup} = editorFixture();
    const button = document.createElement("button");
    document.body.append(button);

    for (const staleState of [null, false, {}]) {
        fixture.activePaletteDrag = staleState;
        expect(() => UslSignTemplateEditor.prototype.onPalettePointerDown.call(
            fixture,
            {
                button: 0,
                clientX: 5,
                clientY: 5,
                pointerId: 17,
                currentTarget: button,
                preventDefault() {},
            },
            TEXT_FIELD
        )).not.toThrow();
        fixture.cancelPaletteDrag();
    }

    button.remove();
    cleanup();
});

test("initials can be placed on every page as one undoable command", async () => {
    const commands = [];
    const history = [];
    const fixture = {
        canPlaceOnEveryPage: true,
        editor: {placeOnEveryPage: true, selectedItemId: false},
        async applyCommand(command) {
            commands.push(command);
            return {
                status: "ok",
                items: [
                    {...command.values, id: 51, page: 1},
                    {...command.values, id: 52, page: 2},
                    {...command.values, id: 53, page: 3},
                ],
            };
        },
        createField() {
            throw new Error("Single-page placement should not be used");
        },
        createFieldsOnEveryPage: UslSignTemplateEditor.prototype.createFieldsOnEveryPage,
        refreshSelection() {},
        pushHistory(entry) {
            history.push(entry);
        },
    };
    const values = {
        field_id: INITIALS_FIELD.id,
        role_id: ROLE_ONE.id,
        page: 2,
        position_x: 80,
        position_y: 90,
        width: 14,
        height: 5,
    };

    await UslSignTemplateEditor.prototype.placeField.call(fixture, values);

    expect(commands).toEqual([{action: "create_all_pages", values}]);
    expect(fixture.editor.selectedItemId).toBe(52);
    expect(history).toEqual([
        {kind: "create_many", itemIds: [51, 52, 53], values},
    ]);
});

test("changing the signer immediately recolors the PDF field", async () => {
    const {fixture, item, cleanup} = editorFixture();
    fixture.renderField = (updatedItem) =>
        UslSignTemplateEditor.prototype.renderField.call(fixture, updatedItem);
    fixture.applyCommand = async () => ({status: "ok", revision: 2});
    fixture.pushHistory = () => undefined;
    const original = fixture.renderField(item);
    expect(original.style.getPropertyValue("--usl-role-color")).toBe(ROLE_ONE.color);

    await UslSignTemplateEditor.prototype.updateField.call(
        fixture,
        item,
        {role_id: ROLE_TWO.id}
    );

    const recolored = fixture.items[item.id];
    expect(recolored.dataset.roleId).toBe(String(ROLE_TWO.id));
    expect(recolored.style.getPropertyValue("--usl-role-color")).toBe(ROLE_TWO.color);
    expect(recolored.getAttribute("aria-label").includes(ROLE_TWO.name)).toBe(true);
    expect(recolored.querySelector(".usl_sign_required").textContent).toBe("*");
    cleanup();
});

test("a stale field for a removed signer is ignored instead of crashing the editor", () => {
    const {fixture, item, page, cleanup} = editorFixture();
    fixture.info.roles = [ROLE_TWO];
    fixture.roleLabel = UslSignTemplateEditor.prototype.roleLabel;

    const rendered = UslSignTemplateEditor.prototype.renderField.call(fixture, item);

    expect(rendered).toBe(undefined);
    expect(page.querySelectorAll(".o_sign_oca_field")).toHaveCount(0);
    expect(UslSignTemplateEditor.prototype.roleLabel.call(fixture, undefined)).toBe(
        "Unassigned signer"
    );
    cleanup();
});

test("autosaves are ordered and a failed update restores the visible field", async () => {
    const calls = [];
    const queueFixture = {
        editor: {
            pending: 0,
            saveStatus: "saved",
            error: false,
            conflict: false,
            selectedRoleId: ROLE_ONE.id,
        },
        commandQueue: Promise.resolve(),
        model: "sign.oca.template",
        res_id: 7,
        info: {revision: 1, items: {}, roles: [ROLE_ONE]},
        items: {},
        notification: {add() {}},
        role(roleId) {
            return this.info.roles.find((role) => role.id === Number(roleId));
        },
        orm: {
            async call(_model, _method, args) {
                calls.push({revision: args[2], action: args[3].action});
                return {status: "ok", revision: args[2] + 1};
            },
        },
    };
    await Promise.all([
        UslSignTemplateEditor.prototype.applyCommand.call(queueFixture, {action: "first"}),
        UslSignTemplateEditor.prototype.applyCommand.call(queueFixture, {action: "second"}),
    ]);
    expect(calls).toEqual([
        {revision: 1, action: "first"},
        {revision: 2, action: "second"},
    ]);
    expect(queueFixture.editor.pending).toBe(0);
    expect(queueFixture.editor.saveStatus).toBe("saved");

    const {fixture, item, cleanup} = editorFixture();
    fixture.renderField = (updatedItem) =>
        UslSignTemplateEditor.prototype.renderField.call(fixture, updatedItem);
    fixture.applyCommand = async () => {
        throw new Error("Synthetic save failure");
    };
    fixture.pushHistory = () => undefined;
    fixture.renderField(item);
    let failed = false;
    try {
        await UslSignTemplateEditor.prototype.updateField.call(
            fixture,
            item,
            {role_id: ROLE_TWO.id}
        );
    } catch {
        failed = true;
    }
    expect(failed).toBe(true);
    expect(item.role_id).toBe(ROLE_ONE.id);
    expect(fixture.items[item.id].style.getPropertyValue("--usl-role-color")).toBe(
        ROLE_ONE.color
    );
    cleanup();
});
