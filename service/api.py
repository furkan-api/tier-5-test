"""
FastAPI REST API for the production GraphRAG service.

Endpoints:
    GET  /health                    — Liveness + readiness check
    GET  /stats                     — Graph statistics & build manifest
    POST /query                     — Semantic search + graph expansion
    GET  /node/{node_id}            — Single node detail
    GET  /node/{node_id}/neighborhood — Neighborhood subgraph
    POST /build                     — Trigger a (re)build
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import get_settings
from .neo4j_driver import close_driver, get_driver
from .query_engine import GraphRAGEngine

logger = logging.getLogger(__name__)

# ── Global engine ─────────────────────────────────────────────────────────────

engine = GraphRAGEngine()


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: connect Neo4j at startup, close at shutdown."""
    s = get_settings()
    logging.basicConfig(
        level=getattr(logging, s.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Connect to Neo4j
    try:
        await get_driver()
        logger.info("Neo4j connected. GraphRAG service ready.")
    except Exception as e:
        logger.error("Failed to connect to Neo4j: %s", e)
        logger.warning("Service starting without Neo4j. Fix connection and restart.")

    yield

    # Shutdown
    await close_driver()
    logger.info("Shutting down.")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="GraphRAG Service",
    description=(
        "Production-grade Graph-based Retrieval Augmented Generation "
        "for the Turkish Legal System — backed by Neo4j"
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response Schemas ────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Search query text")
    top_k: int = Field(default=10, ge=1, le=100)
    expand_hops: int = Field(default=2, ge=0, le=5)
    max_expanded: int = Field(default=50, ge=1, le=500)
    score_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    max_context_chars: int = Field(default=8000, ge=100, le=50000)
    include_context: bool = Field(default=True)
    node_type_filter: Optional[List[str]] = Field(
        default=None,
        description="Filter results by node type (e.g., ['article', 'decision'])",
    )


class SeedNodeResponse(BaseModel):
    node_id: str
    score: float
    node_type: str = ""
    text_preview: str = ""


class QueryResponse(BaseModel):
    query: str
    seed_nodes: List[SeedNodeResponse]
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


class HealthResponse(BaseModel):
    status: str
    graph_loaded: bool
    nodes: int = 0
    edges: int = 0
    built_at: Optional[str] = None


class BuildRequest(BaseModel):
    clean: bool = Field(
        default=True,
        description="Clear existing graph before building",
    )


class BuildResponse(BaseModel):
    status: str
    message: str
    manifest: Optional[Dict[str, Any]] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    """Liveness and readiness check."""
    result = await engine.health_check()
    return HealthResponse(**result)


@app.get("/stats", tags=["System"])
async def stats():
    """Full graph statistics and build manifest."""
    return await engine.get_stats()


@app.post("/query", response_model=QueryResponse, tags=["Query"])
async def query(req: QueryRequest):
    """Perform a GraphRAG query: vector search + graph expansion."""
    try:
        result = await engine.query(
            query_text=req.query,
            top_k=req.top_k,
            expand_hops=req.expand_hops,
            max_expanded=req.max_expanded,
            score_threshold=req.score_threshold,
            max_context_chars=req.max_context_chars,
            node_type_filter=req.node_type_filter,
            include_context=req.include_context,
        )
    except Exception as e:
        logger.exception("Query failed")
        raise HTTPException(503, f"Query failed: {e}")

    # Build response
    seeds = [
        SeedNodeResponse(
            node_id=s.node_id,
            score=round(s.score, 4),
            node_type=s.node_type,
            text_preview=s.text_preview[:120],
        )
        for s in result.seed_nodes
    ]

    # Count edge types (filter out None keys from OPTIONAL MATCH nulls)
    edge_types: Dict[str, int] = {}
    for e in result.edges:
        et = e.get("edge_type") or "?"
        if not isinstance(et, str):
            et = str(et)
        edge_types[et] = edge_types.get(et, 0) + 1

    return QueryResponse(
        query=req.query,
        seed_nodes=seeds,
        expanded_count=len(result.expanded_nodes),
        subgraph_nodes=result.subgraph_node_count,
        subgraph_edges=result.subgraph_edge_count,
        edge_types=edge_types,
        context=result.to_context_string(max_chars=req.max_context_chars) if req.include_context else None,
        latency_ms=result.latency_ms,
    )


@app.get("/node/{node_id}", response_model=NodeResponse, tags=["Graph"])
async def get_node_endpoint(node_id: str):
    """Return full data for a single node."""
    node = await engine.get_node_detail(node_id)
    if node is None:
        raise HTTPException(404, f"Node '{node_id}' not found.")

    return NodeResponse(
        node_id=node.get("node_id", node_id),
        node_type=node.get("node_type", ""),
        embed_text=node.get("embed_text", ""),
        metadata=node.get("metadata", {}),
        in_edges=node.get("in_edges", []),
        out_edges=node.get("out_edges", []),
    )


@app.get("/node/{node_id}/neighborhood", response_model=NeighborhoodResponse, tags=["Graph"])
async def get_neighborhood_endpoint(node_id: str, hops: int = 1):
    """Return the neighborhood subgraph for a node."""
    result = await engine.get_node_neighborhood(node_id, hops=min(hops, 3))
    if result is None:
        raise HTTPException(404, f"Node '{node_id}' not found.")

    return NeighborhoodResponse(
        center=result["center"],
        hops=result["hops"],
        nodes=result["nodes"],
        edges=result["edges"],
    )


@app.post("/build", response_model=BuildResponse, tags=["System"])
async def build(req: BuildRequest = BuildRequest()):
    """Trigger a graph (re)build from source JSON data.

    This reads graph_data/ JSON files, generates embeddings,
    and writes everything into Neo4j.
    """
    from .graph_builder import Neo4jGraphBuilder

    try:
        builder = Neo4jGraphBuilder()
        meta = await builder.build(clean=req.clean)
        return BuildResponse(
            status="success",
            message=f"Built in {meta.get('build_duration_sec', 0)}s",
            manifest=meta,
        )
    except Exception as e:
        logger.exception("Build failed")
        raise HTTPException(500, f"Build failed: {e}")
