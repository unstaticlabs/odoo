/**
 * Upload a voice note to the record it was recorded for, as one of its attachments, with no
 * composer: it does not matter which record or screen is open now. The native upload resolves
 * without an attachment when the server refused it, after telling the user "Server error".
 *
 * @param {object} env
 * @param {{model: string, id: number}} destination
 * @param {File} file
 * @returns {Promise<boolean>} whether the server now holds the recording
 */
export async function uploadVoiceNote(env, destination, file) {
    try {
        const thread = env.services["mail.store"]["mail.thread"].insert({
            model: destination.model,
            id: destination.id,
        });
        const attachment = await env.services["mail.attachment_upload"].upload(thread, undefined, file, { voice: true });
        return Boolean(attachment);
    } catch {
        return false;
    }
}
