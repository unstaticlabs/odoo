# Documents hybrid-search evaluation

`hybrid-v1.json` is a wholly synthetic, versioned French/English retrieval
definition. It contains 21 generated archive records with five questions each
and four negative probes (109 questions total). It covers exact references, VAT numbers, amounts,
paraphrase, projects, Accounting, Expenses, TESE/HR, Platform Billing,
contracts, similar documents, background material, cross-company decoys,
restricted canaries, and malicious prompt text. No production content or
credential is present.

`paperless_hybrid_evaluation.py` runs inside the qualified Paperless image. It
seeds idempotently under the `USL hybrid evaluation v1` tag, measures native
Tantivy lexical results, the permission-scoped semantic endpoint, and the same
reciprocal-rank fusion used by Odoo. Lexical calls include an explicit
authorized `id__in` scope; semantic calls require `document_ids`. It records
per-question top-five rankings, recall, MRR, latency, permission leaks, exact
identifier regressions, and vector-index identity.

Private run output belongs outside Git. Run the seed, rebuild Tantivy and the
LLM index for each qualified chunk-size variant, evaluate, and preserve the
JSON output in the private release-evidence directory. The release gate is
hybrid recall@5 of at least 90%, zero unauthorized results, and zero exact
identifier regressions. A no-result probe is reported separately because
nearest-neighbour retrieval intentionally returns bounded candidates without a
global similarity threshold.

## Qualified result

The 2026-08-25 isolated QA qualification compared the same 31 indexed roots
(the deterministic evaluation set plus the existing QA roots) with Paperless's
native 200-token overlap:

| Candidate | Vectors | Hybrid recall@5 | Hybrid MRR | Semantic median / p95 | Observed Ollama memory |
|---|---:|---:|---:|---:|---:|
| 512 tokens | 911 | 100% | 0.9770 | 250.6 / 773.4 ms | 1.36–1.69 GiB |
| 1024 tokens | 264 | 100% | 0.9770 | 294.3 / 1,149.0 ms | about 2.15 GiB |

Both variants produced identical top-five rankings for all 109 questions,
semantic recall@5 of 100%, semantic MRR of 0.9484, zero unauthorized results,
and zero exact-identifier regressions. Lexical-only recall@5 was 40%, which
demonstrates that the synthetic paraphrase cases exercise semantic retrieval
rather than merely duplicating Tantivy matches. The four negative probes
return bounded nearest neighbours by design and are reported separately.

The release selects 512 tokens: it keeps the same measured quality, improves
warm semantic latency and observed memory, and gives finer long-document
granularity. Its cost is 3.45 times as many vectors. A timed warm rebuild took
448 seconds; the 1024 candidate took approximately 13 minutes 48 seconds in
the same local stack. The final selected rerun recorded 109 questions, hybrid
recall@5 100%, hybrid MRR 0.9770, semantic median/p95 171.0/222.8 ms, zero
unauthorized results, and an index of 911 vectors under schema 2. The private
selected evidence JSON is mode `0600`, is 75,415 bytes, and has SHA-256
`cc772a10b880dcc5e80b57b6dffb40041710d4f3599d9b4b9c722a7dc792c7bf`;
its index identity was
`a42a9c3f514a00a0a4d7db37bc2385801461dd6416a0a1b12de18f16a20937f1`.

The frozen Checkpoint B retrieval identity is Ollama 0.30.11, model alias
`usl-bge-m3:documents-20260824-rc1`, model digest
`7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`,
1,024-dimensional F16 vectors, 8,192-token model context, 512-token document
chunks, 200-token overlap, and Paperless LLM-index schema 2. A change to any
of these values requires a deliberate index rebuild and a new release-cohort
identity.
