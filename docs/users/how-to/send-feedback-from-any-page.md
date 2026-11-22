---
title: Send feedback from any page
type: how-to
description: Report a problem, suggest an improvement or ask a question without leaving the screen you are on, then follow the answer in My feedback.
lang: en
persona: everyone
app: Feedback
models:
  - project.task
screen: /odoo
journey: send-feedback-from-any-page
tour: usl_feedback_desktop_journey
source:
  - custom-addons/usl_feedback/tests/test_feedback_journeys.py
screenshot_01-open_drawer_png: a63e9056708d0b7cea97071e4fed309fd53e550d94fa7c2130e350e49f82e319
screenshot_02-conversation_png: f996286ca0ff35b8b147504cd51938b9507e39ccbf77fdcb365cff6d6274eb3e
screenshot_03-chatter_png: 5d4b5c9453a58966d6360f30524127ff15ff7003ea2b96dd31e32a882d83b7fd
screenshot_04-team_received_png: 7f5e9d0ab373cc8c9014b77ce5dbd58b1eaa06908064cee1db94f6c5c086c89c
screenshot_05-recent_list_png: d908e6763f7171e52f11343d374d1921e3e12a002776207461617c13ae8d5f9d
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Send feedback from any page

Report a problem, suggest an improvement or ask a question without leaving the screen you are on, then follow the answer in My feedback.

1. Open the **Messages** drawer from the top bar. **Feedback** sits beside **New Message** in every tab.

   ![Open the Messages drawer from the top bar](send-feedback-from-any-page/01-open_drawer.png)

2. Choose **Feedback**.

3. The drawer closes and a floating conversation opens, like a chat. Odoo prepares a preview of the page you are on; it stays on your computer until you decide to send it.

   ![The drawer closes and a floating conversation opens, like a chat](send-feedback-from-any-page/02-conversation.png)

4. Describe what you saw and what you expected, in your own words. One message is enough; the product team asks if it needs more.

5. **Page details** is ticked when the screen you are on gives useful context: the action, the record you can read, the company and the release, never the address, form values or passwords. Untick it when the page does not matter.

6. Choose **Send feedback** to start the conversation. The assistant prepares a draft for your review.

7. Read the draft in the conversation. You can add a clarification or edit it before sharing it with the product team.

   ![Read the draft in the conversation](send-feedback-from-any-page/03-chatter.png)

8. When the draft describes what you meant, choose **Send feedback** on the draft.

9. The conversation confirms that the product team has received your feedback. Further replies appear here.

   ![The conversation confirms that the product team has received your feedback](send-feedback-from-any-page/04-team_received.png)

10. **New** starts another conversation. The card you just sent is kept.

11. **My feedback** lists every card you sent with its current status.

12. Choose a card to reopen its conversation and reply.

13. Come back to **My feedback** at any time from the same drawer to see where each report stands.

   ![Come back to My feedback at any time from the same drawer to see where each report stands](send-feedback-from-any-page/05-recent_list.png)
