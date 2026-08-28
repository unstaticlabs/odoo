/** @odoo-module **/
/* global window */

import {onWillUnmount, useState} from "@odoo/owl";
import {_t} from "@web/core/l10n/translation";
import {registry} from "@web/core/registry";
import {useService} from "@web/core/utils/hooks";
import SignOcaConfigure from "@sign_oca/components/sign_oca_configure/sign_oca_configure.esm";

import {
    clamp,
    contrastForeground,
    editableItemValues,
    operationUuid,
    pointToPlacement,
    roleTint,
} from "./editor_utils.esm";

const EDITOR_VALUES = [
    "field_id",
    "role_id",
    "required",
    "placeholder",
    "page",
    "position_x",
    "position_y",
    "width",
    "height",
];

export class UslSignTemplateEditor extends SignOcaConfigure {
    setup() {
        const routeModels = {
            usl_sign_request_configure: "sign.oca.request",
            usl_sign_template_configure: "sign.oca.template",
        };
        const routeTag = this.props.action.tag || window.location.pathname.split("/").at(-1);
        const routeModel = routeModels[routeTag];
        if (routeModel && !this.props.action.params?.res_model) {
            const routeMatch = window.location.pathname.match(/\/(\d+)\/[^/]+\/?$/);
            if (routeMatch) {
                this.props.action.params = {
                    ...this.props.action.params,
                    res_model: routeModel,
                    res_id: Number(routeMatch[1]),
                };
            }
        }
        super.setup(...arguments);
        this.notification = useService("notification");
        this.actionService = useService("action");
        this.editor = useState({
            loading: true,
            libraryOpen: true,
            selectedFieldId: false,
            selectedRoleId: false,
            selectedItemId: false,
            placeOnEveryPage: false,
            newRoleName: "",
            contextPlacement: false,
            saveStatus: "saved",
            pending: 0,
            error: false,
            conflict: false,
            undoCount: 0,
            redoCount: 0,
        });
        this.commandQueue = Promise.resolve();
        this.undoStack = [];
        this.redoStack = [];
        this.pageListeners = [];
        this.initialPageApplied = false;
        this.activeManipulation = null;
        this.activePaletteDrag = null;
        this.beforeUnload = (event) => {
            if (this.editor.pending || this.editor.error) {
                event.preventDefault();
                event.returnValue = "";
            }
        };
        window.addEventListener("beforeunload", this.beforeUnload);
        onWillUnmount(() => {
            window.removeEventListener("beforeunload", this.beforeUnload);
            for (const cleanup of this.pageListeners) {
                cleanup();
            }
            this.cancelManipulation();
            this.cancelPaletteDrag();
        });
    }

    async willStart() {
        await super.willStart(...arguments);
        this.props.action.name = this.info.name;
        this.env.config.setDisplayName?.(this.info.name);
        this.editor.selectedRoleId = this.info.roles[0]?.id || false;
        this.editor.selectedFieldId = this.info.fields[0]?.id || false;
    }

    get selectedItem() {
        return this.info.items[String(this.editor.selectedItemId)] ||
            this.info.items[this.editor.selectedItemId] || false;
    }

    get selectedField() {
        return this.info.fields.find((field) => field.id === this.editor.selectedFieldId);
    }

    get isEditable() {
        return !this.info.readonly && !this.editor.conflict;
    }

    get canPlaceOnEveryPage() {
        return this.selectedField?.kind === "initials";
    }

    get finishLabel() {
        return this.info.editor_mode === "request" ? _t("Continue") : _t("Done");
    }

    async finishEditing() {
        await this.commandQueue;
        if (this.editor.error || this.editor.conflict) {
            this.notification.add(_t("Fix the save problem before continuing."), {
                type: "warning",
            });
            return;
        }
        const resModel = this.props.action.params?.res_model || this.res_model;
        const resId = this.props.action.params?.res_id || this.res_id;
        return this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: resModel,
            res_id: resId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    role(roleId) {
        return this.info.roles.find((role) => role.id === Number(roleId));
    }

    field(fieldId) {
        return this.info.fields.find((field) => field.id === Number(fieldId));
    }

    roleLabel(role) {
        return role?.signer_name || role?.name || _t("Unassigned signer");
    }

    roleButtonStyle(role) {
        return `--usl-role-color:${role.color};--usl-role-tint:${roleTint(role.color)};` +
            `--usl-role-foreground:${contrastForeground(role.color)}`;
    }

    fieldIcon(item) {
        return this.field(item.field_id)?.icon || "fa-font";
    }

    toggleLibrary() {
        this.editor.libraryOpen = !this.editor.libraryOpen;
    }

    selectRole(event) {
        this.editor.selectedRoleId = Number(event.currentTarget.dataset.roleId);
    }

    selectField(event) {
        if (!this.isEditable) {
            return;
        }
        this.editor.selectedFieldId = Number(event.currentTarget.dataset.fieldId);
        this.editor.selectedItemId = false;
        if (!this.canPlaceOnEveryPage) {
            this.editor.placeOnEveryPage = false;
        }
    }

    toggleEveryPagePlacement(event) {
        this.editor.placeOnEveryPage = event.target.checked;
    }

    closeInspector() {
        this.editor.selectedItemId = false;
        this.editor.contextPlacement = false;
        this.refreshSelection();
    }

    selectContextField(event) {
        this.editor.selectedFieldId = Number(event.target.value);
        if (!this.canPlaceOnEveryPage) {
            this.editor.placeOnEveryPage = false;
        }
    }

    selectContextRole(event) {
        this.editor.selectedRoleId = Number(event.target.value);
    }

    updateNewRoleName(event) {
        this.editor.newRoleName = event.target.value;
    }

    onNewRoleKeydown(event) {
        if (event.key === "Enter") {
            event.preventDefault();
            this.addRole();
        }
    }

    pdfApplication() {
        return this.iframe.el?.contentWindow?.PDFViewerApplication;
    }

    previousPage() {
        const viewer = this.pdfApplication()?.pdfViewer;
        if (viewer) {
            viewer.currentPageNumber = Math.max(1, viewer.currentPageNumber - 1);
        }
    }

    nextPage() {
        const viewer = this.pdfApplication()?.pdfViewer;
        if (viewer) {
            viewer.currentPageNumber = Math.min(viewer.pagesCount, viewer.currentPageNumber + 1);
        }
    }

    showFirstPageOnStart(document) {
        if (this.initialPageApplied) {
            return;
        }
        const viewer = this.pdfApplication()?.pdfViewer;
        if (viewer) {
            viewer.currentPageNumber = 1;
        }
        const container = document.getElementById("viewerContainer");
        if (container) {
            container.scrollTop = 0;
            container.scrollLeft = 0;
        }
        this.initialPageApplied = true;
    }

    zoomIn() {
        this.pdfApplication()?.zoomIn();
    }

    zoomOut() {
        this.pdfApplication()?.zoomOut();
    }

    showThumbnails() {
        const sidebar = this.pdfApplication()?.pdfSidebar;
        if (sidebar) {
            sidebar.switchView(1);
            sidebar.open();
        }
    }

    injectIframeAssets(document) {
        if (!document.getElementById("usl-sign-oca-assets")) {
            const stylesheet = document.createElement("link");
            stylesheet.id = "usl-sign-oca-assets";
            stylesheet.rel = "stylesheet";
            stylesheet.href = "/sign_oca/get_assets.css";
            document.head.append(stylesheet);
        }
        if (!document.getElementById("usl-sign-editor-style")) {
            const style = document.createElement("style");
            style.id = "usl-sign-editor-style";
            style.textContent = `
                #editorModeButtons, #printButton, #downloadButton, #secondaryPrint,
                #secondaryDownload, #viewBookmark, #openFile { display: none !important; }
                .o_sign_oca_field {
                    box-sizing: border-box; border: 2px solid var(--usl-role-color);
                    border-radius: 4px; background: var(--usl-role-tint); color: #17202a;
                    cursor: pointer; display: flex; align-items: center; min-height: 24px;
                    overflow: visible; z-index: 80;
                    user-select: none; -webkit-user-select: none;
                }
                .o_sign_oca_field.usl_sign_selected { outline: 3px solid rgba(113,75,103,.32); outline-offset: 2px; z-index: 82; }
                .o_sign_oca_field { cursor: move; touch-action: none; }
                .usl_sign_field_move { cursor: move; padding: 4px 5px; flex: 0 0 auto; }
                .usl_sign_field_label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 600 12px system-ui; }
                .usl_sign_required { margin-left: auto; padding: 0 5px; font-weight: 800; color: var(--usl-role-color); }
                .usl_sign_resize_handle { position: absolute; right: -2px; bottom: -2px; width: 14px; height: 14px; cursor: nwse-resize; background: var(--usl-role-color); border-radius: 3px 0 3px 0; }
                .usl_sign_resize_handle::after { content: ""; position: absolute; right: 3px; bottom: 3px; width: 6px; height: 6px; border-right: 2px solid white; border-bottom: 2px solid white; }
            `;
            document.head.append(style);
        }
    }

    postIframeFields() {
        const document = this.iframe.el.contentDocument;
        this.injectIframeAssets(document);
        this.bindPdfScrolling(document);
        this.showFirstPageOnStart(document);
        for (const page of document.getElementsByClassName("page")) {
            this.bindPage(page);
        }
        for (const item of Object.values(this.info.items)) {
            this.renderField(item);
        }
        if (!document.querySelector(".o_sign_oca_ready")) {
            const marker = document.createElement("div");
            marker.className = "o_sign_oca_ready";
            document.getElementsByClassName("page")[0]?.append(marker);
        }
        document.getElementById("viewer")?.classList.add("sign_oca_ready");
        this.editor.loading = false;
        this.iframeLoaded.resolve();
    }

    bindPdfScrolling(document) {
        const viewer = document.getElementById("viewerContainer");
        if (!viewer || viewer.dataset.uslScrollReady) {
            return;
        }
        viewer.dataset.uslScrollReady = "1";
        viewer.style.overflow = "auto";
        viewer.style.overscrollBehavior = "contain";
        this.pageListeners.push(() => {
            delete viewer.dataset.uslScrollReady;
        });
    }

    runEditorAction(action) {
        return Promise.resolve().then(action).catch(() => undefined);
    }

    bindPage(page) {
        if (page.dataset.uslEditorReady) {
            return;
        }
        page.dataset.uslEditorReady = "1";
        const click = (event) => {
            if (!this.isEditable || event.target.closest(".o_sign_oca_field")) {
                return;
            }
            if (!this.editor.selectedFieldId || !this.editor.selectedRoleId) {
                this.notification.add(_t("Choose a field type and signer first."), {type: "warning"});
                return;
            }
            const field = this.selectedField;
            const placement = pointToPlacement(
                page.getBoundingClientRect(), event.clientX, event.clientY,
                field.default_width, field.default_height
            );
            this.runEditorAction(() => this.placeField({
                field_id: field.id,
                role_id: this.editor.selectedRoleId,
                page: Number(page.dataset.pageNumber),
                ...placement,
                width: field.default_width,
                height: field.default_height,
            }));
        };
        const contextmenu = (event) => {
            if (!this.isEditable || event.target.closest(".o_sign_oca_field")) {
                return;
            }
            event.preventDefault();
            const field = this.selectedField;
            const width = field?.default_width || 20;
            const height = field?.default_height || 5;
            this.editor.contextPlacement = {
                page: Number(page.dataset.pageNumber),
                ...pointToPlacement(page.getBoundingClientRect(), event.clientX, event.clientY, width, height),
            };
            this.editor.selectedItemId = false;
        };
        page.addEventListener("click", click);
        page.addEventListener("contextmenu", contextmenu);
        this.pageListeners.push(() => {
            page.removeEventListener("click", click);
            page.removeEventListener("contextmenu", contextmenu);
        });
    }

    renderField(item) {
        const document = this.iframe.el.contentDocument;
        const page = document.getElementsByClassName("page")[Number(item.page) - 1];
        if (!page) {
            return;
        }
        this.items[item.id]?.remove();
        const role = this.role(item.role_id);
        if (!role) {
            delete this.items[item.id];
            return;
        }
        const element = document.createElement("div");
        element.className = "o_sign_oca_field";
        element.dataset.field = item.id;
        element.dataset.roleId = item.role_id;
        element.tabIndex = this.isEditable ? 0 : -1;
        element.setAttribute("role", "button");
        element.setAttribute(
            "aria-label",
            `${item.name}, ${this.roleLabel(role)}, ${item.required ? _t("required") : _t("optional")}`
        );
        Object.assign(element.style, {
            top: `${item.position_y}%`,
            left: `${item.position_x}%`,
            width: `${item.width}%`,
            height: `${item.height}%`,
            position: "absolute",
        });
        element.style.setProperty("--usl-role-color", role.color);
        element.style.setProperty("--usl-role-tint", roleTint(role.color));
        const move = document.createElement("span");
        move.className = `usl_sign_field_move fa ${this.fieldIcon(item)}`;
        move.title = _t("Move field");
        const label = document.createElement("span");
        label.className = "usl_sign_field_label";
        label.textContent = item.placeholder || item.name;
        element.append(move, label);
        if (item.required) {
            const required = document.createElement("span");
            required.className = "usl_sign_required";
            required.textContent = "*";
            required.title = _t("Required");
            element.append(required);
        }
        if (this.isEditable) {
            const resize = document.createElement("span");
            resize.className = "usl_sign_resize_handle";
            resize.title = _t("Resize field");
            element.append(resize);
            element.addEventListener("pointerdown", (event) => {
                if (event.button !== 0 || event.target.closest(".usl_sign_resize_handle")) {
                    return;
                }
                this.editor.selectedItemId = item.id;
                this.editor.contextPlacement = false;
                this.refreshSelection();
                this.startManipulation(event, item, "move");
            });
            resize.addEventListener("pointerdown", (event) => this.startManipulation(event, item, "resize"));
        }
        element.addEventListener("click", (event) => {
            event.stopPropagation();
            this.editor.selectedItemId = item.id;
            this.editor.contextPlacement = false;
            this.refreshSelection();
        });
        if (this.isEditable) {
            element.addEventListener("contextmenu", (event) => {
                event.preventDefault();
                event.stopPropagation();
                this.editor.selectedItemId = item.id;
                this.editor.contextPlacement = false;
                this.refreshSelection();
                this.runEditorAction(() => this.deleteField(item));
            });
        }
        element.addEventListener("keydown", (event) => this.onFieldKeydown(event, item));
        page.append(element);
        this.items[item.id] = element;
        this.refreshSelection();
        return element;
    }

    refreshSelection() {
        for (const [itemId, element] of Object.entries(this.items)) {
            element.classList.toggle("usl_sign_selected", Number(itemId) === this.editor.selectedItemId);
        }
    }

    startManipulation(event, item, mode) {
        if (!this.isEditable) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        this.cancelManipulation();
        const before = editableItemValues(item);
        const element = this.items[item.id];
        const page = element?.closest(".page");
        if (!page) {
            return;
        }
        const rectangle = page.getBoundingClientRect();
        const origin = {x: event.clientX, y: event.clientY};
        const pointerId = event.pointerId;
        const captureTarget = event.currentTarget;
        const iframeWindow = this.iframe.el.contentWindow;
        const iframeDocument = this.iframe.el.contentDocument;
        let active = true;
        let changed = false;

        const iframePoint = (pointerEvent) => {
            if (pointerEvent.target?.ownerDocument === iframeDocument) {
                return {x: pointerEvent.clientX, y: pointerEvent.clientY};
            }
            const iframeRectangle = this.iframe.el.getBoundingClientRect();
            return {
                x: pointerEvent.clientX - iframeRectangle.left,
                y: pointerEvent.clientY - iframeRectangle.top,
            };
        };
        const move = (pointerEvent) => {
            if (!active || pointerEvent.pointerId !== pointerId) {
                return;
            }
            pointerEvent.preventDefault();
            const point = iframePoint(pointerEvent);
            const deltaX = ((point.x - origin.x) * 100) / rectangle.width;
            const deltaY = ((point.y - origin.y) * 100) / rectangle.height;
            if (mode === "move") {
                item.position_x = clamp(before.position_x + deltaX, 0, 100 - before.width);
                item.position_y = clamp(before.position_y + deltaY, 0, 100 - before.height);
            } else {
                item.width = clamp(before.width + deltaX, 2, 100 - before.position_x);
                item.height = clamp(before.height + deltaY, 2, 100 - before.position_y);
            }
            Object.assign(this.items[item.id].style, {
                left: `${item.position_x}%`, top: `${item.position_y}%`,
                width: `${item.width}%`, height: `${item.height}%`,
            });
            changed = true;
        };
        const cleanup = () => {
            captureTarget.removeEventListener("pointermove", move);
            captureTarget.removeEventListener("pointerup", up);
            captureTarget.removeEventListener("pointercancel", cancel);
            captureTarget.removeEventListener("lostpointercapture", lostCapture);
            iframeWindow.removeEventListener("pointermove", move);
            iframeWindow.removeEventListener("pointerup", up);
            iframeWindow.removeEventListener("pointercancel", cancel);
            window.removeEventListener("pointermove", move, true);
            window.removeEventListener("pointerup", up, true);
            window.removeEventListener("pointercancel", cancel, true);
            window.removeEventListener("blur", cancel);
            if (captureTarget.hasPointerCapture?.(pointerId)) {
                captureTarget.releasePointerCapture(pointerId);
            }
            if (this.activeManipulation?.pointerId === pointerId) {
                this.activeManipulation = null;
            }
        };
        const restore = () => {
            Object.assign(item, before);
            Object.assign(element.style, {
                left: `${before.position_x}%`, top: `${before.position_y}%`,
                width: `${before.width}%`, height: `${before.height}%`,
            });
        };
        const finish = (cancelled = false) => {
            if (!active) {
                return;
            }
            active = false;
            cleanup();
            if (cancelled) {
                restore();
                return;
            }
            const after = editableItemValues(item);
            if (!changed || (
                before.position_x === after.position_x &&
                before.position_y === after.position_y &&
                before.width === after.width &&
                before.height === after.height
            )) {
                return;
            }
            const values = mode === "move"
                ? {position_x: after.position_x, position_y: after.position_y}
                : {width: after.width, height: after.height};
            const inverse = mode === "move"
                ? {position_x: before.position_x, position_y: before.position_y}
                : {width: before.width, height: before.height};
            this.updateField(item, values, inverse);
        };
        const up = (pointerEvent) => {
            if (pointerEvent.pointerId === pointerId) {
                finish(false);
            }
        };
        const cancel = (pointerEvent) => {
            if (pointerEvent.pointerId === undefined || pointerEvent.pointerId === pointerId) {
                finish(true);
            }
        };
        const lostCapture = () => finish(false);
        captureTarget.addEventListener("pointermove", move);
        captureTarget.addEventListener("pointerup", up);
        captureTarget.addEventListener("pointercancel", cancel);
        captureTarget.addEventListener("lostpointercapture", lostCapture);
        iframeWindow.addEventListener("pointermove", move);
        iframeWindow.addEventListener("pointerup", up);
        iframeWindow.addEventListener("pointercancel", cancel);
        window.addEventListener("pointermove", move, true);
        window.addEventListener("pointerup", up, true);
        window.addEventListener("pointercancel", cancel, true);
        window.addEventListener("blur", cancel);
        try {
            captureTarget.setPointerCapture?.(pointerId);
        } catch (error) {
            // Browsers may reject capture when the pointer ended while the PDF
            // iframe was reloading. Window-level listeners still complete the
            // command safely in that case.
            if (error.name !== "NotFoundError") {
                throw error;
            }
        }
        this.activeManipulation = {pointerId, cancel: () => finish(true)};
    }

    cancelManipulation() {
        const activeManipulation = this.activeManipulation;
        this.activeManipulation = null;
        if (activeManipulation && typeof activeManipulation.cancel === "function") {
            activeManipulation.cancel();
        }
    }

    onFieldKeydown(event, item) {
        if (!this.isEditable) {
            return;
        }
        if (event.key === "Delete" || event.key === "Backspace") {
            event.preventDefault();
            this.deleteField(item);
            return;
        }
        const directions = {
            ArrowLeft: [-1, 0], ArrowRight: [1, 0],
            ArrowUp: [0, -1], ArrowDown: [0, 1],
        };
        if (!directions[event.key]) {
            return;
        }
        event.preventDefault();
        const [horizontal, vertical] = directions[event.key];
        const amount = event.altKey ? 0.1 : 0.5;
        const before = editableItemValues(item);
        let values;
        let inverse;
        if (event.shiftKey) {
            values = {
                width: clamp(before.width + horizontal * amount, 2, 100 - before.position_x),
                height: clamp(before.height + vertical * amount, 2, 100 - before.position_y),
            };
            inverse = {width: before.width, height: before.height};
        } else {
            values = {
                position_x: clamp(before.position_x + horizontal * amount, 0, 100 - before.width),
                position_y: clamp(before.position_y + vertical * amount, 0, 100 - before.height),
            };
            inverse = {position_x: before.position_x, position_y: before.position_y};
        }
        this.updateField(item, values, inverse);
    }

    onPalettePointerDown(event, field) {
        if (!this.isEditable || event.button !== 0) {
            return;
        }
        event.preventDefault();
        this.cancelPaletteDrag();
        const start = {x: event.clientX, y: event.clientY};
        const button = event.currentTarget;
        const pointerId = event.pointerId;
        let dragged = false;
        let active = true;
        try {
            button.setPointerCapture?.(pointerId);
        } catch (error) {
            if (error.name !== "NotFoundError") {
                throw error;
            }
        }
        const move = (pointerEvent) => {
            if (!active || pointerEvent.pointerId !== pointerId) {
                return;
            }
            dragged ||= Math.hypot(pointerEvent.clientX - start.x, pointerEvent.clientY - start.y) > 6;
        };
        const cleanup = () => {
            button.removeEventListener("pointermove", move);
            button.removeEventListener("pointerup", up);
            button.removeEventListener("pointercancel", cancel);
            button.removeEventListener("lostpointercapture", lostCapture);
            window.removeEventListener("pointermove", move, true);
            window.removeEventListener("pointerup", up, true);
            window.removeEventListener("pointercancel", cancel, true);
            window.removeEventListener("blur", cancel);
            if (button.hasPointerCapture?.(pointerId)) {
                button.releasePointerCapture(pointerId);
            }
            if (this.activePaletteDrag?.pointerId === pointerId) {
                this.activePaletteDrag = null;
            }
        };
        const finish = (pointerEvent, cancelled = false) => {
            if (!active) {
                return;
            }
            active = false;
            cleanup();
            if (cancelled || !dragged) {
                return;
            }
            pointerEvent.preventDefault();
            const iframeRectangle = this.iframe.el.getBoundingClientRect();
            const iframeX = pointerEvent.clientX - iframeRectangle.left;
            const iframeY = pointerEvent.clientY - iframeRectangle.top;
            const pages = this.iframe.el.contentDocument.getElementsByClassName("page");
            const page = [...pages].find((candidate) => {
                const rectangle = candidate.getBoundingClientRect();
                return iframeX >= rectangle.left && iframeX <= rectangle.right &&
                    iframeY >= rectangle.top && iframeY <= rectangle.bottom;
            });
            if (!page) {
                return;
            }
            const pageRectangle = page.getBoundingClientRect();
            const placement = pointToPlacement(
                pageRectangle, iframeX, iframeY, field.default_width, field.default_height
            );
            this.editor.selectedFieldId = field.id;
            this.runEditorAction(() => this.placeField({
                field_id: field.id,
                role_id: this.editor.selectedRoleId,
                page: Number(page.dataset.pageNumber),
                ...placement,
                width: field.default_width,
                height: field.default_height,
            }));
        };
        const up = (pointerEvent) => {
            if (pointerEvent.pointerId === pointerId) {
                finish(pointerEvent);
            }
        };
        const cancel = (pointerEvent) => {
            if (pointerEvent.pointerId === undefined || pointerEvent.pointerId === pointerId) {
                finish(pointerEvent, true);
            }
        };
        const lostCapture = (pointerEvent) => finish(pointerEvent, !dragged);
        button.addEventListener("pointermove", move);
        button.addEventListener("pointerup", up);
        button.addEventListener("pointercancel", cancel);
        button.addEventListener("lostpointercapture", lostCapture);
        window.addEventListener("pointermove", move, true);
        window.addEventListener("pointerup", up, true);
        window.addEventListener("pointercancel", cancel, true);
        window.addEventListener("blur", cancel);
        this.activePaletteDrag = {pointerId, cancel: () => finish(event, true)};
    }

    cancelPaletteDrag() {
        const activeDrag = this.activePaletteDrag;
        this.activePaletteDrag = null;
        if (activeDrag && typeof activeDrag.cancel === "function") {
            activeDrag.cancel();
        }
    }

    async contextCreate() {
        const field = this.selectedField;
        if (!field || !this.editor.selectedRoleId || !this.editor.contextPlacement) {
            this.notification.add(_t("Choose a field type and signer first."), {type: "warning"});
            return;
        }
        try {
            await this.placeField({
                field_id: field.id,
                role_id: this.editor.selectedRoleId,
                ...this.editor.contextPlacement,
                width: field.default_width,
                height: field.default_height,
            });
            this.editor.contextPlacement = false;
        } catch (_error) {
            // applyCommand already reports the actionable save failure.
        }
    }

    cancelContextPlacement() {
        this.editor.contextPlacement = false;
    }

    async applyCommand(command) {
        const run = async () => {
            this.editor.pending += 1;
            this.editor.saveStatus = "saving";
            this.editor.error = false;
            try {
                const result = await this.orm.call(this.model, "editor_apply_command", [
                    [this.res_id], operationUuid(), this.info.revision, command,
                ]);
                if (result.status === "conflict") {
                    this.editor.conflict = true;
                    this.editor.error = true;
                    this.editor.saveStatus = "error";
                    this.notification.add(result.message, {type: "danger", sticky: true});
                    return result;
                }
                this.info.revision = result.revision;
                if (result.item) {
                    this.info.items[String(result.item.id)] = result.item;
                    this.renderField(result.item);
                }
                for (const item of result.items || []) {
                    this.info.items[String(item.id)] = item;
                    this.renderField(item);
                }
                const deletedIds = [
                    ...(result.deleted_id ? [result.deleted_id] : []),
                    ...(result.deleted_ids || []),
                ];
                for (const deletedId of deletedIds) {
                    delete this.info.items[String(deletedId)];
                    delete this.info.items[deletedId];
                    this.items[deletedId]?.remove();
                    delete this.items[deletedId];
                    if (this.editor.selectedItemId === deletedId) {
                        this.editor.selectedItemId = false;
                    }
                }
                if (result.roles) {
                    this.info.roles = result.roles;
                    if (!this.role(this.editor.selectedRoleId)) {
                        this.editor.selectedRoleId = this.info.roles[0]?.id || false;
                    }
                }
                this.editor.saveStatus = "saved";
                return result;
            } catch (error) {
                this.editor.error = true;
                this.editor.saveStatus = "error";
                this.notification.add(
                    error.data?.message ||
                        _t("The editor change could not be saved. The previous state was restored."),
                    {type: "danger"}
                );
                throw error;
            } finally {
                this.editor.pending -= 1;
            }
        };
        const result = this.commandQueue.then(run, run);
        this.commandQueue = result.catch(() => undefined);
        return result;
    }

    pushHistory(entry) {
        this.undoStack.push(entry);
        this.redoStack = [];
        this.editor.undoCount = this.undoStack.length;
        this.editor.redoCount = 0;
    }

    async createField(values, {recordHistory = true} = {}) {
        const result = await this.applyCommand({action: "create", values});
        if (result.status !== "ok") {
            return result;
        }
        this.editor.selectedItemId = result.item.id;
        this.refreshSelection();
        if (recordHistory) {
            this.pushHistory({kind: "create", itemId: result.item.id, values: editableItemValues(result.item)});
        }
        return result;
    }

    placeField(values) {
        if (this.canPlaceOnEveryPage && this.editor.placeOnEveryPage) {
            return this.createFieldsOnEveryPage(values);
        }
        return this.createField(values);
    }

    async createFieldsOnEveryPage(values, {recordHistory = true} = {}) {
        const result = await this.applyCommand({action: "create_all_pages", values});
        if (result.status !== "ok") {
            return result;
        }
        const placedItems = result.items || [];
        const selected = placedItems.find((item) => item.page === values.page) || placedItems[0];
        this.editor.selectedItemId = selected?.id || false;
        this.refreshSelection();
        if (recordHistory) {
            this.pushHistory({
                kind: "create_many",
                itemIds: placedItems.map((item) => item.id),
                values,
            });
        }
        return result;
    }

    async copySelectedFieldToEveryPage() {
        const source = this.selectedItem;
        if (!source || this.field(source.field_id)?.kind !== "initials") {
            return;
        }
        const result = await this.applyCommand({
            action: "copy_all_pages",
            item_id: source.id,
        });
        const itemIds = (result.items || []).map((item) => item.id);
        this.editor.selectedItemId = source.id;
        this.refreshSelection();
        if (!itemIds.length) {
            this.notification.add(_t("These initials are already placed on every page."), {
                type: "info",
            });
            return;
        }
        this.pushHistory({kind: "copy_all_pages", itemIds, sourceItemId: source.id});
        this.notification.add(_t("Initials placed on every page."), {type: "success"});
    }

    async updateField(item, values, inverseValues = false, {recordHistory = true} = {}) {
        const before = inverseValues || Object.fromEntries(
            Object.keys(values).map((key) => [key, item[key]])
        );
        Object.assign(item, values);
        this.renderField(item);
        try {
            const result = await this.applyCommand({action: "update", item_id: item.id, values});
            if (result.status !== "ok") {
                Object.assign(item, before);
                this.renderField(item);
                return result;
            }
            if (recordHistory) {
                this.pushHistory({kind: "update", itemId: item.id, before, after: values});
            }
            return result;
        } catch (error) {
            Object.assign(item, before);
            this.renderField(item);
            throw error;
        }
    }

    async deleteField(item, {recordHistory = true} = {}) {
        const snapshot = editableItemValues(item);
        const result = await this.applyCommand({action: "delete", item_id: item.id});
        if (result.status === "ok" && recordHistory) {
            this.pushHistory({kind: "delete", itemId: item.id, values: snapshot});
        }
        return result;
    }

    async deleteFields(itemIds) {
        return this.applyCommand({action: "delete_many", item_ids: itemIds});
    }

    async undo() {
        if (!this.isEditable || !this.undoStack.length) {
            return;
        }
        const entry = this.undoStack.pop();
        try {
            let result;
            if (entry.kind === "update") {
                const item = this.info.items[String(entry.itemId)];
                result = await this.updateField(item, entry.before, entry.after, {recordHistory: false});
            } else if (entry.kind === "create") {
                result = await this.deleteField(this.info.items[String(entry.itemId)], {recordHistory: false});
            } else if (entry.kind === "create_many" || entry.kind === "copy_all_pages") {
                result = await this.deleteFields(entry.itemIds);
            } else if (entry.kind === "delete") {
                result = await this.createField(entry.values, {recordHistory: false});
                if (result.status === "ok") {
                    entry.itemId = result.item.id;
                }
            }
            if (result?.status !== "ok") {
                this.undoStack.push(entry);
                return;
            }
        } catch (error) {
            this.undoStack.push(entry);
            throw error;
        }
        this.redoStack.push(entry);
        this.editor.undoCount = this.undoStack.length;
        this.editor.redoCount = this.redoStack.length;
    }

    async redo() {
        if (!this.isEditable || !this.redoStack.length) {
            return;
        }
        const entry = this.redoStack.pop();
        try {
            let result;
            if (entry.kind === "update") {
                const item = this.info.items[String(entry.itemId)];
                result = await this.updateField(item, entry.after, entry.before, {recordHistory: false});
            } else if (entry.kind === "create") {
                result = await this.createField(entry.values, {recordHistory: false});
                if (result.status === "ok") {
                    entry.itemId = result.item.id;
                }
            } else if (entry.kind === "create_many") {
                result = await this.createFieldsOnEveryPage(
                    entry.values,
                    {recordHistory: false}
                );
                if (result.status === "ok") {
                    entry.itemIds = result.items.map((item) => item.id);
                }
            } else if (entry.kind === "copy_all_pages") {
                result = await this.applyCommand({
                    action: "copy_all_pages",
                    item_id: entry.sourceItemId,
                });
                if (result?.status === "ok") {
                    entry.itemIds = result.items.map((item) => item.id);
                }
            } else if (entry.kind === "delete") {
                result = await this.deleteField(this.info.items[String(entry.itemId)], {recordHistory: false});
            }
            if (result?.status !== "ok") {
                this.redoStack.push(entry);
                return;
            }
        } catch (error) {
            this.redoStack.push(entry);
            throw error;
        }
        this.undoStack.push(entry);
        this.editor.undoCount = this.undoStack.length;
        this.editor.redoCount = this.redoStack.length;
    }

    changeItemField(event) {
        const field = this.field(Number(event.target.value));
        this.updateField(this.selectedItem, {field_id: field.id});
    }

    changeItemRole(event) {
        this.updateField(this.selectedItem, {role_id: Number(event.target.value)});
    }

    changeItemRequired(event) {
        this.updateField(this.selectedItem, {required: event.target.checked});
    }

    changeItemPlaceholder(event) {
        this.updateField(this.selectedItem, {placeholder: event.target.value});
    }

    changeItemNumber(event) {
        const name = event.target.name;
        const value = Number(event.target.value);
        if (!EDITOR_VALUES.includes(name) || !Number.isFinite(value)) {
            return;
        }
        this.updateField(this.selectedItem, {[name]: value});
    }

    async deleteSelectedField() {
        if (this.selectedItem) {
            try {
                await this.deleteField(this.selectedItem);
            } catch (_error) {
                // applyCommand already reports the actionable save failure.
            }
        }
    }

    async addRole() {
        const name = this.editor.newRoleName.trim();
        if (!name) {
            this.notification.add(_t("Enter a signer role name."), {type: "warning"});
            return;
        }
        const result = await this.applyCommand({action: "role_add", values: {name}});
        if (result.status === "ok") {
            this.editor.newRoleName = "";
            this.editor.selectedRoleId = result.role_id;
        }
    }

    async removeRole(event) {
        const roleId = Number(event.currentTarget.dataset.roleId);
        const result = await this.applyCommand({action: "role_remove", role_id: roleId});
        if (result.status === "ok" && this.editor.selectedRoleId === roleId) {
            this.editor.selectedRoleId = this.info.roles[0]?.id || false;
        }
    }

    reloadEditor() {
        window.location.reload();
    }
}

UslSignTemplateEditor.template = "usl_sign.SignTemplateEditor";

registry.category("actions").add("usl_sign_template_configure", UslSignTemplateEditor, {force: true});
registry.category("actions").add("usl_sign_request_configure", UslSignTemplateEditor, {force: true});
