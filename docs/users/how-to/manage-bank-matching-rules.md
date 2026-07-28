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

Choose **Find** from the rules list. The analysis looks back two years for at
least three reconciled bank transactions with the same label, journal and
counterpart account. It ignores invoice/payment matching and partner-only
patterns. Running it again is safe: it does not duplicate an existing
suggestion or equivalent rule.

The resulting records are inert **Suggestions**:

- they contain their source, confidence, date range and supporting bank
  transactions;
- they cannot participate in Bank Matching;
- an Accounting Manager must review the condition and counterpart entry, then
  choose **Approve Rule** or **Dismiss**.

**Find** never changes a bank transaction, journal entry or reconciliation and
never activates a rule. If there is no new pattern to review, it reports that
there are no new suggestions.

A future Accounting Agent may create the same kind of suggestion through MCP
by setting the structured suggestion source, confidence and evidence fields.
An AI suggestion remains inert under the same approval boundary.

Archiving or dismissing a rule preserves its chatter and usage evidence. It
does not change or undo any existing journal entry.
