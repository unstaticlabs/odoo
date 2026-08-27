import {expect, test} from "@odoo/hoot";
import {animationFrame} from "@odoo/hoot-mock";
import {defineMailModels} from "@mail/../tests/mail_test_helpers";
import {reactive} from "@odoo/owl";
import {contains, mountWithCleanup} from "@web/../tests/web_test_helpers";

import {SigningMethodRadio} from "../src/js/signing_method_radio.esm";

defineMailModels();

test("selected signing method is visible without relying on :has()", async () => {
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

    await contains('.o_radio_input[data-value="strong_personal"]').click();
    await animationFrame();

    expect(".o_radio_item.is-selected").toHaveCount(1);
    expect(".o_radio_item.is-selected").toHaveText(/Strong personal/);
    expect(".o_radio_item.is-selected").toHaveText(/Approved identity and Pocket ID passkey/);
    expect(".o_radio_item.is-selected").toHaveText(/personal PAdES signature per signer/);
});
