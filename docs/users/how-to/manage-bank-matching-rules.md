# Manage Bank Matching Rules

Open **Accounting > Configuration > Bank Matching Rules**.

The list separates three facts that older reconciliation-model screens mixed
together:

- **Assessment** explains whether a rule is used, executable, redundant,
  incomplete, suggested or archived.
- **Uses** counts source-system history and traceable applications in this
  rebuild. **Open matches** counts unmatched transactions that satisfy the rule
  now.
- **Purpose**, **Applies when** and **Accounting result** explain what the rule
  does without requiring knowledge of its implementation.

Partner-only rules are normally **Redundant**. The smart bank-evidence system
already infers partners from bank accounts, counterparty names and consistent
reconciled history. These old rules cannot create an accounting proposal in the
current OCA engine. Review them, then use **Archive Redundant Rule** when no
external process depends on them.

Keep accounting rules when they represent a predictable direct accounting
result, such as a bank fee or a controlled internal transfer. Prefer **Require
Review** until the rule has narrow conditions and repeated correct outcomes.
Use **Automate** only when every match has one unambiguous treatment.

## Find new opportunities

Choose **Find Opportunities** from the rules list. The analysis looks back two
years for at least three reconciled bank transactions with the same label,
journal and counterpart account. It ignores invoice/payment matching and
partner-only patterns.

The resulting records are inert **Suggestions**:

- they contain their source, confidence, date range and supporting bank
  transactions;
- they cannot participate in Bank Matching;
- an Accounting Manager must review the condition and counterpart entry, then
  choose **Approve Rule** or **Dismiss**.

A future Accounting Agent may create the same kind of suggestion through MCP
by setting the structured suggestion source, confidence and evidence fields.
An AI suggestion remains inert under the same approval boundary.

Archiving or dismissing a rule preserves its chatter and usage evidence. It
does not change or undo any existing journal entry.
