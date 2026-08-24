/** @odoo-module **/

import {useRef, useState} from "@odoo/owl";
import {_t} from "@web/core/l10n/translation";
import {useDropzone} from "@web/core/dropzone/dropzone_hook";
import {registry} from "@web/core/registry";
import {useService} from "@web/core/utils/hooks";
import {getDataURLFromFile} from "@web/core/utils/urls";
import {KanbanController} from "@web/views/kanban/kanban_controller";
import {kanbanView} from "@web/views/kanban/kanban_view";

const MAX_DOCUMENTS = 20;
const MAX_ENVELOPE_BYTES = 50 * 1024 * 1024;

export class SignTemplateKanbanController extends KanbanController {
    setup() {
        super.setup(...arguments);
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.uploadInput = useRef("uploadInput");
        this.rootRef = useRef("root");
        this.uploadState = useState({uploading: false});
        useDropzone(
            this.rootRef,
            (event) => this.uploadFiles(event.dataTransfer?.files || []),
            "usl-sign-template-dropzone",
            () => this.canCreate && !this.uploadState.uploading,
        );
    }

    async createRecord() {
        if (!this.uploadState.uploading) {
            this.uploadInput.el?.click();
        }
    }

    async onInputChange(event) {
        const files = event.target.files || [];
        await this.uploadFiles(files);
        event.target.value = "";
    }

    async uploadFiles(fileList) {
        const files = [...fileList];
        if (!files.length || this.uploadState.uploading) {
            return;
        }
        if (files.length > MAX_DOCUMENTS) {
            this.notification.add(_t("Choose no more than twenty PDF documents."), {
                type: "danger",
            });
            return;
        }
        if (files.some((file) => !file.name.toLowerCase().endsWith(".pdf"))) {
            this.notification.add(_t("Every template document must be a PDF."), {
                type: "danger",
            });
            return;
        }
        if (files.reduce((total, file) => total + file.size, 0) > MAX_ENVELOPE_BYTES) {
            this.notification.add(_t("The selected PDF envelope is larger than 50 MB."), {
                type: "danger",
            });
            return;
        }
        this.uploadState.uploading = true;
        try {
            const documents = [];
            for (const file of files) {
                const dataUrl = await getDataURLFromFile(file);
                documents.push({name: file.name, data: dataUrl.split(",")[1]});
            }
            const action = await this.orm.call(
                "sign.oca.template",
                "create_from_documents",
                [],
                {
                    documents,
                    operation_uuid: globalThis.crypto.randomUUID(),
                },
            );
            await this.actionService.doAction(action);
        } catch (error) {
            this.notification.add(
                error.data?.message || error.message || _t("The template could not be created."),
                {type: "danger"},
            );
        } finally {
            this.uploadState.uploading = false;
        }
    }

}

registry.category("views").add("usl_sign_template_kanban", {
    ...kanbanView,
    Controller: SignTemplateKanbanController,
    buttonTemplate: "usl_sign.TemplateKanban.Buttons",
});
