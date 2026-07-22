# How the Rebuilt Accounting Evidence Works

Audience: CEO, accountant, finance operator.

The rebuilt accounting product starts from an Odoo Online backup. The source database is restored separately and read as evidence. The target Odoo database is then populated through the Community fork using source-traced records.

The goal is not to make a copy of the SaaS database. The goal is to preserve accounting meaning in a clean target Odoo system.

## What Is Preserved

The imported target preserves:

- companies;
- accounts;
- journals;
- posted journal entries;
- journal items;
- partners;
- taxes;
- tax repartition and tax tags;
- currencies;
- payments with journal entries;
- bank statement lines;
- full and partial reconciliations inside the selected boundary;
- cross-boundary reconciliation review records;
- analytic plans, accounts and lines;
- fixed assets;
- depreciation schedule evidence;
- deferred schedule evidence;
- selected accounting attachments;
- source report catalogue and report structure;
- discrepancies and review decisions.

## Source Trace

Imported records keep source trace fields where applicable. These fields identify:

- source database;
- source model;
- source record id;
- source snapshot;
- import run;
- import status or note.

This lets a reviewer trace a target record back to the source backup.

## Technical Evidence Versus Professional Acceptance

Technical evidence means the system has generated, compared or validated data.

Professional acceptance means the accountant or authorized stakeholder has reviewed the evidence and recorded a decision.

Milestone closure requires both. Technical evidence alone is not accountant approval.

## Why Reports Are "Imported" Reports

Many source reports came from Odoo Online Enterprise. The Community fork must not copy proprietary Enterprise implementation code.

Instead, the rebuilt product provides lawful USL-original report views, export workflows and source report evidence. The source report catalogue, lines, columns and expressions are preserved for review so the accountant can compare intent and outcome.

