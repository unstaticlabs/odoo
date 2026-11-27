import { Component, onMounted, useRef } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { router } from "@web/core/browser/router";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { url } from "@web/core/utils/urls";
import { ControlPanel } from "@web/search/control_panel/control_panel";
import { patch } from "@web/core/utils/patch";

class ManualRecordLink extends Component {
    static template = "usl_home.ManualRecordLink";
    static components = { Dialog };
    static props = { link: String, close: Function };

    setup() {
        this.input = useRef("link");
        this.title = _t("Copy link");
        onMounted(() => {
            this.input.el.focus();
            this.input.el.select();
        });
    }
}

class CopyRecordLink extends Component {
    static template = "usl_home.CopyRecordLink";
    static props = { resModel: String, resId: Number };

    setup() {
        this.notification = useService("notification");
        this.dialog = useService("dialog");
    }

    async copyLink() {
        const link = url(router.stateToUrl({ model: this.props.resModel, resId: this.props.resId }));
        try {
            await browser.navigator.clipboard.writeText(link);
        } catch {
            this.dialog.add(ManualRecordLink, { link });
            return;
        }
        this.notification.add(_t("Link copied"), { type: "success" });
    }
}

patch(ControlPanel.prototype, {
    get recordLinkComponent() {
        return CopyRecordLink;
    },
});
