# B2C archive source and field matrix

Every parsed row is retained verbatim in restricted immutable
`b2c.provider.evidence.payload_json`, even when a column also has a typed
destination. The payload is business evidence, not technical migration trace.
`file SHA-256 + schema digest + row number + row digest` makes blank or repeated
provider identifiers safe. PII columns are excluded from broad analytics.

Legend: **typed** populates a structured B2C field; **evidence** is deliberately
retained only in the restricted payload; **link candidate** is retained and may
support a reviewed link but is never applied automatically.

## Files

| Archive | Files | Rows | Canonical use |
| --- | ---: | ---: | --- |
| Etsy monthly statements, Dec 2024–Aug 2026 | 21 | 1,346 | payment/refund/deposit/fee/tax events; order reference when present |
| Etsy sold items, 2024–2026 | 3 | 235 | 173 canonical orders, 235 lines, 237 units and SKU/listing aliases |
| Legacy Goodboys/Medusa | 1 | 249 | umbrella order headers; lowest precedence |
| Current Medusa | 1 | 96 | richer order/payment/fulfilment headers; 41 overlap legacy |
| Supplemental Medusa sold items, through 5 Aug 2026 | 1 | 222 | line detail for all 96 current Medusa orders; 225 units; file/row identity because provider line IDs are absent |
| Stripe payouts | 1 | 8 | payout events |
| Stripe unified payments | 1 | 149 | payment/refund/fee events; 72 blank row IDs remain distinct |
| Revolut Merchant | 1 | 318 | 311 payments and seven linked refunds |
| Printful review PDF | 1 | 261 | 247 completed and 14 negative-refund fulfilment/COGS events |
| Supporting sales and Stripe tax PDFs | 9 | n/a | checksum-locked supporting attachment/evidence links |

## Etsy monthly statement schema

| Columns | Disposition |
| --- | --- |
| `Date` | typed `event_date` |
| `Type` | typed `event_type` plus `original_provider_state` |
| `Title`, `Info` | evidence; an embedded nonblank order number is a link candidate |
| `Currency` | typed transaction currency |
| `Amount` | typed amount; Refund is normalized negative |
| `Fees & Taxes` | typed nonnegative fee amount |
| `Net` | typed net amount |
| `Tax Details` | evidence; no fabricated tax allocation |

The parser locks the observed type counts: Sale 175, Refund 4, Payment 1,
Deposit 20, Fee 1,062, Marketing 2, and Tax 82.

## Etsy sold-item schema

| Columns | Disposition |
| --- | --- |
| `Sale Date`, `Date Paid`, `Date Shipped` | Sale Date types `order_date`; paid/shipped dates remain evidence until a complete event link is reviewed |
| `Item Name`, `Quantity`, `Price`, `Item Total` | typed line name, quantity, unit price, and evidenced line revenue |
| `Discount Amount`, `Order Shipping`, `Order Sales Tax` | typed line discount (negative), shipping, and tax |
| `Transaction ID`, `Listing ID`, `Order ID`, `SKU` | typed durable external identifiers; SKU/listing create a pending alias |
| `Variations` | typed original variation |
| `Currency`, `Ship Country` | typed order/line currency and country where consistent across the order |
| `Coupon Code`, `Coupon Details`, `Shipping Discount`, `Order Type`, `Listings Type`, `Payment Type`, `InPerson Discount`, `InPerson Location`, `VAT Paid by Buyer` | evidence; no unsupported amount allocation or status inference |
| `Buyer`, `Ship Name`, `Ship Address1`, `Ship Address2`, `Ship City`, `Ship State`, `Ship Zipcode` | restricted PII evidence only |

All 56 nonblank source SKUs are preserved. None exactly matches the 43 nonblank
catalog SKUs, so every mapping begins pending.

## Legacy Goodboys/Medusa schema

| Columns | Disposition |
| --- | --- |
| `Order` | typed canonical/external order identifier |
| `Store` | evidence; channel is normalized to Medusa by governed source mapping |
| `Status` | typed original state and normalized order state |
| `Total` | typed transaction revenue/total when present; currency remains unknown |
| `Date` | typed order date |
| `Address` | restricted PII/country evidence; not parsed into a guessed customer or country |

## Current Medusa schema

| Columns | Disposition |
| --- | --- |
| `Order_ID`, `Display_ID` | typed external identifiers and overlap keys |
| `Order status`, `Fulfillment Status`, `Payment Status` | order state is typed from Order status; all three original values remain evidence; fulfilment/payment values are link candidates only |
| `Date` | typed order date |
| `Shipping Country Code` | typed country where a unique native country exists |
| `Subtotal`, `Shipping Total`, `Discount Total`, `Tax Total`, `Total`, `Currency Code` | typed transaction amounts/currency; discount is normalized negative; order totals remain header-owned and are never replaced by line sums |
| `Customer First name`, `Customer Last name`, `Customer Email`, `Customer ID`, `Shipping Address 1`, `Shipping Address 2`, `Shipping City`, `Shipping Postal Code`, `Shipping Region ID` | restricted PII evidence only; no guessed partner |

## Supplemental Medusa sold-item schema

The Odoo Online dump remains authoritative for native and archived records. It
contains the 96 current Medusa headers but not their line export. The separately
supplied provider export `medusa-sold-items-2026-08-05.csv` is therefore
classified as post-dump supplemental business evidence. Its SHA-256 is
`e8308c402a63d4c4fd7ee066c8a59daeba7b00cd66f421221191cec50418550a`.
The importer requires that exact private file under
`artifacts/b2c-restore/source/`; it is ignored by Git and never committed.

| Columns | Disposition |
| --- | --- |
| `order_number` | exact join to the current Medusa `Display_ID`; an unknown, blank, duplicate or uncovered display ID aborts |
| `date`, `currency` | must agree with the authoritative Medusa order header; disagreement aborts |
| `product`, `variant`, `quantity`, `unit_price`, `line_total` | typed original line name/variation, units, unit price and evidenced line amount |
| `sku` | typed original SKU; a pending alias is created when nonblank; an exact Odoo SKU may be suggested but is never verified automatically |
| `order_status` | restricted provider evidence; it does not override the header-owned canonical state |
| `customer_email` | restricted PII evidence only |

The locked baseline is 222 rows, 96 orders, 225 units, 138 rows with a
nonblank SKU and 50 distinct SKUs. Nine SKUs exactly match the restored Odoo
catalogue and remain pending suggestions. All 222 rows lack immutable provider
line IDs, so idempotency uses the checksum-locked file plus row number and does
not collapse the one genuine duplicate business row. Line sums are preserved
as evidenced; they are not forced to equal header totals and never overwrite
order-level revenue, shipping, discounts or tax.

## Stripe payout schema

| Columns | Disposition |
| --- | --- |
| `id` | required typed payout and transaction identifier; blank aborts |
| `Amount`, `Currency`, `Created (UTC)`, `Status` | typed amount, currency, event date, and original/normalized state |
| `Arrival Date (UTC)`, `Balance Transaction`, `Failure Balance Transaction`, `Trace ID` | evidence and reviewed accounting/bank link candidates |
| `Source Type`, `Destination`, `Type`, `Method`, `Description`, `Statement Descriptor`, `Trace ID Status`, `Destination Name`, `Destination Country`, `Destination Last 4` | evidence; destination data is restricted |
| `Failure Message`, `Failure Code` | evidence; never discarded on failed payouts |

## Stripe unified-payment schema

| Columns | Disposition |
| --- | --- |
| `id` | typed external transaction when nonblank; blank uses row evidence key and is never a deduplication key |
| `PaymentIntent ID`, `Checkout Session ID`, `session_id (metadata)` | typed durable identifiers; there are respectively 134, four, and 117 distinct nonblank values |
| `Created date (UTC)`, `Refunded date (UTC)`, `Status` | created date and original/normalized state are typed; refund date remains evidence until separately evidenced as an event date |
| `Amount`, `Amount Refunded`, `Currency`, `Fee`, `Taxes On Fee` | typed amount, negative refund component, transaction currency, fee, and tax evidence |
| `Converted Amount`, `Converted Amount Refunded`, `Converted Currency` | typed company amount/conversion only when processor evidence is complete; otherwise conversion stays pending |
| `Captured`, `Decline Reason`, `Seller Message`, `Disputed Amount`, `Dispute Date (UTC)`, `Dispute Evidence Due (UTC)`, `Dispute Reason`, `Dispute Status` | evidence and state/review context; no native dispute workflow is manufactured |
| `Description`, `Statement Descriptor`, `Invoice ID`, `Invoice Number`, `Client Reference ID`, `Payment Link ID` | evidence and reviewed order/accounting link candidates |
| `Is Link`, `Link Funding`, `Mode`, `Payment Source Type`, `Interchange Costs`, `Merchant Service Charge`, `Application Fee`, `Application ID`, `Destination`, `Transfer`, `Transfer Group` | evidence; fee subcomponents are not double-counted without a proved formula |
| `Checkout Custom Field 1 Key`, `Checkout Custom Field 1 Value`, `Checkout Custom Field 2 Key`, `Checkout Custom Field 2 Value`, `Checkout Custom Field 3 Key`, `Checkout Custom Field 3 Value`, `Checkout Line Item Summary`, `Checkout Promotional Consent`, `Checkout Terms of Service Consent` | restricted evidence; no line items are fabricated from summaries |
| `UTM Campaign`, `UTM Content`, `UTM Medium`, `UTM Source`, `UTM Term` | evidence; no global UTM records are created |
| `Terminal Location ID`, `Terminal Reader ID` | evidence |
| `Card ID`, `Card Name`, `Card Address Line1`, `Card Address Line2`, `Card Address City`, `Card Address State`, `Card Address Country`, `Card Address Zip`, `Card AVS Line1 Status`, `Card AVS Zip Status`, `Card Brand`, `Card CVC Status`, `Card Exp Month`, `Card Exp Year`, `Card Fingerprint`, `Card Funding`, `Card Issue Country`, `Card Last4`, `Card Tokenization Method` | restricted payment/PII evidence only |
| `Customer ID`, `Customer Description`, `Customer Email`, `Customer Phone`, `Shipping Name`, `Shipping Address Line1`, `Shipping Address Line2`, `Shipping Address City`, `Shipping Address State`, `Shipping Address Country`, `Shipping Address Postal Code` | restricted PII evidence only; no guessed partner |

A Stripe row can contain a positive original payment and a negative refund
component. It remains a payment event unless the source amount itself is
nonpositive; this avoids reversing the original sale while preserving the
refund field.

## Revolut Merchant schema

| Columns | Disposition |
| --- | --- |
| `payment_id` | required typed provider event/transaction identifier |
| `type`, `state`, `reason` | typed event/original state where applicable; full values remain evidence |
| `original_payment_id` | typed original-event link; a missing referenced payment aborts |
| `order_id`, `merchant_order_ext_ref` | typed external order candidate, with explicit precedence and no guess |
| `amount`, `currency`, `refunded_amount`, `fee_amount`, `fee_currency` | typed transaction/refund/fee values; refunds normalize negative; inconsistent fee currency remains explicit evidence |
| `created_date`, `captured_date` | created date types event date; captured date remains evidence |
| `description`, `surcharge_amount`, `tip_amount`, `payment_method`, `location_id` | evidence; surcharge/tip are not silently folded into another metric |
| `customer_id`, `customer_card_number`, `customer_card_country`, `customer_card_brand`, `customer_card_type`, `customer_card_category`, `customer_email` | restricted PII/payment evidence only |

## Printful PDF-derived schema

The PDF parser deterministically extracts exactly these visible columns per
row. Changed token layout, missing monetary values, or an invalid Printful ID
aborts parsing.

| Parsed column | Disposition |
| --- | --- |
| date, status | typed event date and original/normalized fulfilment state |
| order, Printful ID | typed external order and Printful identifiers |
| origin country codes, destination | typed origin evidence and destination country where resolvable |
| products, discount, shipping, digitalization, tax, VAT, total | typed transaction-currency fulfilment/COGS components; all amounts invert for refunded rows |
| review | evidence/review context |

Printful evidence is not converted into native purchase orders, receipts,
deliveries, or valuation moves. Native links remain pending unless those real
records independently exist.

## Supporting PDFs

The sales report and eight Stripe tax invoices are retained by exact file
checksum and optional target attachment link. Their binary content is not
flattened into invented typed amounts. They remain supporting business evidence
for a human-reviewed accounting link.
