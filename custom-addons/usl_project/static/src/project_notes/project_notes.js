/** @odoo-module **/

import { Component, markup, onMounted, onWillUnmount, onWillUpdateProps, useExternalListener, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

export class ProjectNotes extends Component {
    static template = "usl_project.ProjectNotes";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ data: { status: "loading" } });
        this.sequence = 0;
        onMounted(() => {
            this.load();
            this.interval = browser.setInterval(() => {
                if (!document.hidden && !this.pending) {
                    this.load(this.path, { background: true });
                }
            }, 60000);
        });
        onWillUpdateProps((next) => {
            if (next.record.resId !== this.props.record.resId) {
                this.load(null, { projectId: next.record.resId });
            }
        });
        useExternalListener(document, "visibilitychange", () => {
            if (document.hidden) {
                this.sequence++;
                this.pending = false;
                this.state.data = { status: "loading" };
            } else {
                this.load(this.path);
            }
        });
        onWillUnmount(() => {
            this.sequence++;
            browser.clearInterval(this.interval);
        });
    }

    async load(path = null, { background = false, projectId = this.props.record.resId } = {}) {
        const sequence = ++this.sequence;
        this.path = path;
        this.pending = true;
        if (!background) {
            this.state.data = { status: "loading" };
        }
        // A delayed recheck must not leave a previously readable note on screen indefinitely.
        const deadline = browser.setTimeout(() => {
            if (sequence === this.sequence) {
                this.sequence++;
                this.pending = false;
                this.state.data = { status: "unavailable" };
            }
        }, 10000);
        try {
            const result = await this.orm.call("project.project", "get_team_notes", [[projectId], path]);
            if (sequence === this.sequence) {
                this.path = result.path || null;
                this.state.data = result;
            }
        } catch (error) {
            if (sequence === this.sequence) {
                const denied = ["odoo.exceptions.AccessError", "odoo.exceptions.MissingError"].includes(error.data?.name);
                this.state.data = { status: denied ? "denied" : "unavailable" };
            }
        } finally {
            browser.clearTimeout(deadline);
            if (sequence === this.sequence) {
                this.pending = false;
            }
        }
    }

    get content() {
        return markup(this.state.data.html || "");
    }

    followLink(event) {
        const link = event.target.closest("a[data-project-note]");
        if (link) {
            event.preventDefault();
            this.load(link.dataset.projectNote);
        }
    }
}

registry.category("view_widgets").add("usl_project_notes", { component: ProjectNotes });
