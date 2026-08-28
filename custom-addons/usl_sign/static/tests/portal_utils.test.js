import {expect, test} from "@odoo/hoot";

import {
    ensureSignerFieldsRendered,
    focusFirstPdfPage,
    signerFieldElement,
} from "../src/js/portal_utils.esm";

test("the signer journey always opens the first PDF page", () => {
    const container = document.createElement("div");
    container.id = "viewerContainer";
    container.scrollTop = 720;
    container.scrollLeft = 48;
    const viewer = {currentPageNumber: 7};
    const iframe = {
        contentDocument: {getElementById: () => container},
        contentWindow: {PDFViewerApplication: {pdfViewer: viewer}},
    };

    expect(focusFirstPdfPage(iframe)).toBe(true);
    expect(viewer.currentPageNumber).toBe(1);
    expect(container.scrollTop).toBe(0);
    expect(container.scrollLeft).toBe(0);
});

test("missing signer fields are restored on their PDF page", () => {
    const pages = [document.createElement("section"), document.createElement("section")];
    const iframeDocument = document.implementation.createHTMLDocument();
    for (const page of pages) {
        page.className = "page";
        iframeDocument.body.append(page);
    }
    const existing = document.createElement("div");
    existing.className = "o_sign_oca_field";
    existing.dataset.field = "1";
    pages[0].append(existing);
    const parent = {
        iframe: {el: {contentDocument: iframeDocument}},
        info: {
            role_id: 4,
            items: {
                1: {id: 1, role_id: 4, page: 1},
                2: {id: 2, role_id: 4, page: 2},
                3: {id: 3, role_id: 9, page: 2},
            },
        },
        items: {1: existing},
        postIframeField(item) {
            const field = document.createElement("div");
            field.className = "o_sign_oca_field";
            field.dataset.field = String(item.id);
            pages[item.page - 1].append(field);
            this.items[item.id] = field;
            return [field];
        },
    };

    expect(ensureSignerFieldsRendered(parent)).toEqual(["2"]);
    expect(pages[0].querySelectorAll('[data-field="1"]').length).toBe(1);
    expect(pages[1].querySelectorAll('[data-field="2"]').length).toBe(1);
    expect(pages[1].querySelectorAll('[data-field="3"]').length).toBe(0);
    expect(signerFieldElement(parent, "2")).toBe(parent.items[2]);
});
