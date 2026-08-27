import {expect, test} from "@odoo/hoot";
import {defineMailModels} from "@mail/../tests/mail_test_helpers";
import {mountWithCleanup} from "@web/../tests/web_test_helpers";

import {
    SignDocumentCardPreviewField,
    SignDocumentPreviewField,
} from "../src/js/document_preview_field.esm";

defineMailModels();

test("document cards reuse an authorized Documents thumbnail", async () => {
    await mountWithCleanup(SignDocumentCardPreviewField, {
        props: {
            name: "document_preview_url",
            record: {
                data: {
                    document_preview_url: "/web/content/sign.oca.request/4/data/file.pdf",
                    document_thumbnail_url: "/usl_documents/17/thumbnail",
                },
            },
        },
    });

    expect(".usl_sign_document_card_preview img").toHaveCount(1);
    expect(".o_usl_document_preview").toHaveCount(0);
});

test("document preview has an explicit unavailable state", async () => {
    await mountWithCleanup(SignDocumentPreviewField, {
        props: {
            name: "document_preview_url",
            record: {data: {document_preview_url: false}},
        },
    });

    expect(".usl_sign_document_preview_placeholder").toHaveText(/Preview unavailable/);
});
