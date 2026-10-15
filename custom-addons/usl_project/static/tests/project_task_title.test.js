import { expect, test } from "@odoo/hoot";

import { setProjectTaskDisplayName } from "../src/project_task_title";

test("direct project task routes use the project name as their display name", async () => {
    const displayNames = [];

    await setProjectTaskDisplayName({
        context: { default_project_id: 42 },
        config: {
            setDisplayName(name) {
                displayNames.push(name);
            },
        },
        orm: {
            read(model, ids, fields) {
                expect(model).toBe("project.project");
                expect(ids).toEqual([42]);
                expect(fields).toEqual(["display_name"]);
                return [{ id: 42, display_name: "Production migration" }];
            },
        },
    });

    expect(displayNames).toEqual(["Production migration"]);
});

test("generic task views keep their native action title", async () => {
    let readCount = 0;
    let setCount = 0;

    await setProjectTaskDisplayName({
        context: {},
        config: { setDisplayName: () => setCount++ },
        orm: {
            read: () => {
                readCount++;
                return [];
            },
        },
    });

    expect(readCount).toBe(0);
    expect(setCount).toBe(0);
});


test("milestone task routes use the milestone title before the project title", async () => {
    const names = [];
    await setProjectTaskDisplayName({
        context: { default_project_id: 42, default_milestone_id: 73 },
        config: { setDisplayName: (name) => names.push(name) },
        orm: { read(model, ids, fields) {
            expect(model).toBe("project.milestone");
            expect(ids).toEqual([73]);
            expect(fields).toEqual(["display_name"]);
            return [{ id: 73, display_name: "[QA Navigation] Launch readiness" }];
        } },
    });
    expect(names).toEqual(["[QA Navigation] Launch readiness"]);
});

test("an unreadable milestone keeps the native title without leaking its project", async () => {
    let reads = 0;
    let changed = false;
    await setProjectTaskDisplayName({
        context: { default_project_id: 42, default_milestone_id: 73 },
        config: { setDisplayName: () => changed = true },
        orm: { read() { reads++; throw new Error("Access denied"); } },
    });
    expect(reads).toBe(1);
    expect(changed).toBe(false);
});
