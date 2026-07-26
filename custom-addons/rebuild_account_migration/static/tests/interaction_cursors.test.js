import { expect, getFixture, test } from "@odoo/hoot";

test("actionable and disabled elements expose the expected pointer affordance", () => {
    const fixture = getFixture();
    fixture.classList.add("o_web_client");
    fixture.innerHTML = `
        <button id="action">Review</button>
        <span id="menu" class="o_menu_item">Reporting</span>
        <button id="disabled" disabled>Post</button>
        <input id="text-input" type="text"/>
        <div class="o_list_view">
            <table>
                <tbody>
                    <tr class="o_data_row">
                        <td id="record-cell" class="o_data_cell">BILL/2026/001</td>
                    </tr>
                    <tr class="o_data_row o_list_no_open">
                        <td id="static-cell" class="o_data_cell">Total</td>
                    </tr>
                </tbody>
            </table>
        </div>
    `;

    expect("#action").toHaveStyle({ cursor: "pointer" });
    expect("#menu").toHaveStyle({ cursor: "pointer" });
    expect("#record-cell").toHaveStyle({ cursor: "pointer" });
    expect("#disabled").toHaveStyle({ cursor: "not-allowed" });
    expect("#text-input").toHaveStyle({ cursor: "text" });
    expect("#static-cell").not.toHaveStyle({ cursor: "pointer" });
});
