import { UslUserPreferencesController } from "@usl_pocketid/js/sender_alias_preferences";
import { expect, test } from "@odoo/hoot";

test("saving address changes keeps the Preferences dialog open and confirms delivery", async () => {
    const notifications = [];
    const controller = {
        env: { inDialog: true },
        model: {
            root: {
                async getChanges() {
                    return { usl_sender_alias_ids: [[0, 0, { email: "new@example.com" }]] };
                },
                async save(params) {
                    expect(params.reload).toBe(true);
                    expect.step("record saved and refreshed");
                    return true;
                },
            },
        },
        notification: {
            add(message, options) {
                notifications.push({ message, options });
            },
        },
        onSaveError() {},
        props: {
            saveRecord() {
                expect.step("generic dialog save");
            },
            onSave() {
                expect.step("dialog closed");
            },
        },
    };

    const saved = await UslUserPreferencesController.prototype.save.call(controller);

    expect(saved).toBe(true);
    expect(notifications).toHaveLength(1);
    expect(notifications[0].options.title).not.toBe(undefined);
    expect(notifications[0].message).not.toBe(undefined);
    expect(notifications[0].options.type).toBe("success");
    expect.verifySteps(["record saved and refreshed"]);
});

test("ordinary preference saves retain the standard close behavior", async () => {
    const controller = {
        env: { inDialog: true },
        model: { root: { getChanges: async () => ({}) } },
        notification: { add: () => expect.step("notification") },
        onSaveError() {},
        props: {
            saveRecord: async () => {
                expect.step("generic dialog save");
                return true;
            },
            onSave: () => expect.step("dialog closed"),
        },
    };

    const saved = await UslUserPreferencesController.prototype.save.call(controller);

    expect(saved).toBe(true);
    expect.verifySteps(["generic dialog save", "dialog closed"]);
});

test("security actions save without closing the Preferences dialog", async () => {
    const controller = {
        env: { inDialog: true },
        model: {
            root: {
                async save(params) {
                    expect(params.reload).toBe(true);
                    expect.step("saved");
                    return true;
                },
            },
        },
        props: {
            onSave() {
                expect.step("dialog closed");
            },
        },
    };

    const saved = await UslUserPreferencesController.prototype.beforeExecuteActionButton.call(
        controller,
        { name: "action_revoke_all_devices", type: "object" }
    );

    expect(saved).toBe(true);
    expect.verifySteps(["saved"]);
});
