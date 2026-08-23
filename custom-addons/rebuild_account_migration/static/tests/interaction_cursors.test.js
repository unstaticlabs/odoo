import { expect, getFixture, test } from "@odoo/hoot";

test("actionable and disabled elements expose the expected pointer affordance", () => {
    const fixture = getFixture();
    fixture.classList.add("o_web_client");
    fixture.innerHTML = `
        <button id="action">Review</button>
        <button id="cash-amount" class="btn btn-link o_usl_cash_amount_link">
            €42,000
        </button>
        <summary id="details">View estimate details</summary>
        <div id="link-role" role="link">Open source entry</div>
        <span id="menu" class="o_menu_item">Reporting</span>
        <article id="kanban-record" class="o_kanban_record">Bank journal</article>
        <input id="checkbox" type="checkbox"/>
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
                    <tr class="o_data_row o_selected_row">
                        <td id="editing-cell" class="o_data_cell">Editing</td>
                    </tr>
                </tbody>
            </table>
        </div>
    `;

    expect("#action").toHaveStyle({ cursor: "pointer" });
    expect("#cash-amount").toHaveStyle({ cursor: "pointer" });
    expect("#details").toHaveStyle({ cursor: "pointer" });
    expect("#link-role").toHaveStyle({ cursor: "pointer" });
    expect("#menu").toHaveStyle({ cursor: "pointer" });
    expect("#kanban-record").toHaveStyle({ cursor: "pointer" });
    expect("#checkbox").toHaveStyle({ cursor: "pointer" });
    expect("#record-cell").toHaveStyle({ cursor: "pointer" });
    expect("#disabled").toHaveStyle({ cursor: "not-allowed" });
    expect("#text-input").toHaveStyle({ cursor: "text" });
    expect("#static-cell").not.toHaveStyle({ cursor: "pointer" });
    expect("#editing-cell").toHaveStyle({ cursor: "default" });
});
