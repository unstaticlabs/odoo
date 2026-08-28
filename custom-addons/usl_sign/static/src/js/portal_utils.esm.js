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

function assignedItems(parent) {
    return Object.values(parent?.info?.items || {}).filter(
        (item) => item.role_id === parent.info.role_id
    );
}

function fieldOnExpectedPage(parent, item) {
    const iframeDocument = parent?.iframe?.el?.contentDocument;
    const page = iframeDocument?.getElementsByClassName("page")?.[item.page - 1];
    if (!page) {
        return null;
    }
    const current = parent.items?.[item.id];
    if (current?.isConnected && current.parentElement === page) {
        return current;
    }
    return page.querySelector(`.o_sign_oca_field[data-field="${Number(item.id)}"]`);
}

export function ensureSignerFieldsRendered(parent) {
    const repaired = [];
    for (const item of assignedItems(parent)) {
        let field = fieldOnExpectedPage(parent, item);
        if (!field && typeof parent.postIframeField === "function") {
            field = parent.postIframeField(item)?.[0] || null;
            if (field) {
                repaired.push(String(item.id));
            }
        }
        if (field) {
            parent.items[item.id] = field;
        }
    }
    return repaired;
}

export function signerFieldElement(parent, itemId) {
    const item = Object.values(parent?.info?.items || {}).find(
        (candidate) => String(candidate.id) === String(itemId)
    );
    if (!item || item.role_id !== parent.info.role_id) {
        return null;
    }
    let field = fieldOnExpectedPage(parent, item);
    if (!field) {
        ensureSignerFieldsRendered(parent);
        field = fieldOnExpectedPage(parent, item);
    }
    return field;
}
