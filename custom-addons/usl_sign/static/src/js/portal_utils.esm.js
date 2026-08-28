/** @odoo-module **/

export function focusFirstPdfPage(iframe) {
    const viewer = iframe?.contentWindow?.PDFViewerApplication?.pdfViewer;
    const container = iframe?.contentDocument?.getElementById("viewerContainer");
    if (viewer) {
        viewer.currentPageNumber = 1;
    }
    if (container) {
        container.scrollTop = 0;
        container.scrollLeft = 0;
    }
    return Boolean(viewer || container);
}
