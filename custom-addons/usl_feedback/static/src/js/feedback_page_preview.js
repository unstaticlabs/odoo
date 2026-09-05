import { browser } from "@web/core/browser/browser";

import { toCanvas } from "../lib/html_to_image";


export const MAX_PAGE_PREVIEW_DIMENSION = 1920;
export const MAX_PAGE_PREVIEW_BYTES = 5 * 1024 * 1024;

const TRANSPARENT_PIXEL =
    "data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=";
const EXCLUDED_SELECTORS = [
    ".o-mail-ChatHub",
    ".o-mail-MessagingMenu",
    ".o_notification_manager",
    ".o-usl-FeedbackButton",
    "[data-usl-feedback-private]",
    "input[type='password']",
    "input[autocomplete='current-password']",
    "input[autocomplete='new-password']",
];

export function isFeedbackPreviewNodeAllowed(node) {
    if (!(node instanceof Element)) {
        return true;
    }
    if (node.matches(EXCLUDED_SELECTORS.join(","))) {
        return false;
    }
    if (
        node.matches(".o-overlay-item, .o_popover") &&
        node.querySelector(".o-mail-MessagingMenu, [class*='o-mail-'], .o-usl-FeedbackPanel")
    ) {
        return false;
    }
    return true;
}

function canvasAsJpeg(canvas, quality) {
    return new Promise((resolve, reject) => {
        canvas.toBlob(
            (blob) =>
                blob
                    ? resolve(blob)
                    : reject(new Error("The page preview could not be encoded.")),
            "image/jpeg",
            quality
        );
    });
}

function resizeCanvas(canvas, scale, documentRef) {
    const resized = documentRef.createElement("canvas");
    resized.width = Math.max(1, Math.round(canvas.width * scale));
    resized.height = Math.max(1, Math.round(canvas.height * scale));
    resized
        .getContext("2d")
        .drawImage(canvas, 0, 0, resized.width, resized.height);
    return resized;
}

async function compressPagePreview(canvas, documentRef) {
    let workingCanvas = canvas;
    let quality = 0.86;
    for (let attempt = 0; attempt < 12; attempt++) {
        const blob = await canvasAsJpeg(workingCanvas, quality);
        if (blob.size <= MAX_PAGE_PREVIEW_BYTES) {
            return { blob, canvas: workingCanvas };
        }
        if (quality > 0.5) {
            quality -= 0.1;
        } else {
            workingCanvas = resizeCanvas(workingCanvas, 0.8, documentRef);
            quality = 0.8;
        }
    }
    throw new Error("The page preview is too large.");
}

export async function captureFeedbackPagePreview({
    root = document.querySelector(".o_web_client"),
    render = toCanvas,
    documentRef = document,
    urlApi = URL,
    now = () => new Date(),
} = {}) {
    if (!root) {
        throw new Error("The Odoo page is unavailable.");
    }
    const width = Math.max(1, Math.round(root.clientWidth || browser.innerWidth));
    const height = Math.max(1, Math.round(root.clientHeight || browser.innerHeight));
    const scale = Math.min(1, MAX_PAGE_PREVIEW_DIMENSION / Math.max(width, height));
    const backgroundColor = window.getComputedStyle(root).backgroundColor || "#ffffff";
    const rendered = await render(root, {
        backgroundColor,
        canvasHeight: Math.max(1, Math.round(height * scale)),
        canvasWidth: Math.max(1, Math.round(width * scale)),
        filter: isFeedbackPreviewNodeAllowed,
        height,
        imagePlaceholder: TRANSPARENT_PIXEL,
        includeQueryParams: false,
        pixelRatio: 1,
        width,
    });
    const { blob, canvas } = await compressPagePreview(rendered, documentRef);
    const previewUrl = urlApi.createObjectURL(blob);
    let released = false;
    return {
        blob,
        height: canvas.height,
        mimetype: "image/jpeg",
        name: `odoo-feedback-${now().toISOString().replaceAll(":", "-")}.jpg`,
        previewUrl,
        release() {
            if (!released) {
                urlApi.revokeObjectURL(previewUrl);
                released = true;
            }
        },
        width: canvas.width,
    };
}
