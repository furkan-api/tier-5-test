from __future__ import annotations

from pymilvus import MilvusClient

from app.core.config import get_settings
from app.retrieval.embeddings import embed_texts, get_embedding_client


def search_chunks(
    client: MilvusClient,
    query: str,
    top_k_chunks: int = 100,
    model: str | None = None,
) -> list[dict]:
    """Search Milvus for nearest chunks to query.

    Returns list of {chunk_id, doc_id, score} dicts, sorted by score descending.
    """
    embedding_client = get_embedding_client()
    query_vec = embed_texts(embedding_client, [query], model=model)[0]

    collection_name = get_settings().collection_name
    results = client.search(
        collection_name=collection_name,
        data=[query_vec],
        anns_field="vector",
        search_params={"metric_type": "COSINE", "params": {"nprobe": 16}},
        limit=top_k_chunks,
        output_fields=["chunk_id", "doc_id"],
    )

    return [
        {
            "chunk_id": hit["entity"].get("chunk_id"),
            "doc_id": hit["entity"].get("doc_id"),
            "score": hit["distance"],
        }
        for hit in results[0]
    ]
