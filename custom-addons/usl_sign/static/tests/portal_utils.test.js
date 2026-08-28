import {expect, test} from "@odoo/hoot";

import {focusFirstPdfPage} from "../src/js/portal_utils.esm";

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
