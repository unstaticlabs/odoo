import {
    click,
    contains,
    defineMailModels,
    openFormView,
    start,
    startServer,
} from "@mail/../tests/mail_test_helpers";
import { expect, test } from "@odoo/hoot";
import { mockUserAgent } from "@odoo/hoot-mock";
import { mockService, onRpc } from "@web/../tests/web_test_helpers";

import { canPersistPdfThumbnail } from "@usl_documents/attachment_list_patch";

defineMailModels();

test("PDF thumbnail persistence follows attachment access, not parent access", () => {
    expect(
        canPersistPdfThumbnail({
            ownership_token: undefined,
            uslCanUpdateThumbnail: undefined,
        })
    ).toBe(false);
    expect(
        canPersistPdfThumbnail({
            ownership_token: undefined,
            uslCanUpdateThumbnail: false,
        })
    ).toBe(false);
    expect(
        canPersistPdfThumbnail({
            ownership_token: undefined,
            uslCanUpdateThumbnail: true,
        })
    ).toBe(true);
    expect(
        canPersistPdfThumbnail({
            ownership_token: "scoped-upload-token",
            uslCanUpdateThumbnail: false,
        })
    ).toBe(true);
});

async function openChatterAttachment({
    mobile = false,
    initialDetail = {
        state: "available",
        status_label: "Keep in Documents",
        document_id: false,
    },
    keepResultDetail = {
        state: "pending",
        status_label: "Queued for Documents",
        operation_id: 41,
        document_id: false,
    },
} = {}) {
    if (mobile) {
        mockUserAgent("android");
    }
    const pyEnv = await startServer();
    const partnerId = pyEnv["res.partner"].create({ name: "Archive action" });
    const attachmentId = pyEnv["ir.attachment"].create({
        mimetype: "text/plain",
        name: "keep-me.txt",
        res_id: partnerId,
        res_model: "res.partner",
    });
    let detail = initialDetail;
    onRpc("ir.attachment", "get_keep_in_documents_details", ({ args }) => {
        expect(args).toEqual([[attachmentId]]);
        return { [attachmentId]: detail };
    });
    onRpc("ir.attachment", "action_keep_in_documents_from_ui", ({ args }) => {
        expect(args).toEqual([[attachmentId]]);
        expect.step("kept");
        detail = keepResultDetail;
        return {
            attachment_id: attachmentId,
            operation_id: 41,
            state: detail.state,
            detail,
            message:
                "Archiving started. The original stays attached here; Documents will reuse or create one archive document.",
        };
    });
    onRpc("ir.attachment", "action_open_in_documents", ({ args }) => {
        expect(args).toEqual([[attachmentId]]);
        expect.step("opened");
        return { type: "ir.actions.act_window_close" };
    });
    onRpc("ir.attachment", "action_remove_archived_from_record", ({ args }) => {
        expect(args[0]).toEqual([attachmentId]);
        expect(["unlink", "trash"]).toInclude(args[1]);
        expect.step(`removed:${args[1]}`);
        return {
            removed: true,
            message:
                args[1] === "trash"
                    ? "The document was unlinked and moved to Documents Trash."
                    : "The document was unlinked and remains in Documents.",
        };
    });
    mockService("notification", {
        add(message, options) {
            if (message.includes("The original stays attached here")) {
                expect(options).toEqual({ type: "success" });
                expect.step("notified");
            } else if (message.includes("was unlinked")) {
                expect(options).toEqual({ type: "success" });
                expect.step("removal-notified");
            }
        },
    });
    await start();
    await openFormView("res.partner", partnerId, {
        arch: `
            <form>
                <sheet/>
                <chatter open_attachments="True"/>
            </form>`,
    });
    await contains(".o-mail-AttachmentList");
}

test.tags("desktop");
test("native chatter attachment can be kept in Documents inline", async () => {
    await openChatterAttachment();
    await click(".o_usl_keep_document");

    await contains(".o_usl_archiving_document[title='Queued for Documents']");
    await expect.waitForSteps(["kept", "notified"]);
});

test.tags("mobile");
test("native chatter attachment can be kept from mobile Actions", async () => {
    await openChatterAttachment({ mobile: true });
    await click(".o-mail-AttachmentList button[aria-label='Actions']");
    await click(".dropdown-item", { text: "Keep in Documents" });

    await expect.waitForSteps(["kept", "notified"]);
});

test.tags("desktop");
test("archived chatter attachment opens its exact Documents record", async () => {
    await openChatterAttachment({
        initialDetail: {
            state: "archived",
            status_label: "Open in Documents",
            operation_id: 41,
            document_id: 73,
        },
    });

    await click(".o_usl_open_document");
    await expect.waitForSteps(["opened"]);
});

test.tags("desktop");
test("native chatter attachment shows its real Documents processing phase", async () => {
    await openChatterAttachment({
        initialDetail: {
            state: "processing",
            status_label: "Documents is indexing this file",
            operation_id: 41,
            document_id: false,
        },
    });

    await contains(
        ".o_usl_archiving_document[title='Documents is indexing this file']"
    );
});

test.tags("desktop");
test("record attachment removal offers unlink and Documents Trash", async () => {
    await openChatterAttachment({
        initialDetail: {
            state: "archived",
            status_label: "Open in Documents",
            operation_id: 41,
            document_id: 73,
            can_remove_from_record: true,
            can_move_to_trash: true,
        },
    });

    await contains(".o_usl_open_document");
    await click(".o-mail-Attachment-unlink");
    await contains(".modal-title", { text: "Remove document from this record?" });
    await contains(".o_usl_unlink_document", {
        text: "Unlink Document from Record",
    });
    await contains(".o_usl_unlink_trash_document", {
        text: "Unlink and Move to Trash",
    });
    await click(".o_usl_unlink_document");

    await contains(".o-mail-AttachmentList", { count: 0 });
    await expect.waitForSteps(["removed:unlink", "removal-notified"]);
});

test.tags("desktop");
test("shared archived attachment cannot be moved to Trash from one record", async () => {
    await openChatterAttachment({
        initialDetail: {
            state: "archived",
            status_label: "Open in Documents",
            operation_id: 41,
            document_id: 73,
            can_remove_from_record: true,
            can_move_to_trash: false,
        },
    });

    await contains(".o_usl_open_document");
    await click(".o-mail-Attachment-unlink");
    await contains(".alert-info", {
        text: "shared with another record",
    });
    await contains(".o_usl_unlink_trash_document:disabled");
});
