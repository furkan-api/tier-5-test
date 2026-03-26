from fastapi import APIRouter, Depends

from app.api.deps import get_current_settings, get_milvus_collection
from app.core.config import Settings
from app.core.db import get_connection
from app.models import DocumentResult, SearchRequest, SearchResponse
from app.retrieval.aggregation import max_score
from app.retrieval.dense import search_chunks

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/search", response_model=SearchResponse)
def search(
    request: SearchRequest,
    settings: Settings = Depends(get_current_settings),
    collection=Depends(get_milvus_collection),
):
    chunk_results = search_chunks(collection, request.query, top_k_chunks=request.top_k * 5)
    ranked_docs = max_score(chunk_results, top_k=request.top_k)

    # Fetch metadata from PG for the ranked documents
    doc_ids = [doc_id for doc_id, _ in ranked_docs]
    metadata = {}
    if doc_ids:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT doc_id, court, daire, decision_date, esas_no, karar_no "
                "FROM documents WHERE doc_id = ANY(%s)",
                (doc_ids,),
            )
            for row in cur.fetchall():
                metadata[row[0]] = {
                    "court": row[1],
                    "daire": row[2],
                    "decision_date": row[3],
                    "esas_no": row[4],
                    "karar_no": row[5],
                }

    results = []
    for doc_id, score in ranked_docs:
        meta = metadata.get(doc_id, {})
        results.append(DocumentResult(
            doc_id=doc_id,
            score=round(score, 4),
            court=meta.get("court", ""),
            daire=meta.get("daire", ""),
            decision_date=meta.get("decision_date", ""),
            esas_no=meta.get("esas_no", ""),
            karar_no=meta.get("karar_no", ""),
        ))

    return SearchResponse(query=request.query, results=results, total=len(results))
