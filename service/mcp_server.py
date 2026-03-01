"""
MCP (Model Context Protocol) server for GraphRAG.

Provides a stdio-based JSON-RPC 2.0 server that exposes GraphRAG
as tools for AI agents (Claude Desktop, Cursor, Copilot, etc.).

Usage:
    python -m service.mcp_server [--api URL]

Configure in Claude Desktop:
    {
      "mcpServers": {
        "graphrag": {
          "command": "python",
          "args": ["-m", "service.mcp_server"],
          "cwd": "/path/to/GraphRAG"
        }
      }
    }
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE = os.environ.get("GRAPHRAG_API", "http://localhost:8000")
for _i, _a in enumerate(sys.argv):
    if _a == "--api" and _i + 1 < len(sys.argv):
        API_BASE = sys.argv[_i + 1]

SERVER_NAME = "graphrag"
SERVER_VERSION = "2.0.0"
PROTOCOL_VERSION = "2024-11-05"


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{API_BASE}{path}", timeout=30) as r:
        return json.loads(r.read())


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


# ── Tool Definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "graphrag_search",
        "description": (
            "Search the Turkish legal knowledge graph using a natural language query. "
            "Returns relevant law articles (madde), paragraphs (fıkra), "
            "court decisions (karar), and their reasoning (karar_gerekçe) "
            "with full text and source references. "
            "Always use this tool first for any Turkish law question."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query in Turkish.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of most relevant seed nodes to return.",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20,
                },
                "expand_hops": {
                    "type": "integer",
                    "description": "Graph traversal hops from seed nodes.",
                    "default": 1,
                    "minimum": 0,
                    "maximum": 3,
                },
                "score_threshold": {
                    "type": "number",
                    "description": "Minimum similarity score (0.0–1.0).",
                    "default": 0.0,
                },
                "max_context_chars": {
                    "type": "integer",
                    "description": "Maximum total characters in the returned context.",
                    "default": 8000,
                },
                "node_type_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by node types: kanun, madde, fikra, karar, karar_gerekce",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "graphrag_node_detail",
        "description": (
            "Get the full text, metadata, and all connections of a specific node. "
            "Use when you have a node_id from a previous search."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "Exact node ID to retrieve.",
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "graphrag_neighborhood",
        "description": (
            "Explore the graph neighborhood around a specific node. "
            "Discovers related articles and connected legal concepts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "Center node ID for neighborhood exploration.",
                },
                "hops": {
                    "type": "integer",
                    "description": "How many hops from the center node.",
                    "default": 1,
                    "minimum": 1,
                    "maximum": 3,
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "graphrag_stats",
        "description": "Get statistics about the legal knowledge graph.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ── Tool Handlers ─────────────────────────────────────────────────────────────

def handle_search(args: dict) -> str:
    """Execute semantic search and format for LLM consumption."""
    payload = {
        "query": args["query"],
        "top_k": args.get("top_k", 5),
        "expand_hops": args.get("expand_hops", 1),
        "score_threshold": args.get("score_threshold", 0.0),
        "max_context_chars": args.get("max_context_chars", 8000),
        "include_context": True,
    }
    if "node_type_filter" in args:
        payload["node_type_filter"] = args["node_type_filter"]

    result = _post("/query", payload)

    parts = [
        f"Found {len(result.get('seed_nodes', []))} relevant sources "
        f"(subgraph: {result.get('subgraph_nodes', 0)} nodes, "
        f"{result.get('subgraph_edges', 0)} edges, "
        f"latency: {result.get('latency_ms', 0):.0f}ms)",
        "",
    ]

    # Seed nodes
    for i, seed in enumerate(result.get("seed_nodes", []), 1):
        nid = seed["node_id"]
        ntype = seed.get("node_type", "")
        score = seed.get("score", 0)
        preview = seed.get("text_preview", "")
        parts.append(f"[Source {i}] ({ntype}) {nid} [relevance: {score:.4f}]")
        parts.append(preview)
        parts.append("")

    # Context (if available)
    ctx = result.get("context", "")
    if ctx:
        parts.append("--- Expanded Context ---")
        parts.append(ctx)

    return "\n".join(parts)


def handle_node_detail(args: dict) -> str:
    """Fetch and format a node's full details."""
    nid = args["node_id"]
    safe = urllib.parse.quote(nid, safe="")
    node = _get(f"/node/{safe}")

    parts = [
        f"Node: {node.get('node_id', nid)}",
        f"Type: {node.get('node_type', '')}",
        "",
        "Full Text:",
        node.get("embed_text", "(no text)"),
    ]

    meta = node.get("metadata", {})
    if meta:
        parts.append("")
        parts.append("Metadata:")
        for k, v in meta.items():
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            parts.append(f"  {k}: {v}")

    out_edges = node.get("out_edges", [])
    in_edges = node.get("in_edges", [])
    if out_edges:
        parts.append(f"\nOutgoing edges ({len(out_edges)}):")
        for e in out_edges:
            parts.append(f"  → {e.get('target', '?')} ({e.get('edge_type', '?')})")
    if in_edges:
        parts.append(f"\nIncoming edges ({len(in_edges)}):")
        for e in in_edges:
            parts.append(f"  ← {e.get('source', '?')} ({e.get('edge_type', '?')})")

    return "\n".join(parts)


def handle_neighborhood(args: dict) -> str:
    """Fetch and format neighborhood data."""
    nid = args["node_id"]
    hops = args.get("hops", 1)
    safe = urllib.parse.quote(nid, safe="")
    nb = _get(f"/node/{safe}/neighborhood?hops={hops}")

    nodes = nb.get("nodes", [])
    edges = nb.get("edges", [])

    type_counts: dict[str, int] = {}
    for n in nodes:
        t = n.get("node_type", "?")
        type_counts[t] = type_counts.get(t, 0) + 1

    parts = [
        f"Neighborhood of '{nb.get('center', nid)}' ({nb.get('hops', hops)} hops)",
        f"Nodes: {len(nodes)}, Edges: {len(edges)}",
        f"Types: {', '.join(f'{k}: {v}' for k, v in type_counts.items())}",
        "",
        "Nodes:",
    ]
    for n in nodes[:30]:
        parts.append(f"  [{n.get('node_type', '?')}] {n['node_id']}: {n.get('text_preview', '')[:100]}")
    if len(nodes) > 30:
        parts.append(f"  ... and {len(nodes) - 30} more")

    return "\n".join(parts)


def handle_stats(_args: dict) -> str:
    """Fetch and format graph statistics."""
    data = _get("/stats")

    g = data.get("graph", {})
    parts = [
        "Graph Statistics",
        f"  Nodes: {g.get('nodes', '?')}",
        f"  Edges: {g.get('edges', '?')}",
        f"  Avg degree: {g.get('avg_degree', '?')}",
        f"  Max degree: {g.get('max_degree', '?')}",
        f"  Isolated: {g.get('isolated_nodes', '?')}",
    ]

    nt = data.get("node_types", {})
    if nt:
        parts.append("\nNode Types:")
        for k, v in sorted(nt.items(), key=lambda x: -x[1]):
            parts.append(f"  {k}: {v}")

    et = data.get("edge_types", {})
    if et:
        parts.append("\nEdge Types:")
        for k, v in sorted(et.items(), key=lambda x: -x[1]):
            parts.append(f"  {k}: {v}")

    m = data.get("manifest", {})
    if m:
        parts.append(f"\nBuilt: {m.get('built_at', '?')}")
        parts.append(f"Embedding: {m.get('embedding_model', '?')}")

    return "\n".join(parts)


TOOL_HANDLERS = {
    "graphrag_search": handle_search,
    "graphrag_node_detail": handle_node_detail,
    "graphrag_neighborhood": handle_neighborhood,
    "graphrag_stats": handle_stats,
}


# ── MCP JSON-RPC Handler ─────────────────────────────────────────────────────

def handle_request(request: dict) -> dict | None:
    """Handle a single MCP JSON-RPC 2.0 request."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                    "isError": True,
                },
            }

        try:
            text = handler(arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}]},
            }
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:500]
            except Exception:
                pass
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"API error (HTTP {e.code}): {body}"}],
                    "isError": True,
                },
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {type(e).__name__}: {e}"}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """MCP server — stdio transport."""
    import logging
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    log = logging.getLogger("mcp_server")
    log.info("GraphRAG MCP server starting (API: %s)", API_BASE)

    try:
        health = _get("/health")
        if health.get("graph_loaded"):
            log.info("Connected: %d nodes, %d edges", health.get("nodes", 0), health.get("edges", 0))
        else:
            log.warning("Connected but graph is empty")
    except Exception as e:
        log.error("Cannot reach API at %s: %s", API_BASE, e)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
