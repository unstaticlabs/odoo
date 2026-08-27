/** @odoo-module **/

import {Component, onMounted, onWillUnmount, useRef, useState} from "@odoo/owl";
import {registry} from "@web/core/registry";
import {standardFieldProps} from "@web/views/fields/standard_field_props";
import {DocumentPreview} from "@usl_documents/documents_app";

export class SignDocumentPreviewField extends Component {
    static template = "usl_sign.SignDocumentPreviewField";
    static components = {DocumentPreview};
    static props = {...standardFieldProps};

    get previewUrl() {
        return String(this.props.record.data[this.props.name] || "");
    }
}

export class SignDocumentCardPreviewField extends SignDocumentPreviewField {
    static template = "usl_sign.SignDocumentCardPreviewField";

    setup() {
        this.root = useRef("root");
        this.state = useState({visible: false});
        this.observer = null;
        onMounted(() => {
            if (!globalThis.IntersectionObserver) {
                this.state.visible = true;
                return;
            }
            this.observer = new IntersectionObserver(
                (entries) => {
                    if (!entries.some((entry) => entry.isIntersecting)) {
                        return;
                    }
                    this.state.visible = true;
                    this.observer?.disconnect();
                    this.observer = null;
                },
                {rootMargin: "240px"}
            );
            this.observer.observe(this.root.el);
        });
        onWillUnmount(() => this.observer?.disconnect());
    }

    get thumbnailUrl() {
        return String(this.props.record.data.document_thumbnail_url || "");
    }
}

registry.category("fields").add("usl_sign_document_preview", {
    component: SignDocumentPreviewField,
    supportedTypes: ["char"],
});
registry.category("fields").add("usl_sign_document_card_preview", {
    component: SignDocumentCardPreviewField,
    supportedTypes: ["char"],
});
