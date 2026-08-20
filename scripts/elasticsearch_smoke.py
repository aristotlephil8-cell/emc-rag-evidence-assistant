"""Real Elasticsearch 8.11 integration smoke test for CI and local diagnostics."""

from __future__ import annotations

import json
import os
import uuid

from app.models import Chunk, SourceLocator
from app.search import ElasticsearchIndex, SearchIndexError
from elasticsearch import BadRequestError, Elasticsearch

DIMENSION = 1024


def _vector(position: int) -> list[float]:
    values = [0.0] * DIMENSION
    values[position] = 1.0
    return values


def _chunk(chunk_id: str, text: str, embedding: list[float]) -> Chunk:
    document_id = "ci-smoke-document"
    return Chunk(
        chunk_id=chunk_id,
        document_id=document_id,
        filename="ci-smoke.txt",
        text=text,
        source=SourceLocator(
            document_id=document_id,
            filename="ci-smoke.txt",
            page_number=1,
            section_path=["CI smoke"],
            bbox=(0.0, 0.0, 100.0, 20.0),
            table_index=1,
            table_row=1,
            chunk_id=chunk_id,
        ),
        embedding=embedding,
    )


def main() -> None:
    url = os.environ.get("ELASTICSEARCH_URL", "http://127.0.0.1:9200")
    client = Elasticsearch(url, request_timeout=15)
    if not client.ping():
        raise RuntimeError(f"Elasticsearch is not reachable at {url}")
    version = client.info()["version"]["number"]
    if version != "8.11.3":
        raise AssertionError(f"expected Elasticsearch 8.11.3, received {version}")

    index_name = f"cvrag-ci-smoke-{uuid.uuid4().hex}"
    index = ElasticsearchIndex(client, index_name, DIMENSION, "ci-smoke-v1")
    try:
        index.ensure()
        mapping_response = client.indices.get_mapping(index=index_name)
        mapping = next(iter(mapping_response.values()))["mappings"]
        if mapping.get("dynamic") != "strict":
            raise AssertionError("index mapping is not dynamic=strict")
        embedding_mapping = mapping.get("properties", {}).get("embedding", {})
        if embedding_mapping.get("dims") != DIMENSION:
            raise AssertionError("dense vector mapping has the wrong dimension")

        first = _chunk(
            "ci-chunk-a",
            "Synthetic conducted emissions filter verification marker.",
            _vector(0),
        )
        second = _chunk(
            "ci-chunk-b",
            "Synthetic radiated immunity fixture calibration marker.",
            _vector(1),
        )
        initial_target = next(iter(client.indices.get_alias(name=index_name)))
        index.replace_document(first.document_id, [first, second])
        active_target = next(iter(client.indices.get_alias(name=index_name)))
        if active_target == initial_target:
            raise AssertionError(
                "document replacement did not switch the blue-green alias"
            )
        if index.count_document(first.document_id) != 2:
            raise AssertionError(
                "document replacement produced an incomplete chunk set"
            )
        stored = {candidate.chunk_id: candidate for candidate in index.all_candidates()}
        if (
            stored[first.chunk_id].source.table_index != 1
            or stored[first.chunk_id].source.table_row != 1
        ):
            raise AssertionError("table locator fields did not survive Elasticsearch round-trip")

        invalid = _chunk("invalid-dimension", "must not become visible", [1.0])
        try:
            index.replace_document(first.document_id, [invalid])
        except SearchIndexError as error:
            if error.code != "invalid_chunk_embedding":
                raise
        else:
            raise AssertionError("invalid replacement unexpectedly succeeded")
        rollback_target = next(iter(client.indices.get_alias(name=index_name)))
        if (
            rollback_target != active_target
            or index.count_document(first.document_id) != 2
        ):
            raise AssertionError(
                "failed replacement changed the active alias or chunk set"
            )

        lexical = index.bm25("conducted emissions filter", limit=2)
        if not lexical or lexical[0].chunk_id != first.chunk_id:
            raise AssertionError("BM25 did not rank the exact synthetic marker first")
        vector = index.vector(_vector(0), limit=2)
        if not vector or vector[0].chunk_id != first.chunk_id:
            raise AssertionError("kNN did not rank the matching vector first")

        try:
            client.index(
                index=index_name,
                document={"chunk_id": "invalid", "unexpected_field": "must fail"},
                refresh=True,
            )
        except BadRequestError:
            strict_rejected_unknown_field = True
        else:
            strict_rejected_unknown_field = False
        if not strict_rejected_unknown_field:
            raise AssertionError("strict mapping accepted an unknown field")

        print(
            json.dumps(
                {
                    "status": "passed",
                    "elasticsearch_version": version,
                    "strict_mapping": True,
                    "blue_green_replace": True,
                    "failed_replace_rollback": True,
                    "bm25_top_chunk": lexical[0].chunk_id,
                    "knn_top_chunk": vector[0].chunk_id,
                },
                sort_keys=True,
            )
        )
    finally:
        if client.indices.exists_alias(name=index_name):
            targets = client.indices.get_alias(name=index_name)
            for target in targets:
                client.indices.delete(index=target)


if __name__ == "__main__":
    main()
