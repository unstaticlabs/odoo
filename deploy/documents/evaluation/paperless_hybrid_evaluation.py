"""Seed and evaluate the synthetic USL Paperless hybrid-search corpus."""

import hashlib
import json
import os
import re
import sqlite3
import statistics
import time
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from documents.models import Document, Tag
from guardian.shortcuts import assign_perm
from paperless.config import AIConfig
from paperless_ai.tables import DocumentChunksTable, IndexMetaTable
from paperless_ai.vector_store import DB_FILENAME
from rest_framework.test import APIClient

DEFINITION_PATH = Path(
    os.environ.get("USL_HYBRID_EVALUATION_DEFINITION", "/tmp/hybrid-v1.json"),
)
ACTION = os.environ.get("USL_HYBRID_EVALUATION_ACTION", "evaluate")
VARIANT = os.environ.get("USL_HYBRID_EVALUATION_VARIANT", "unspecified")
OUTPUT_PATH = os.environ.get("USL_HYBRID_EVALUATION_OUTPUT")
TAG_NAME = "USL hybrid evaluation v1"
EXACT_QUERY = re.compile(
    r"\d{3,}|\b[A-Z]{2}[A-Z0-9 -]{5,}\b|[€$£]|\d+[.,]\d{2}",
)


def load_definition():
    return json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))


def document_checksum(definition_version, item):
    payload = f"{definition_version}\0{item['key']}\0{item['content']}"
    return hashlib.sha256(payload.encode()).hexdigest()


def seed(definition):
    user_model = get_user_model()
    service = user_model.objects.get(username="odoo-integration")
    tag, _created = Tag.objects.get_or_create(
        name=TAG_NAME,
        defaults={"owner": service},
    )
    identities = {
        persona: user_model.objects.get(username=username)
        for persona, username in definition["personas"].items()
    }
    seeded = {}
    for item in definition["documents"]:
        checksum = document_checksum(definition["version"], item)
        document, created = Document.objects.get_or_create(
            checksum=checksum,
            defaults={
                "title": item["title"],
                "content": item["content"],
                "mime_type": "text/plain",
                "owner": service,
            },
        )
        if not created:
            document.title = item["title"]
            document.content = item["content"]
            document.owner = service
            document.save(update_fields=("title", "content", "owner", "modified"))
        document.tags.add(tag)
        for persona in item["visible_to"]:
            assign_perm("view_document", identities[persona], document)
        seeded[item["key"]] = document.id
    print(  # noqa: T201 - management-shell contract writes JSON to stdout
        json.dumps({"action": "seed", "documents": seeded}, sort_keys=True),
    )


def cleanup():
    tag = Tag.objects.filter(name=TAG_NAME).first()
    document_ids = []
    if tag is not None:
        document_ids = list(tag.documents.values_list("id", flat=True))
        Document.objects.filter(id__in=document_ids).delete()
        tag.delete()
    print(  # noqa: T201 - management-shell contract writes JSON to stdout
        json.dumps(
            {"action": "cleanup", "deleted_document_ids": document_ids},
            sort_keys=True,
        ),
    )


def percentile(values, percent):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = min(len(ordered) - 1, round((len(ordered) - 1) * percent))
    return round(ordered[position], 3)


def fuse(lexical_ids, semantic_ids, query):
    lexical_ids = list(dict.fromkeys(lexical_ids))
    semantic_ids = list(dict.fromkeys(semantic_ids))
    if EXACT_QUERY.search(query):
        lexical = set(lexical_ids)
        return lexical_ids + [item for item in semantic_ids if item not in lexical]
    scores = {}
    for ranking in (lexical_ids, semantic_ids):
        for rank, document_id in enumerate(ranking, start=1):
            scores[document_id] = scores.get(document_id, 0.0) + 1.0 / (60 + rank)
    lexical_rank = {item: rank for rank, item in enumerate(lexical_ids, start=1)}
    semantic_rank = {item: rank for rank, item in enumerate(semantic_ids, start=1)}
    return sorted(
        scores,
        key=lambda item: (
            -scores[item],
            lexical_rank.get(item, 1_000_000),
            semantic_rank.get(item, 1_000_000),
            item,
        ),
    )


def request_rankings(client, query, scope):
    started = time.perf_counter()
    lexical_response = client.get(
        "/api/documents/",
        {
            "text": query,
            "id__in": ",".join(str(item) for item in scope),
            "page_size": 10,
        },
        HTTP_ACCEPT="application/json; version=10",
        HTTP_HOST="paperless-webserver",
    )
    lexical_ms = (time.perf_counter() - started) * 1000
    if lexical_response.status_code != 200:
        raise RuntimeError(f"Lexical request failed: {lexical_response.status_code}")
    lexical_payload = lexical_response.data
    lexical_ids = [item["id"] for item in lexical_payload.get("results", [])]

    started = time.perf_counter()
    semantic_response = client.post(
        "/api/documents/semantic_search/",
        {"query": query, "document_ids": scope, "limit": 10},
        format="json",
        HTTP_ACCEPT="application/json; version=10",
        HTTP_HOST="paperless-webserver",
    )
    semantic_ms = (time.perf_counter() - started) * 1000
    if semantic_response.status_code != 200:
        raise RuntimeError(
            f"Semantic request failed: {semantic_response.status_code} "
            f"{semantic_response.data}",
        )
    semantic_ids = [item["id"] for item in semantic_response.data["results"]]
    return lexical_ids, semantic_ids, lexical_ms, semantic_ms


def ranking_metrics(expected, ranking):
    top_five = ranking[:5]
    if not expected:
        return None, None
    hits = [item for item in expected if item in top_five]
    recall = len(hits) / len(expected)
    positions = [ranking.index(item) + 1 for item in expected if item in ranking]
    reciprocal_rank = 1.0 / min(positions) if positions else 0.0
    return recall, reciprocal_rank


def index_identity():
    database = settings.LLM_INDEX_DIR / DB_FILENAME
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        vector_count = DocumentChunksTable.count(connection)
        indexed_documents = connection.execute(
            "SELECT count(*) FROM document_meta",
        ).fetchone()[0]
        schema = IndexMetaTable.get_schema_version(connection)
    return {
        "path": str(database),
        "sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "bytes": database.stat().st_size,
        "schema": schema,
        "vector_count": vector_count,
        "indexed_documents": indexed_documents,
    }


def evaluate(definition):
    user_model = get_user_model()
    service = user_model.objects.get(username="odoo-integration")
    identities = {
        persona: user_model.objects.get(username=username)
        for persona, username in definition["personas"].items()
    }
    tag = Tag.objects.get(name=TAG_NAME)
    by_checksum = {document.checksum: document.id for document in tag.documents.all()}
    document_ids = {
        item["key"]: by_checksum[document_checksum(definition["version"], item)]
        for item in definition["documents"]
    }
    scopes = {
        persona: sorted(
            document_ids[item["key"]]
            for item in definition["documents"]
            if persona in item["visible_to"]
        )
        for persona in definition["personas"]
    }
    cases = []
    for item in definition["documents"]:
        persona = item["visible_to"][0]
        for sequence, (query, language, category) in enumerate(item["queries"], 1):
            cases.append(
                {
                    "id": f"{item['key']}-{sequence}",
                    "query": query,
                    "language": language,
                    "category": category,
                    "persona": persona,
                    "expected": [document_ids[item["key"]]],
                },
            )
    for sequence, (persona, query, language, category) in enumerate(
        definition["negative_queries"],
        1,
    ):
        cases.append(
            {
                "id": f"negative-{sequence}",
                "query": query,
                "language": language,
                "category": category,
                "persona": persona,
                "expected": [],
            },
        )

    client = APIClient()
    client.force_authenticate(user=service)
    results = []
    unauthorized = 0
    exact_regressions = 0
    latencies = {"lexical_ms": [], "semantic_ms": []}
    for case in cases:
        scope = scopes[case["persona"]]
        lexical, semantic, lexical_ms, semantic_ms = request_rankings(
            client,
            case["query"],
            scope,
        )
        hybrid = fuse(lexical, semantic, case["query"])
        latencies["lexical_ms"].append(lexical_ms)
        latencies["semantic_ms"].append(semantic_ms)
        unauthorized += sum(
            item not in set(scope) for item in lexical + semantic + hybrid
        )
        lexical_recall, lexical_rr = ranking_metrics(case["expected"], lexical)
        semantic_recall, semantic_rr = ranking_metrics(case["expected"], semantic)
        hybrid_recall, hybrid_rr = ranking_metrics(case["expected"], hybrid)
        if EXACT_QUERY.search(case["query"]) and lexical_recall and not hybrid_recall:
            exact_regressions += 1
        results.append(
            {
                **case,
                "lexical_top5": lexical[:5],
                "semantic_top5": semantic[:5],
                "hybrid_top5": hybrid[:5],
                "lexical_recall_at_5": lexical_recall,
                "semantic_recall_at_5": semantic_recall,
                "hybrid_recall_at_5": hybrid_recall,
                "lexical_rr": lexical_rr,
                "semantic_rr": semantic_rr,
                "hybrid_rr": hybrid_rr,
                "lexical_ms": round(lexical_ms, 3),
                "semantic_ms": round(semantic_ms, 3),
            },
        )

    positive = [item for item in results if item["expected"]]
    permission_checks = []
    all_ids = sorted(document_ids.values())
    for persona, user in identities.items():
        persona_client = APIClient()
        persona_client.force_authenticate(user=user)
        response = persona_client.post(
            "/api/documents/semantic_search/",
            {
                "query": "confidential evidence canary invoice payroll",
                "document_ids": all_ids,
                "limit": 50,
            },
            format="json",
            HTTP_ACCEPT="application/json; version=10",
            HTTP_HOST="paperless-webserver",
        )
        returned = [item["id"] for item in response.data.get("results", [])]
        leaked = [item for item in returned if item not in scopes[persona]]
        unauthorized += len(leaked)
        permission_checks.append(
            {
                "persona": persona,
                "status": response.status_code,
                "returned": returned,
                "unauthorized": leaked,
            },
        )

    config = AIConfig()
    output = {
        "definition": definition["version"],
        "variant": VARIANT,
        "question_count": len(cases),
        "positive_question_count": len(positive),
        "negative_question_count": len(cases) - len(positive),
        "embedding": {
            "backend": config.llm_embedding_backend,
            "model": config.llm_embedding_model,
            "chunk_size": config.llm_embedding_chunk_size,
            "context_size": config.llm_context_size,
        },
        "metrics": {
            "lexical_recall_at_5": round(
                statistics.fmean(item["lexical_recall_at_5"] for item in positive),
                4,
            ),
            "semantic_recall_at_5": round(
                statistics.fmean(item["semantic_recall_at_5"] for item in positive),
                4,
            ),
            "hybrid_recall_at_5": round(
                statistics.fmean(item["hybrid_recall_at_5"] for item in positive),
                4,
            ),
            "lexical_mrr": round(
                statistics.fmean(item["lexical_rr"] for item in positive),
                4,
            ),
            "semantic_mrr": round(
                statistics.fmean(item["semantic_rr"] for item in positive),
                4,
            ),
            "hybrid_mrr": round(
                statistics.fmean(item["hybrid_rr"] for item in positive),
                4,
            ),
            "unauthorized_result_count": unauthorized,
            "exact_identifier_regressions": exact_regressions,
            "negative_queries_with_results": sum(
                bool(item["hybrid_top5"]) for item in results if not item["expected"]
            ),
        },
        "latency": {
            name: {
                "cold": round(values[0], 3),
                "warm_median": round(statistics.median(values[1:]), 3),
                "warm_p95": percentile(values[1:], 0.95),
            }
            for name, values in latencies.items()
        },
        "index": index_identity(),
        "permission_checks": permission_checks,
        "results": results,
    }
    serialized = json.dumps(output, indent=2, sort_keys=True) + "\n"
    if OUTPUT_PATH:
        descriptor = os.open(OUTPUT_PATH, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
        console_output = {
            key: output[key]
            for key in (
                "definition",
                "variant",
                "question_count",
                "positive_question_count",
                "negative_question_count",
                "embedding",
                "metrics",
                "latency",
                "index",
            )
        }
    else:
        console_output = output
    print(  # noqa: T201 - management-shell contract writes JSON to stdout
        json.dumps(console_output, sort_keys=True),
    )


definition = load_definition()
if ACTION == "seed":
    seed(definition)
elif ACTION == "cleanup":
    cleanup()
elif ACTION == "evaluate":
    evaluate(definition)
else:
    raise RuntimeError(f"Unsupported evaluation action: {ACTION}")
