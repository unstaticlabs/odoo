import { expect, test } from "@odoo/hoot";
import { captionBox, caretRect, dictationTarget, captureTarget, wantsMicrophone } from "@usl_voice/dictation_target";
import { VoiceCapture } from "@usl_voice/capture";

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

test("the insertion point is found inside the field, on the right line", () => {
    const input = document.createElement("input");
    const area = document.createElement("textarea");
    for (const field of [input, area]) {
        Object.assign(field.style, { position: "fixed", left: "40px", top: "60px", width: "300px", font: "16px/20px monospace", padding: "4px" });
    }
    area.style.height = "120px";
    document.body.append(input, area);
    try {
        input.value = "Bonjour tout le monde";
        input.setSelectionRange(0, 0);
        const start = caretRect(input);
        input.setSelectionRange(7, 7);
        const middle = caretRect(input);
        expect(middle.left).toBeGreaterThan(start.left + 40);
        expect(middle.left).toBeLessThan(input.getBoundingClientRect().right);
        expect(middle.top).toBeGreaterThan(59);
        area.value = "un\ndeux\ntrois";
        area.setSelectionRange(2, 2);
        const first = caretRect(area);
        area.setSelectionRange(area.value.length, area.value.length);
        const third = caretRect(area);
        expect(Math.round(third.top - first.top)).toBe(40);
    } finally {
        input.remove();
        area.remove();
    }
});

test("the resting microphone shows where words are written or searched, not on pickers", () => {
    const holder = document.createElement("div");
    holder.innerHTML = `
        <input class="title" type="text"/>
        <textarea></textarea>
        <div class="o_searchview"><input class="o_searchview_input" type="text"/></div>
        <input class="home-search" type="text" role="combobox"/>
        <input class="native-search" type="search"/>
        <div class="o-autocomplete"><input class="o-autocomplete--input" type="text" role="combobox"/></div>
        <div class="o_field_float"><input class="amount" type="text" inputmode="decimal"/></div>
        <input class="o_datetime_input" type="text"/>`;
    document.body.append(holder);
    try {
        expect(wantsMicrophone(holder.querySelector(".title"))).toBe(true);
        expect(wantsMicrophone(holder.querySelector("textarea"))).toBe(true);
        for (const selector of [".o_searchview_input", ".home-search", ".native-search"]) {
            expect(wantsMicrophone(holder.querySelector(selector))).toBe(true);
        }
        for (const selector of [".o-autocomplete--input", ".amount", ".o_datetime_input"]) {
            expect(wantsMicrophone(holder.querySelector(selector))).toBe(false);
            // The keyboard shortcut still dictates there.
            expect(dictationTarget(holder.querySelector(selector))).not.toBe(null);
        }
    } finally {
        holder.remove();
    }
});

test("a preview sends only the last seconds, and nothing before a second is recorded", () => {
    const capture = new VoiceCapture();
    capture.encoder = { dataBuffer: [new Int8Array(8_000)] };
    expect(capture.tail()).toBe(null);
    capture.encoder.dataBuffer = Array.from({ length: 100 }, () => new Int8Array(16_000));
    expect(capture.tail(20).size).toBe(320_000);
});

test("the words heard are written on the line of the insertion point, inside the field", () => {
    const holder = document.createElement("div");
    holder.style.cssText = "position:fixed;left:40px;top:40px;background-color:rgb(250, 250, 240)";
    const input = document.createElement("input");
    input.style.cssText = "width:400px;padding:0 10px;border:1px solid;font:italic 700 20px serif;background:transparent;color:rgb(1, 2, 3)";
    holder.append(input);
    document.body.append(holder);
    try {
        input.value = "Hello";
        input.focus();
        input.setSelectionRange(5, 5);
        const caret = caretRect(input);
        const box = input.getBoundingClientRect();
        const words = captionBox(input, caret);
        expect(words.left).toBe(caret.left);
        expect(words.top).toBe(caret.top);
        expect(Math.round(words.left + words.width)).toBe(Math.round(box.right - 11));
        expect(words.font).toInclude("italic 700 20px");
        expect(words.color).toBe("rgb(1, 2, 3)");
        // The field is transparent: the words take the background seen behind it.
        expect(words.background).toBe("rgb(250, 250, 240)");
        // No room left after the insertion point: the whole line is used.
        const full = captionBox(input, { ...caret, left: box.right - 30 });
        expect(Math.round(full.left)).toBe(Math.round(box.left + 11));
    } finally {
        holder.remove();
    }
});
