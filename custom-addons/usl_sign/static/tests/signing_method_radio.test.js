import {expect, test} from "@odoo/hoot";
import {animationFrame} from "@odoo/hoot-mock";
import {defineMailModels} from "@mail/../tests/mail_test_helpers";
import {reactive} from "@odoo/owl";
import {contains, mountWithCleanup} from "@web/../tests/web_test_helpers";

import {SigningMethodRadio} from "../src/js/signing_method_radio.esm";

defineMailModels();

test("signing methods render as comparable selectable columns", async () => {
    const record = reactive({
        data: {requested_trust: "standard"},
        fields: {
            requested_trust: {
                type: "selection",
                selection: [
                    ["standard", "Standard"],
                    ["strong_personal", "Strong personal"],
                    ["qualified_external", "Qualified external"],
                ],
            },
        },
        update(values) {
            Object.assign(this.data, values);
        },
    });
    await mountWithCleanup(SigningMethodRadio, {
        props: {
            name: "requested_trust",
            record,
            orientation: "horizontal",
            label: "Signing method",
        },
    });

    expect(".o_radio_item.is-selected").toHaveCount(1);
    expect(".o_radio_item.is-selected").toHaveText(/Standard/);
    expect(".usl_sign_method_card").toHaveCount(3);
    expect(".usl_sign_method_card .usl_sign_method_card_radio").toHaveCount(3);
    expect(".usl_sign_method_card .usl_sign_method_card_icon").toHaveCount(3);
    expect(".usl_sign_method_card .usl_sign_method_facts").toHaveCount(3);
    expect(".usl_sign_method_card .usl_sign_method_select").toHaveCount(3);
    expect(".usl_sign_method_card:nth-child(2) .usl_sign_method_recommendation").toHaveText(
        "Recommended"
    );
    expect(".usl_sign_method_card").toHaveText(/Signer check/);
    expect(".usl_sign_method_card").toHaveText(/Signed PDF/);
    expect(".usl_sign_method_card").toHaveText(/Proof kept/);
    expect(".o_radio_item.is-selected .usl_sign_method_select").toHaveText("Selected");

    await contains('.o_radio_input[data-value="strong_personal"]').click();
    await animationFrame();

    expect(".o_radio_item.is-selected").toHaveCount(1);
    expect(".o_radio_item.is-selected").toHaveText(/Strong/);
    expect(".o_radio_item.is-selected").toHaveText(/Reviewed identity and Pocket ID passkey/);
    expect(".o_radio_item.is-selected").toHaveText(/Personal PAdES per signer/);
    expect(".o_radio_item.is-selected .usl_sign_method_select").toHaveText("Selected");
    expect(".o_radio_item:not(.is-selected) .usl_sign_method_select").toHaveText(
        /Choose/
    );
});
