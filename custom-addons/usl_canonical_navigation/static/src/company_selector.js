import { router } from "@web/core/browser/router";
import { user, userBus } from "@web/core/user";
import { useBus } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import {
    CompanySelector,
    SwitchCompanyMenu,
} from "@web/webclient/switch_company_menu/switch_company_menu";
import {
    NAVIGATION_VERSION,
    companyTransitionPatch,
    writePortableRoute,
} from "./navigation_state";

patch(CompanySelector.prototype, {
    async apply() {
        if (Number(router.current.nv) !== NAVIGATION_VERSION) {
            return super.apply(...arguments);
        }
        const nextCompanyIds = this.selectedCompaniesIds.length
            ? [...this.selectedCompaniesIds]
            : [user.activeCompany.id];
        if (nextCompanyIds.join(",") === user.activeCompanies.map(({ id }) => id).join(",")) {
            return;
        }
        // Commit the semantic company transition while the old URL is still
        // the current history entry. The subsequent action remount is a
        // replacement, so it cannot create a duplicate company entry.
        writePortableRoute(() =>
            router.pushState(
                companyTransitionPatch(router.current, nextCompanyIds),
                { sync: true }
            )
        );
        await user.activateCompanies(nextCompanyIds, {
            includeChildCompanies: false,
            reload: false,
        });

        const controller = this.actionService.currentController;
        if (
            controller?.props.resId &&
            controller?.props.resModel &&
            !(await user.checkAccessRight(
                controller.props.resModel,
                "read",
                controller.props.resId
            ))
        ) {
            router.replaceState(
                { actionStack: router.current.actionStack?.slice(0, -1) || [] },
                { sync: true }
            );
        }
        await this.actionService.loadState(router.current);
    },
});

patch(SwitchCompanyMenu.prototype, {
    setup() {
        super.setup(...arguments);
        useBus(userBus, "ACTIVE_COMPANIES_CHANGED", () => this.render());
    },
});
