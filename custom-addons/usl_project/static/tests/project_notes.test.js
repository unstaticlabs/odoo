import { expect, test } from "@odoo/hoot";
import { advanceTime, animationFrame } from "@odoo/hoot-mock";
import { contains, defineModels, fields, makeServerError, models, mountWithCleanup, onRpc } from "@web/../tests/web_test_helpers";
import { ProjectNotes } from "../src/project_notes/project_notes";

class Project extends models.Model {
    _name = "project.project";
    name = fields.Char();
}
defineModels([Project]);

const notes = [{ path: "Guide.md", title: "Guide" }, { path: "Review.md", title: "Review" }];
function ready(path, html) {
    return { status: "ready", path, notes, html, obsidian_url: "obsidian://open?vault=Demo&file=" + path };
}
function mountNotes() {
    return mountWithCleanup(ProjectNotes, { props: { record: { resId: 7 } } });
}

test("a reader follows a note link and loses the open note when project access ends", async () => {
    const paths = [];
    let canRead = true;
    onRpc("project.project", "get_team_notes", ({ args }) => {
        paths.push(args[1]);
        if (!canRead) {
            throw makeServerError({ type: "AccessError" });
        }
        return args[1] === "Review.md" ? ready("Review.md", "<h2>Collect feedback</h2>")
            : ready("Guide.md", '<p><a href="#Review.md" data-project-note="Review.md">Checklist</a></p>');
    });
    await mountNotes();
    await contains("article a", { text: "Checklist" }).click();
    expect("article h2").toHaveText("Collect feedback");
    expect(paths).toEqual([null, "Review.md"]);
    expect(".o_usl_project_notes_tools a").toHaveAttribute("href", /Review.md/);
    canRead = false;
    await contains('button[aria-label="Refresh notes"]').click();
    await animationFrame();
    expect("article, .o_usl_project_notes_tools").toHaveCount(0);
    expect(".o_usl_project_notes [role=status]").toHaveText(/no longer have access/);
});

test("a stalled refresh clears the previous note and ignores its late reply", async () => {
    let finish;
    let first = true;
    onRpc("project.project", "get_team_notes", () => {
        if (first) {
            first = false;
            return ready("Guide.md", "<p>Project guide</p>");
        }
        return new Promise(resolve => { finish = resolve; });
    });
    await mountNotes();
    expect("article").toHaveText("Project guide");
    await advanceTime(60000);
    await advanceTime(10500);
    await animationFrame();
    expect("article").toHaveCount(0);
    expect(".o_usl_project_notes [role=status]").toHaveText(/unavailable/);
    finish(ready("Guide.md", "<p>Outdated guide</p>"));
    await animationFrame();
    expect("article").toHaveCount(0);
});
