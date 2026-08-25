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
import { onRpc } from "@web/../tests/web_test_helpers";

defineMailModels();

async function openChatterAttachment({ mobile = false } = {}) {
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
    onRpc("ir.attachment", "get_keep_in_documents_states", ({ args }) => {
        expect(args).toEqual([[attachmentId]]);
        return { [attachmentId]: "available" };
    });
    onRpc("ir.attachment", "action_keep_in_documents_from_ui", ({ args }) => {
        expect(args).toEqual([[attachmentId]]);
        expect.step("kept");
        return {
            attachment_id: attachmentId,
            operation_id: 41,
            state: "queued",
            message: "This file will be kept in Documents.",
        };
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

    await contains(".o_notification", {
        text: "This file will be kept in Documents.",
    });
    expect.verifySteps(["kept"]);
});

test.tags("mobile");
test("native chatter attachment can be kept from mobile Actions", async () => {
    await openChatterAttachment({ mobile: true });
    await click(".o-mail-AttachmentList button[aria-label='Actions']");
    await click(".dropdown-item", { text: "Keep in Documents" });

    await contains(".o_notification", {
        text: "This file will be kept in Documents.",
    });
    expect.verifySteps(["kept"]);
});
