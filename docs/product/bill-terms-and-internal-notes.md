# Bill terms and internal accounting notes

**Terms and Conditions** (`account.move.narration`) is contract text printed on
the document. Put internal accounting analysis in a native internal Chatter
note. The same field meaning and native permissions apply to people and Agents.

| Information | Native destination |
| --- | --- |
| Supplier invoice reference | `account.move.ref` |
| Product or service description | Invoice line `name` |
| Payment identification | `account.move.payment_reference` |
| Printable contract terms | `account.move.narration` |
| Internal accounting commentary | Chatter, internal note (`mail.mt_note`) |

The bill configurator continues to accept native `header_values.narration` in
one transaction. Its MCP shortcut names that input `terms_and_conditions`;
clients must refresh their tool schema. Generic native writes keep `narration`.
MCP create/update tools provide advice when nonempty terms are written and
preserve the supplied text. This is guidance, not a permission or approval gate.
Use `odoo_add_internal_note` for a separate internal note action. It does not
write terms or notify partners or followers directly. After an unknown mutation
outcome, read the bill or Chatter before deciding whether anything is missing.

Upgrading this module adds English and French field help without rewriting
existing terms, notes, attachments or accounting lines. It retains Odoo’s
computed, editable HTML field and native draft/posted behavior. Existing data
is not automatically moved, cleared or classified by its wording. There is no
server-wide prohibition on accounting language in legitimate contract clauses.
