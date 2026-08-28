# Create and Collect a Customer Invoice

1. Open **Customers > Invoices** and select **New**.
2. Choose the customer, invoice and due dates, currency and payment terms.
3. Add business lines, income accounts, taxes and analytic distribution.
4. Preview the invoice, then post it.
5. Send or download the customer document.
6. When money arrives, register or identify the payment.
7. In the bank journal, open **Bank Matching** and match the receipt to the payment or invoice.
8. For a partial collection, confirm the remaining residual and keep it open.
9. Check payment state and residual on the invoice.
10. Inspect Grand livre auxiliaire, Balance âgée clients and Compte de résultat.

On a posted invoice, **Pay** records a payment against the document; it does not
contact the bank. **Credit Note** creates a linked draft correction. Review and
post it separately so the original invoice and correction remain traceable.
The generated PDF uses the company's governed legal identity and the pinned
USL template revision. If Odoo asks for missing legal information, complete
**Settings > Companies > Document Layout** before retrying. Odoo does not
silently substitute a different official layout when the renderer is
unavailable; an already persisted invoice PDF remains downloadable.
