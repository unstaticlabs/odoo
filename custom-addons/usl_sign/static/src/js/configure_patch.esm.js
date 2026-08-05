/** @odoo-module **/

import {patch} from "@web/core/utils/patch";
import {registry} from "@web/core/registry";
import {useService} from "@web/core/utils/hooks";
import {useState} from "@odoo/owl";
import SignOcaConfigure from "@sign_oca/components/sign_oca_configure/sign_oca_configure.esm";

const ROLE_COLORS = ["#7c3aed", "#0284c7", "#059669", "#d97706", "#dc2626", "#0891b2", "#9333ea", "#4d7c0f"];

patch(SignOcaConfigure.prototype, {
    setup() {
        const routeModels = {
            usl_sign_request_configure: "sign.oca.request",
            usl_sign_template_configure: "sign.oca.template",
        };
        const routeTag =
            this.props.action.tag || window.location.pathname.split("/").at(-1);
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
        this.uslEditor = useState({
            loading: true,
            error: false,
            selectedRoleId: false,
            previewRoleId: false,
        });
    },

    async willStart() {
        await super.willStart(...arguments);
        this.props.action.name = this.info.name;
        // Reloaded client actions are reconstructed from the URL without an
        // ``ir.actions.client`` record, so the action service initially calls
        // them "Unnamed". Update the controller display name once the model
        // metadata has loaded.
        this.env.config.setDisplayName?.(this.info.name);
        this.uslEditor.selectedRoleId = this.info.roles[0]?.id || false;
    },

    roleColor(roleId) {
        const index = Math.max(0, this.info.roles.findIndex((role) => role.id === roleId));
        return ROLE_COLORS[index % ROLE_COLORS.length];
    },

    roleName(roleId) {
        return this.info.roles.find((role) => role.id === roleId)?.name || "Unassigned role";
    },

    roleButtonStyle(roleId) {
        const color = this.roleColor(roleId);
        return `--usl-role-color:${color};border-color:${color}`;
    },

    selectRole(event) {
        this.uslEditor.selectedRoleId = Number(event.currentTarget.dataset.roleId);
    },

    setRolePreview(event) {
        this.uslEditor.previewRoleId = Number(event.currentTarget.dataset.roleId) || false;
        this.applyRolePreview();
    },

    applyRolePreview() {
        const preview = this.uslEditor.previewRoleId;
        for (const element of Object.values(this.items)) {
            const matches = !preview || Number(element.dataset.roleId) === preview;
            element.classList.toggle("usl_sign_role_dimmed", !matches);
            element.style.opacity = matches ? "1" : "0.16";
        }
    },

    pdfApplication() {
        return this.iframe.el?.contentWindow?.PDFViewerApplication;
    },

    previousPage() {
        const viewer = this.pdfApplication()?.pdfViewer;
        if (viewer) {
            viewer.currentPageNumber = Math.max(1, viewer.currentPageNumber - 1);
        }
    },

    nextPage() {
        const viewer = this.pdfApplication()?.pdfViewer;
        if (viewer) {
            viewer.currentPageNumber = Math.min(viewer.pagesCount, viewer.currentPageNumber + 1);
        }
    },

    zoomIn() {
        this.pdfApplication()?.zoomIn();
    },

    zoomOut() {
        this.pdfApplication()?.zoomOut();
    },

    showThumbnails() {
        const sidebar = this.pdfApplication()?.pdfSidebar;
        if (sidebar) {
            sidebar.switchView(1);
            sidebar.open();
        }
    },

    onPaletteDragStart(event, field) {
        event.dataTransfer.effectAllowed = "copy";
        event.dataTransfer.setData("application/x-usl-sign-field", String(field.id));
    },

    postIframeFields() {
        super.postIframeFields(...arguments);
        this.uslEditor.loading = false;
        const iframeDocument = this.iframe.el.contentDocument;
        if (!iframeDocument.getElementById("usl-sign-editor-style")) {
            const style = iframeDocument.createElement("style");
            style.id = "usl-sign-editor-style";
            style.textContent = ".o_sign_oca_field:focus{outline:3px solid rgba(113,75,103,.35)}";
            iframeDocument.head.append(style);
        }
        for (const page of this.iframe.el.contentDocument.getElementsByClassName("page")) {
            if (page.dataset.uslDropReady) {
                continue;
            }
            page.dataset.uslDropReady = "1";
            page.addEventListener("dragover", (event) => {
                if (event.dataTransfer.types.includes("application/x-usl-sign-field")) {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "copy";
                }
            });
            page.addEventListener("drop", async (event) => {
                const fieldId = Number(
                    event.dataTransfer.getData("application/x-usl-sign-field")
                );
                if (!fieldId) {
                    return;
                }
                event.preventDefault();
                event.stopImmediatePropagation();
                const rectangle = page.getBoundingClientRect();
                const left = Math.round((((event.clientX - rectangle.left) * 100) / rectangle.width) * 2) / 2;
                const top = Math.round((((event.clientY - rectangle.top) * 100) / rectangle.height) * 2) / 2;
                try {
                    const item = await this.orm.call(this.model, "add_item", [
                        [this.res_id],
                        {
                            field_id: fieldId,
                            role_id: this.uslEditor.selectedRoleId,
                            page: Number(page.dataset.pageNumber),
                            position_x: Math.max(0, Math.min(80, left)),
                            position_y: Math.max(0, Math.min(96, top)),
                            width: 20,
                            height: 4,
                        },
                    ]);
                    this.info.items[item.id] = item;
                    this.postIframeField(item);
                } catch {
                    this.uslEditor.error = true;
                    this.notification.add("The field could not be placed. Check access and retry.", {type: "danger"});
                }
            }, true);
        }
        this.applyRolePreview();
    },

    postIframeField(item) {
        const element = super.postIframeField(...arguments);
        element[0].dataset.roleId = item.role_id;
        element[0].style.setProperty("--usl-role-color", this.roleColor(item.role_id));
        element[0].style.borderColor = this.roleColor(item.role_id);
        element[0].setAttribute("role", "button");
        element[0].setAttribute(
            "aria-label",
            `${item.name}, ${this.roleName(item.role_id)}${item.required ? ", required" : ", optional"}`
        );
        element[0].title = `${this.roleName(item.role_id)} • ${item.required ? "Required" : "Optional"}`;
        element[0].tabIndex = 0;
        element[0].addEventListener("keydown", async (event) => {
            if (event.key === "Delete" || event.key === "Backspace") {
                event.preventDefault();
                await this.orm.call(this.model, "delete_item", [[this.res_id], item.id]);
                delete this.info.items[item.id];
                element[0].remove();
                return;
            }
            const directions = {
                ArrowLeft: [-1, 0],
                ArrowRight: [1, 0],
                ArrowUp: [0, -1],
                ArrowDown: [0, 1],
            };
            if (!directions[event.key]) {
                return;
            }
            event.preventDefault();
            const amount = event.altKey ? 0.1 : 0.5;
            const [horizontal, vertical] = directions[event.key];
            item.position_x = Number(item.position_x);
            item.position_y = Number(item.position_y);
            item.width = Number(item.width);
            item.height = Number(item.height);
            const updates = {};
            if (event.shiftKey) {
                updates.width = Math.max(1, Math.min(100 - item.position_x, item.width + horizontal * amount));
                updates.height = Math.max(1, Math.min(100 - item.position_y, item.height + vertical * amount));
            } else {
                updates.position_x = Math.max(0, Math.min(100 - item.width, item.position_x + horizontal * amount));
                updates.position_y = Math.max(0, Math.min(100 - item.height, item.position_y + vertical * amount));
            }
            Object.assign(item, updates);
            Object.assign(element[0].style, {
                left: `${item.position_x}%`,
                top: `${item.position_y}%`,
                width: `${item.width}%`,
                height: `${item.height}%`,
            });
            await this.orm.call(this.model, "set_item_data", [[this.res_id], item.id, updates]);
        });
        this.applyRolePreview();
        return element;
    },
});

registry.category("actions").add("usl_sign_template_configure", SignOcaConfigure);
registry.category("actions").add("usl_sign_request_configure", SignOcaConfigure);
