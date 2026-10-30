# Manage Bank Matching Rules

Open **Accounting > Configuration > Bank Matching Rules**.

The list separates three facts that older reconciliation-model screens mixed
together:

- **Assessment** explains whether a rule is used, executable, redundant,
  incomplete, suggested or archived.
- **Activity** combines recorded uses and currently open matches in one badge.
  Blue badges identify rules with matching transactions to inspect; green
  badges identify recorded use; muted badges mean no observed activity.
- **Accounting result** explains what the rule creates. **Automated** triggers
  use a light amber badge so unattended behavior is immediately visible.

Opening a rule keeps the operational configuration in three compact areas:

- **Match when** contains journal, partner, bank-label and amount conditions;
- **Then** states the accounting result, behavior and optional follow-up;
- **Counterpart entries** contains the native Odoo accounting lines.

Optional business notes, use history and suggestion evidence are folded under
**Notes and evidence**. A recommendation appears only for an incomplete,
redundant or suggested rule, or when current matching transactions provide a
concrete review action. Healthy rules with nothing to act on do not display
generic advice.

Partner-only rules are normally **Redundant**. The smart bank-evidence system
already infers partners from bank accounts, counterparty names and consistent
reconciled history. These old rules cannot create an accounting proposal in the
current OCA engine. Review them, then use **Archive Redundant Rule** when no
external process depends on them.

Keep accounting rules when they represent a predictable direct accounting
result, such as a bank fee or a controlled internal transfer. Prefer **Require
Review** until the rule has narrow conditions and repeated correct outcomes.
Use **Automate** only when every match has one unambiguous treatment.

## Find suggested rules

Use the discovery flow to prepare a review without changing accounting:

1. Choose **Find** to prepare suggestions from at least three distinct surviving
   transactions with a consistent native accounting result. A partial result
   opens the available suggestions immediately and shows a warning with counts
   and links to preserved skipped rules; dismissing the warning is optional.
2. Inspect the label pattern, supporting transactions and proposed counterpart
   entries. Changing reference digits can be represented without broadening the
   stable wording into an unrestricted pattern.
3. Open **Possible overlaps** to inspect the named rules. The visible warning
   states the uncertainty before approval. Conflicts demonstrated by
   surviving transactions or supported witnesses in the same text field are
   refused. Possible future combinations across label, details and narration
   remain warnings requiring judgment, even when the label patterns are separate.
4. After an edit or a stale-source warning, choose **Recheck evidence**. This
   checks the current rule without changing accounting or activating it.
5. Choose **Approve Rule** or **Dismiss**. A pending suggestion cannot run.

Support counts are examples, not confidence percentages. Generated provenance
cannot be replaced through ordinary fields or context, and a copied generated
rule remains a suggestion. Ordinary handwritten rules keep their native editor
and permissions. Unsupported historical effects need native manual rule
configuration; they are not silently replaced by a simple account assignment.

Suggestions prepared before this update retain their existing manager review and
approval, with their original notes. They are not presented as having passed the
new replay check. Recheck evidence is available when their retained examples can
support the new proof.

Archiving or dismissing a rule preserves its chatter and usage evidence. It
does not change or undo any existing journal entry. To reconsider an archived
suggestion, find it with the native **Archived** filter and choose **Unarchive**
in the action menu. Review or recheck its evidence, then approve it. Approval and
dismissal are unavailable while the suggestion is archived, for humans and
delegated Agents alike.
