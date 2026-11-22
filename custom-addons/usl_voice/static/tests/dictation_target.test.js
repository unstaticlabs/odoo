import { expect, test } from "@odoo/hoot";
import { dictationTarget, captureTarget } from "@usl_voice/dictation_target";

test("dictation preserves suffix and rejects a stale field", () => {
    const input = document.createElement("textarea");
    document.body.append(input);
    try {
        input.value = "Hello friend";
        input.focus();
        input.setSelectionRange(0, 5);
        const destination = captureTarget(input);
        expect(destination.insert("Bonjour")).toBe(true);
        expect(input.value).toBe("Bonjour friend");
        const stale = captureTarget(input);
        input.value += "!";
        expect(stale.insert("old words")).toBe(false);
        expect(input.value).toBe("Bonjour friend!");
        input.readOnly = true;
        expect(dictationTarget(input)).toBe(null);
        const password = document.createElement("input");
        password.type = "password";
        expect(dictationTarget(password)).toBe(null);
    } finally {
        input.remove();
    }
});

test("a late result leaves a different search field untouched", () => {
    const first = document.createElement("input");
    const search = document.createElement("input");
    search.type = "search";
    document.body.append(first, search);
    try {
        first.focus();
        const destination = captureTarget(first);
        search.focus();
        expect(destination.insert("late")).toBe(false);
        expect(document.activeElement).toBe(search);
        expect(first.value).toBe("");
        expect(search.value).toBe("");
        expect(captureTarget(search).insert("task")).toBe(true);
        expect(search.value).toBe("task");
    } finally {
        first.remove();
        search.remove();
    }
});

test("rich dictation preserves formatting and rejected silence leaves selection intact", () => {
    const editor = document.createElement("div");
    editor.contentEditable = "true";
    editor.innerHTML = "<strong>Hello</strong> world";
    document.body.append(editor);
    try {
        editor.focus();
        const range = document.createRange();
        range.selectNodeContents(editor.firstChild);
        const selection = document.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        expect(dictationTarget(editor.firstChild)).toBe(editor);
        expect(captureTarget(editor).insert("")).toBe(false);
        expect(captureTarget(editor).insert("Bonjour")).toBe(true);
        expect(editor.textContent).toBe("Bonjour world");
        expect(editor.querySelector("strong").textContent).toBe("Bonjour");
    } finally {
        editor.remove();
    }
});
