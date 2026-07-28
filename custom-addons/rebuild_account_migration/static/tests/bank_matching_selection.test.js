import {expect, getFixture, test} from "@odoo/hoot";

test("the opened Bank Matching line has a light-grey background", () => {
    const fixture = getFixture();
    fixture.innerHTML = `
        <main class="o_account_reconcile_oca">
            <div class="o_kanban_renderer o_account_reconcile_oca_selector">
                <article
                    id="opened-line"
                    class="o_kanban_record o_kanban_record_reconcile_oca_selected"
                >
                    Opened transaction
                </article>
                <article id="other-line" class="o_kanban_record">
                    Other transaction
                </article>
            </div>
        </main>
    `;

    expect("#opened-line").toHaveStyle({
        backgroundColor: "rgb(233, 236, 239)",
    });
    expect("#other-line").not.toHaveStyle({
        backgroundColor: "rgb(233, 236, 239)",
    });
});
