import { Chatter } from "@mail/chatter/web_portal_project/chatter";
import { AttachmentList } from "@mail/core/common/attachment_list";
import { user } from "@web/core/user";
import { patch } from "@web/core/utils/patch";
import { onWillStart, useState } from "@odoo/owl";

function isAccountingEvidenceModel(modelName) {
    return (
        modelName === "hr.expense" ||
        modelName?.startsWith("account.") ||
        modelName?.startsWith("rebuild.account.")
    );
}

patch(Chatter.prototype, {
    setup() {
        super.setup();
        this.uslAccountingReview = useState({ reviewer: false });
        onWillStart(async () => {
            this.uslAccountingReview.reviewer =
                (await user.hasGroup(
                    "rebuild_account_migration.group_rebuild_accountant_reviewer"
                )) && !(await user.hasGroup("account.group_account_user"));
        });
    },

    get isReadonlyAccountingChatter() {
        return (
            this.uslAccountingReview.reviewer &&
            isAccountingEvidenceModel(this.props.threadModel)
        );
    },
});

patch(AttachmentList.prototype, {
    setup() {
        super.setup();
        this.uslAccountingReview = useState({ reviewer: false });
        onWillStart(async () => {
            this.uslAccountingReview.reviewer =
                (await user.hasGroup(
                    "rebuild_account_migration.group_rebuild_accountant_reviewer"
                )) && !(await user.hasGroup("account.group_account_user"));
        });
    },

    showDelete(attachment) {
        const chatterModel = this.env.inChatter?.thread?.model;
        if (
            this.uslAccountingReview.reviewer &&
            isAccountingEvidenceModel(chatterModel) &&
            !this.env.inComposer
        ) {
            return false;
        }
        return super.showDelete(attachment);
    },
});
