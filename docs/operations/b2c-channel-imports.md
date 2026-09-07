# Importing channel exports

The channels email a CSV; this turns it into commerce, sales, stock and
accounting. Nothing outside a batch changes until the batch is applied, and
every stage can be run again without adding anything.

## What a drop is read as

| Export | What it is believed for |
| --- | --- |
| Etsy — sold orders | The money Etsy charged and kept, and the destination |
| Etsy — sold order items | What was bought, and each transaction's identity |
| Medusa — orders | The destination and the carriage, discount and tax |
| Medusa — sold items per order | The lines, one row per order |
| Medusa — sold items | The lines, one row per line |
| Printful (read over its API) | What fulfilment cost, and when it shipped |

A file is recognised by the **set** of column names it declares, not by its
name or the order of its columns. A channel that reorders its columns is still
recognised; one that renames or drops a column is reported, naming the column.

Medusa's item exports carry neither the destination nor the carriage. Without
the destination there is no rate to charge, so a drop of those alone is stopped
and the **orders** export is asked for by name. Export all three.

## The routine

1. **B2C → Channel Imports → New.** Drop the files in. Name the drop.
2. **Read the files.** Nothing outside the batch changes. The report says how
   many orders are new, how many Odoo already holds, and what needs attention.
3. **Resolve products.** Every line is matched to the product it sold, through
   the mappings already confirmed. A variant matched exactly is *derived*, and
   confirming it writes the mapping so the next sale of the same thing needs
   nobody. Anything else says what it needs: a product Odoo does not know, a
   name pointing at two products, or an attribute with no value for what the
   channel sold — which can be added from the finding itself.
4. **Read the supplier.** Printful is asked what each order cost and when it
   shipped. A fulfilment whose reference no export carries is tied to its sale
   by recipient and date, or reported as tied to none.
5. **Check readiness.** What would make the drop post the wrong numbers, each
   finding stopping only the step it would spoil.
6. **Apply.** Contacts, canonical orders, sales orders priced at what was
   actually paid, and deliveries completed on the day the goods left.
7. **Invoice.** One invoice per sale, dated the day of the sale, settled into
   the channel's clearing account.
8. **Bill the commission.** One bill per channel per month, paid out of the
   same clearing account.

What is left is the bank: each payout meets the clearing account, which by then
holds exactly what the payout brings.

## Configuration this depends on

Per channel (**B2C → Configuration → Channels**):

- **Printful store** — which store fulfils this channel.
- **Carriage product** — what customers pay to ship is not revenue from goods;
  this product's income account is where it goes.
- **Clearing journals** — one per currency the channel collects in, each
  posting its payments to that channel's clearing account.
- **Channel operator** and **Commission product** — who bills the commission
  and what it is bought as.

Per company:

- **Registered for the One Stop Shop**, and the two accounts destination VAT
  reaches before and after registration. Until registration it accrues as a
  liability to be regularised rather than as VAT collected under a scheme the
  shop is not in.

In the chart:

- Channel prices are what the customer paid, so the taxes those sales bear must
  be **included in the price**, not added to it.
- A fiscal position built for a country must be the one that answers for it. A
  generic position with no country and a lower sequence answers first and taxes
  a German sale at nothing.
- Every product sold states **one** sales tax, and every sold product's
  category names a usable income account.

The readiness check reports each of these against the drop in hand, and makes
the corrections that are a matter of fact rather than judgement.

## Destination VAT

**B2C → Destination VAT** holds one return per quarter. Computing it reads the
quarter's invoices for the taxable amount and the VAT each country bore. While
the shop is not registered, the return is what the liability would be; the
ledger carries it in the account for liabilities to be regularised.

## The Printful token

Set the system parameter `usl_b2c_ingest.printful_token`. It is read-only
access to the supplier's own orders. It is never in the repository and a
rotated token needs no release.

## One sale recorded twice

A sale exported by the older shop and again by the one that replaced it is one
sale. The finding names both records; resolving it keeps the fuller — the lines
first, then the money — cancels the other and points it at its replacement.
Nothing is deleted, and the ledger is untouched.
