import {expect, test} from "@odoo/hoot";

import {SignTemplateKanbanController} from "../src/js/template_library.esm";

test("template PDF upload creates one multi-document envelope and opens the editor", async () => {
    const calls = [];
    const actions = [];
    const fixture = {
        uploadState: {uploading: false},
        notification: {add(message, options) { calls.push({message, options}); }},
        orm: {
            async call(model, method, args, kwargs) {
                calls.push({model, method, args, kwargs});
                return {type: "ir.actions.client", tag: "usl_sign_template_configure"};
            },
        },
        actionService: {async doAction(action) { actions.push(action); }},
    };
    await SignTemplateKanbanController.prototype.uploadFiles.call(fixture, [
        new File(["%PDF-1.7\nfirst"], "Routine Agreement.pdf", {type: "application/pdf"}),
        new File(["%PDF-1.7\nannex"], "Annex.pdf", {type: "application/pdf"}),
    ]);

    expect(calls).toHaveLength(1);
    expect(calls[0].model).toBe("sign.oca.template");
    expect(calls[0].method).toBe("create_from_documents");
    expect(calls[0].kwargs.documents.map((document) => document.name)).toEqual([
        "Routine Agreement.pdf",
        "Annex.pdf",
    ]);
    expect(actions).toEqual([
        {type: "ir.actions.client", tag: "usl_sign_template_configure"},
    ]);
    expect(fixture.uploadState.uploading).toBe(false);
});

test("template drop rejects a mixed file selection before RPC", async () => {
    const notices = [];
    const fixture = {
        uploadState: {uploading: false},
        notification: {add(message, options) { notices.push({message, options}); }},
        orm: {call() { throw new Error("RPC must not run"); }},
    };
    await SignTemplateKanbanController.prototype.uploadFiles.call(fixture, [
        new File(["not a PDF"], "notes.txt", {type: "text/plain"}),
    ]);

    expect(notices).toHaveLength(1);
    expect(notices[0].options.type).toBe("danger");
    expect(fixture.uploadState.uploading).toBe(false);
});
