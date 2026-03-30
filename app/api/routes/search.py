import logging

from fastapi import APIRouter, Depends

from app.api.deps import get_current_settings, get_milvus_collection, get_neo4j_session
from app.core.config import Settings
from app.core.db import get_connection
from app.models import DocumentResult, SearchRequest, SearchResponse
from app.retrieval.aggregation import max_score
from app.retrieval.dense import search_chunks
from app.retrieval.graph_retrieval import expand_and_rescore, expand_and_rescore_fallback

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/search", response_model=SearchResponse)
def search(
    request: SearchRequest,
    settings: Settings = Depends(get_current_settings),
    collection=Depends(get_milvus_collection),
    neo4j_session=Depends(get_neo4j_session),
):
    chunk_results = search_chunks(collection, request.query, top_k_chunks=request.top_k * 5)
    ranked_docs = max_score(chunk_results, top_k=request.top_k * 3)  # wider pool for graph

    # Graph-augmented retrieval
    if request.use_graph:
        try:
            with get_connection(settings.database_url) as conn:
                graph_results = expand_and_rescore(
                    ranked_docs,
                    neo4j_session,
                    conn,
                    top_k_seeds=5,
                    hops=settings.graph_expansion_hops,
                    graph_weight=settings.graph_score_weight,
                )
        except Exception as e:
            log.warning("Graph retrieval failed, falling back to dense: %s", e)
            graph_results = expand_and_rescore_fallback(ranked_docs)
    else:
        graph_results = expand_and_rescore_fallback(ranked_docs)

    top_results = graph_results[: request.top_k]
    doc_ids = [r.doc_id for r in top_results]
    score_map = {r.doc_id: r for r in top_results}

    # Fetch metadata from PG
    metadata: dict = {}
    if doc_ids:
        with get_connection(settings.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT doc_id, court, daire, decision_date, esas_no, karar_no, "
                    "COALESCE(pagerank_score, 0.0) "
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
                        "pagerank_score": float(row[6]),
                    }

    results = []
    for doc_id in doc_ids:
        meta = metadata.get(doc_id, {})
        gr = score_map[doc_id]
        results.append(DocumentResult(
            doc_id=doc_id,
            score=round(gr.final_score, 4),
            court=meta.get("court", ""),
            daire=meta.get("daire", ""),
            decision_date=meta.get("decision_date", ""),
            esas_no=meta.get("esas_no", ""),
            karar_no=meta.get("karar_no", ""),
            graph_score=round(gr.graph_score, 4),
            is_graph_expansion=not gr.is_seed,
            pagerank_score=round(meta.get("pagerank_score", 0.0), 6),
        ))

    return SearchResponse(query=request.query, results=results, total=len(results))
