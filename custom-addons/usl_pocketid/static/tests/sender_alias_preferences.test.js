import { UslUserPreferencesController } from "@usl_pocketid/js/sender_alias_preferences";
import { expect, test } from "@odoo/hoot";

test("saving address changes keeps the Preferences dialog open and confirms delivery", async () => {
    const notifications = [];
    const controller = {
        env: { inDialog: true },
        keepOpenForSenderAliases: false,
        model: {
            root: {
                async save() {
                    controller.keepOpenForSenderAliases = true;
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
    expect.verifySteps([]);
});

test("ordinary preference saves retain the standard close behavior", async () => {
    const controller = {
        env: { inDialog: true },
        keepOpenForSenderAliases: false,
        model: { root: { save: async () => true } },
        notification: { add: () => expect.step("notification") },
        onSaveError() {},
        props: { onSave: () => expect.step("dialog closed") },
    };

    const saved = await UslUserPreferencesController.prototype.save.call(controller);

    expect(saved).toBe(true);
    expect.verifySteps(["dialog closed"]);
});
