from __future__ import annotations

from pymilvus import Collection

from app.retrieval.embeddings import embed_texts, get_embedding_client


def search_chunks(
    collection: Collection,
    query: str,
    top_k_chunks: int = 100,
    model: str | None = None,
) -> list[dict]:
    """Search Milvus for nearest chunks to query.

    Returns list of {chunk_id, doc_id, score} dicts, sorted by score descending.
    """
    client = get_embedding_client()
    query_vec = embed_texts(client, [query], model=model)[0]

    results = collection.search(
        data=[query_vec],
        anns_field="vector",
        param={"metric_type": "COSINE", "params": {"nprobe": 16}},
        limit=top_k_chunks,
        output_fields=["chunk_id", "doc_id"],
    )

    return [
        {
            "chunk_id": hit.entity.get("chunk_id"),
            "doc_id": hit.entity.get("doc_id"),
            "score": hit.score,
        }
        for hit in results[0]
    ]
