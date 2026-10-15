# Task search qualification — 15 October 2026

These six captures show the final tested source delivered with this change,
including the native Task page/count/group/Pivot execution guards. `capture.json`
pins the product source and exact image SHA-256 values. An ordinary Project user
works in a disposable synthetic database, with two allowed companies and only
company A selected. Every displayed company, person, task and activity is invented.
Desktop is 1440×1000; mobile is 390×844. Both implementation owner and Lead personally
inspected every image and its metadata before publication. PNGs contain only
image chunks; no credentials, private records, browser bars or local paths appear.

1. **Home before searching:** existing native widgets and search entry point.
2. **Available Task during search:** the real disposable remaining-category
   transport is deliberately delayed for five seconds. The Task result can be
   opened while the search status remains visible.
3. **Open the native Task:** normal form, source description and synthetic assignee.
4. **Back to search:** the original query and recomputed authorized result remain.
5. **Native Task list:** Open + Task-content facets, 80 rows out of exactly 801 matches,
   with the normal native order and pager.
6. **Mobile result:** the same readable and actionable result at 390 pixels.

Still images illustrate these states. Separate functional assertions verify
partial completion, stale-reply isolation, native request aborts, opening/Back,
correct native page/count/Pivot aggregates and access boundaries. In the final
browser journey the native Task page returned in 267 ms with zero page errors;
this is a synthetic local measurement, not a production performance guarantee.
No actual browser crash was reproduced. Browser transport abort does not promise
cancellation of server SQL already executing.

The exact 164-module canonical closure passed 44 native backend cases. Desktop and
mobile HOOT each passed 21 cases / 105 assertions. Causal reversions fail the new
activity/schema, partial-result/cancellation and native execution/Pivot checks.
Original versus fixed full Task payloads have identical ordered 80 IDs and exact
801 count with JIT safely disabled in both comparison transactions. The broad
query's PostgreSQL JIT compilation cost is scoped away during actual content
query execution; ordinary queries retain their prior settings.
