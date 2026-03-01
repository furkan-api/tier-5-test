"""
FastAPI REST API for GraphRAG.

Endpoints:
    GET  /health            — Liveness + readiness check
    GET  /stats             — Graph statistics & build manifest
    POST /query             — Semantic search + graph expansion
    GET  /node/{node_id}    — Single node detail
    GET  /node/{node_id}/neighborhood — Neighborhood subgraph
    POST /build             — Trigger a (re)build
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import (
    DEFAULT_EMBEDDING_PROVIDER,
    QUERY_EXPAND_HOPS,
    QUERY_MAX_CONTEXT_CHARS,
    QUERY_SCORE_THRESHOLD,
    QUERY_TOP_K,
    OUTPUT_DIR,
    LOG_LEVEL,
)
from .graph_store import GraphStore

logger = logging.getLogger(__name__)

# ── Global state ──────────────────────────────────────────────────────────────

store = GraphStore(output_dir=OUTPUT_DIR)
_build_lock = False  # simple flag — single-process guard


def _create_embedder():
    """Lazy-import to avoid loading model weights at module level."""
    from .main import create_embedder
    return create_embedder()


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load graph at startup if a build exists on disk."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if store.has_build():
        logger.info("Loading existing graph build …")
        embedder = _create_embedder()
        store.load(embedder)
        logger.info("GraphRAG ready.")
    else:
        logger.warning(
            "No previous build found at %s. Use POST /build to create one.",
            store.output_dir,
        )
    yield
    logger.info("Shutting down.")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="GraphRAG API",
    description="Graph-based Retrieval Augmented Generation for Turkish Legal System",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response schemas ────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Search query text")
    top_k: int = Field(default=QUERY_TOP_K, ge=1, le=100)
    expand_hops: int = Field(default=QUERY_EXPAND_HOPS, ge=0, le=5)
    max_expanded: int = Field(default=50, ge=1, le=500)
    score_threshold: float = Field(default=QUERY_SCORE_THRESHOLD, ge=0.0, le=1.0)
    max_context_chars: int = Field(default=QUERY_MAX_CONTEXT_CHARS, ge=100, le=50000)
    include_context: bool = Field(default=True, description="Include merged context string")


class SeedNode(BaseModel):
    node_id: str
    score: float
    node_type: str = ""
    text_preview: str = ""


class QueryResponse(BaseModel):
    query: str
    seed_nodes: List[SeedNode]
    expanded_count: int
    subgraph_nodes: int
    subgraph_edges: int
    edge_types: Dict[str, int]
    context: Optional[str] = None
    latency_ms: float


class NodeResponse(BaseModel):
    node_id: str
    node_type: str
    embed_text: str
    metadata: Dict[str, Any]
    in_edges: List[Dict[str, Any]]
    out_edges: List[Dict[str, Any]]


class NeighborhoodResponse(BaseModel):
    center: str
    hops: int
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]


class BuildRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None


class BuildResponse(BaseModel):
    status: str
    message: str
    manifest: Optional[Dict[str, Any]] = None


class HealthResponse(BaseModel):
    status: str
    graph_loaded: bool
    nodes: int = 0
    edges: int = 0
    built_at: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    """Liveness and readiness check."""
    if store.is_ready:
        m = store.manifest
        return HealthResponse(
            status="ready",
            graph_loaded=True,
            nodes=m.total_nodes if m else 0,
            edges=m.total_edges if m else 0,
            built_at=m.built_at if m else None,
        )
    return HealthResponse(status="no_graph", graph_loaded=False)


@app.get("/stats", tags=["System"])
async def stats():
    """Full graph statistics and build manifest."""
    return store.stats()


@app.post("/query", response_model=QueryResponse, tags=["Query"])
async def query(req: QueryRequest):
    """
    Perform a GraphRAG query.

    1) Vector search finds seed nodes.
    2) BFS expands the neighborhood.
    3) Returns structured results + optional merged LLM context.
    """
    if not store.is_ready:
        raise HTTPException(503, "Graph not loaded. POST /build first.")

    t0 = time.perf_counter()
    result = store.query(
        query_text=req.query,
        top_k=req.top_k,
        expand_hops=req.expand_hops,
        max_expanded=req.max_expanded,
        score_threshold=req.score_threshold,
    )
    latency = (time.perf_counter() - t0) * 1000

    # Build seed node info
    rag = store.rag
    seeds = []
    for nid, score in result.seed_nodes:
        node_data = rag.nodes.get(nid, {})
        seeds.append(SeedNode(
            node_id=nid,
            score=round(score, 4),
            node_type=node_data.get("node_type", ""),
            text_preview=node_data.get("embed_text", "")[:120],
        ))

    # Edge type counts
    edge_types: Dict[str, int] = {}
    for _, _, d in result.subgraph.edges(data=True):
        et = d.get("edge_type", "?")
        edge_types[et] = edge_types.get(et, 0) + 1

    return QueryResponse(
        query=req.query,
        seed_nodes=seeds,
        expanded_count=len(result.expanded_nodes),
        subgraph_nodes=result.subgraph.number_of_nodes(),
        subgraph_edges=result.subgraph.number_of_edges(),
        edge_types=edge_types,
        context=result.to_context_string(max_chars=req.max_context_chars) if req.include_context else None,
        latency_ms=round(latency, 2),
    )


@app.get("/node/{node_id}", response_model=NodeResponse, tags=["Graph"])
async def get_node(node_id: str):
    """Return full data for a single node."""
    if not store.is_ready:
        raise HTTPException(503, "Graph not loaded.")

    rag = store.rag
    if node_id not in rag.nodes:
        raise HTTPException(404, f"Node '{node_id}' not found.")

    node = rag.nodes[node_id]
    graph = rag.graph

    out_edges = [
        {"target": v, "edge_type": d.get("edge_type", ""), "weight": d.get("weight", 1.0)}
        for _, v, d in graph.out_edges(node_id, data=True)
    ] if node_id in graph else []

    in_edges = [
        {"source": u, "edge_type": d.get("edge_type", ""), "weight": d.get("weight", 1.0)}
        for u, _, d in graph.in_edges(node_id, data=True)
    ] if node_id in graph else []

    return NodeResponse(
        node_id=node_id,
        node_type=node.get("node_type", ""),
        embed_text=node.get("embed_text", ""),
        metadata=node.get("metadata", {}),
        in_edges=in_edges,
        out_edges=out_edges,
    )


@app.get("/node/{node_id}/neighborhood", response_model=NeighborhoodResponse, tags=["Graph"])
async def get_neighborhood(node_id: str, hops: int = 1):
    """Return the neighborhood subgraph for a node."""
    if not store.is_ready:
        raise HTTPException(503, "Graph not loaded.")

    rag = store.rag
    if node_id not in rag.nodes:
        raise HTTPException(404, f"Node '{node_id}' not found.")

    sub = rag.get_node_neighborhood(node_id, hops=min(hops, 3))
    if sub is None:
        raise HTTPException(404, f"Node '{node_id}' not in graph.")

    nodes_out = []
    for nid, data in sub.nodes(data=True):
        node_data = rag.nodes.get(nid, {})
        nodes_out.append({
            "node_id": nid,
            "node_type": node_data.get("node_type", data.get("node_type", "")),
            "text_preview": node_data.get("embed_text", "")[:120],
        })

    edges_out = []
    for u, v, data in sub.edges(data=True):
        edges_out.append({
            "source": u,
            "target": v,
            "edge_type": data.get("edge_type", ""),
            "weight": data.get("weight", 1.0),
        })

    return NeighborhoodResponse(
        center=node_id,
        hops=hops,
        nodes=nodes_out,
        edges=edges_out,
    )


@app.post("/build", response_model=BuildResponse, tags=["System"])
async def build(req: BuildRequest = BuildRequest(), background_tasks: BackgroundTasks = None):
    """
    Trigger a graph (re)build.

    This is synchronous for small datasets (<1 min) to keep things simple.
    For large datasets, swap to a background task queue.
    """
    global _build_lock
    if _build_lock:
        raise HTTPException(409, "A build is already in progress.")

    _build_lock = True
    try:
        from .main import create_embedder
        embedder = create_embedder(provider=req.provider, model_name=req.model)
        manifest = store.build(embedder)
        return BuildResponse(
            status="success",
            message=f"Built in {manifest.build_duration_sec}s",
            manifest={
                "built_at": manifest.built_at,
                "nodes": manifest.total_nodes,
                "edges": manifest.total_edges,
                "node_types": manifest.node_types,
                "edge_types": manifest.edge_types,
                "embedding_model": manifest.embedding_model,
                "duration_sec": manifest.build_duration_sec,
            },
        )
    except Exception as e:
        logger.exception("Build failed")
        raise HTTPException(500, f"Build failed: {e}")
    finally:
        _build_lock = False
