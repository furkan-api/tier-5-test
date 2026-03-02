# GraphRAG — Turkish Legal Knowledge Graph

A production-ready **Graph-augmented Retrieval** (GraphRAG) system for Turkish law. Nodes represent articles, clauses, and court decisions from HMK, IK, TBK, and TMK. Relationships encode hierarchical structure, cross-references, court appeal chains, and contradictory rulings. Semantic search is powered by a multilingual sentence-transformer and Neo4j's native vector index.

---

## Architecture

```
graph_data/
 ├─ nodes/        (6 JSON files, 393 nodes)
 ├─ edges/        (structural_edges.json + edge_rules.json)
 ├─ ontology.json (10 node types, 13 edge types)
 └─ embeddings/   (persistent cache — auto-generated)
        │
        ▼
 Neo4jGraphBuilder + EmbeddingCache
  ├─ hash-based embed cache (skip unchanged)
  ├─ upsert nodes + vectors into Neo4j
  └─ apply structural + dynamic edge rules
        │
        ▼
   Neo4j 5.26 + APOC
  ├─ LegalNode graph with embeddings
  └─ 13 typed edge relationships
        │
        ▼
  FastAPI service (service/api.py)
  ├─ /query   ← vector search + graph expansion
  ├─ /node    ← single node / neighbourhood
  ├─ /build   ← trigger rebuild
  └─ /stats   ← graph statistics
        │
   ┌────┴────┐
   │         │
 CLI       MCP server
(tools/)  (service/mcp_server.py)
           └─ Claude Desktop / Cursor / Copilot
```

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Docker (for Neo4j)

### 2. Clone & install

```bash
git clone https://github.com/ApilexAI/GraphRAG.git
cd GraphRAG
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env   # then edit with your settings
```

Key variables in `.env`:

| Variable | Default | Description |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt connection |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `graphrag_password` | Neo4j password |
| `EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Embedding model |
| `EMBEDDING_DIMENSION` | `384` | Must match model output |
| `GRAPH_DATA_DIR` | `graph_data/` | Source JSON directory |
| `API_PORT` | `8000` | FastAPI listen port |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### 4. Start Neo4j

```bash
docker compose up -d neo4j
```

Neo4j Browser available at [http://localhost:7474](http://localhost:7474) (user: `neo4j`, password: `graphrag_password`).

### 5. Build the graph

```bash
./scripts/build.sh json
```

Or use the pipeline (embeds + builds in one pass with caching):

```bash
python -m service pipeline build
```

### 6. Start the API

```bash
./scripts/serve.sh
```

### 7. Test

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "kıdem tazminatı şartları", "top_k": 5, "hops": 2}'
```

---

## Shell Scripts

All shell scripts live in `scripts/`.

### `scripts/build.sh` — Build the graph

```
./scripts/build.sh <subcommand> [options]
```

| Subcommand | Description |
|---|---|
| `json` | Full rebuild from JSON files → Neo4j (clears DB by default) |
| `vector` | Re-embed all nodes and rebuild vector index (keeps edges) |
| `edges` | Drop and re-create all edges from `edge_rules.json` only |
| `similarity` | Create semantic similarity edges via Neo4j vector index |
| `status` | Show node/edge counts, types, degree stats, last build time |

```bash
./scripts/build.sh json                          # fresh build from graph_data/
./scripts/build.sh json --data /path/to/data     # custom data directory
./scripts/build.sh json --no-clean               # upsert without wiping Neo4j
./scripts/build.sh vector                        # re-embed everything
./scripts/build.sh vector --skip-edges           # re-embed only, keep edges
./scripts/build.sh edges                         # re-apply edge_rules.json
./scripts/build.sh similarity --threshold 0.85   # create similarity edges
./scripts/build.sh status                        # check current state
```

### `scripts/update.sh` — Incremental updates

```
./scripts/update.sh <subcommand> [options]
```

| Subcommand | Description |
|---|---|
| `nodes` | Upsert nodes from JSON (no DB clear, no re-embed) |
| `vector` | Re-embed specified nodes only |
| `edges` | Drop + recreate all edges (needed after adding nodes) |
| `full` | nodes + vector + edges in one pass |

```bash
./scripts/update.sh nodes                               # upsert all data files
./scripts/update.sh nodes --files kararlar_yargitay.json  # update one file
./scripts/update.sh vector --files new_data.json        # re-embed one file
./scripts/update.sh edges                               # rebuild all edges
./scripts/update.sh full                                # full incremental pass
./scripts/update.sh full --files new_kararlar.json      # add a new source file
```

All subcommands support:
- `--data DIR` — override data directory
- `--files f1.json,f2.json` — process specific files only

### `scripts/serve.sh` — Start API server

```bash
./scripts/serve.sh                          # default: 0.0.0.0:8000
./scripts/serve.sh --port 9000
./scripts/serve.sh --workers 4              # production multi-worker
./scripts/serve.sh --reload                 # dev auto-reload (single worker)
```

### `scripts/debug.sh` — Start API + CLI

Starts the API server (if not already running), then launches the interactive CLI.

```bash
./scripts/debug.sh
./scripts/debug.sh --port 9000
./scripts/debug.sh --api http://remote-host:8000
```

---

## REST API

Base URL: `http://localhost:8000`

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness + readiness check |
| `GET` | `/stats` | Graph statistics, node/edge type distribution |
| `POST` | `/query` | Semantic search + graph expansion |
| `GET` | `/node/{node_id}` | Single node with in/out edges |
| `GET` | `/node/{node_id}/neighborhood` | Neighbourhood subgraph |
| `POST` | `/build` | Trigger graph rebuild |

### `POST /query`

```json
{
  "query": "kıdem tazminatı şartları",
  "top_k": 10,
  "hops": 2,
  "score_threshold": 0.3,
  "max_context_chars": 8000,
  "include_context": true,
  "node_type_filter": ["madde", "fikra"]
}
```

Response includes `seed_nodes`, `expanded_count`, `subgraph_nodes`, `subgraph_edges`, `edge_types`, and `context` (formatted text ready to pass to an LLM).

### `GET /node/{node_id}/neighborhood`

```bash
GET /node/HMK_M1/neighborhood?hops=2
```

### `POST /build`

```json
{ "clean": true }
```

Set `clean: false` for incremental upsert without clearing the database.

Interactive docs: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## MCP Server (AI Agent Integration)

The MCP server exposes GraphRAG as tools for AI agents via [Model Context Protocol](https://modelcontextprotocol.io/).

### Start

```bash
python -m service.mcp_server
python -m service.mcp_server --api http://localhost:8000
```

### Tools exposed

| Tool | Description |
|---|---|
| `graphrag_search` | Semantic search + graph expansion, returns formatted context |
| `graphrag_node_detail` | Fetch a single node by exact ID |
| `graphrag_neighborhood` | Explore neighbourhood subgraph around a node |
| `graphrag_stats` | Graph statistics |

### Claude Desktop configuration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "graphrag": {
      "command": "python",
      "args": ["-m", "service.mcp_server"],
      "cwd": "/path/to/GraphRAG",
      "env": {
        "GRAPHRAG_API": "http://localhost:8000"
      }
    }
  }
}
```

---

## Interactive CLI

```bash
./scripts/debug.sh
```

| Command | Description |
|---|---|
| `<query text>` | Semantic search |
| `/node <id>` | Inspect a single node |
| `/nb <id> [hops]` | Neighbourhood subgraph |
| `/stats` | Graph statistics |
| `/set top_k 10` | Adjust query parameter |
| `/set hops 2` | Graph expansion depth |
| `/set threshold 0.4` | Similarity threshold |
| `/filter madde,fikra` | Filter results by node type |
| `/build` | Trigger graph rebuild |
| `/help` | Show all commands |

---

## Graph Data

### Source files (`graph_data/`)

```
graph_data/
├── ontology.json              # Master schema: node types, edge types, build config
├── nodes/                     # Node data files
│   ├── kanunlar.json          # Statute-level nodes (HMK, IK, TBK, TMK)
│   ├── maddeler.json          # All article & clause nodes (merged)
│   ├── kararlar.json          # All court decisions (merged)
│   ├── mahkemeler.json        # Court entity nodes
│   ├── hukuk_dallari.json     # Legal domain nodes
│   └── kavramlar.json         # Legal concept nodes
├── edges/
│   ├── structural_edges.json  # 1001 explicit edges
│   └── edge_rules.json        # 3 dynamic edge-building rules
├── embeddings/                # Persistent embedding cache (auto-generated)
│   ├── cache.npz
│   └── cache_meta.json
└── validation/
    └── schema.json            # JSON Schema for data validation
```

### Node types

| Type | Description |
|---|---|
| `kanun` | Statute (e.g. `kanun_hmk_6100`) |
| `madde` | Article (e.g. `HMK_M6`) |
| `fikra` | Clause / paragraph (e.g. `HMK_M6_F1`) |
| `bent` | Sub-clause / item |
| `madde_versiyon` | Historical version of an article |
| `karar` | Court decision |
| `karar_gerekce` | Reasoning section of a decision |
| `mahkeme` | Court entity (e.g. Yargıtay, BAM) |
| `hukuk_dali` | Legal domain (İş Hukuku, Borçlar Hukuku, …) |
| `kavram` | Legal concept (kıdem tazminatı, ibraname, …) |

### Edge types

| Type | Description |
|---|---|
| `ICERIR` | Parent → child containment (kanun → madde → fikra) |
| `UST_NODE` | Child → parent back-link |
| `AYNI_KANUN` | Nodes belonging to the same statute |
| `ATIF_YAPAR` | Cross-reference citation |
| `GEREKCESI` | Decision → reasoning |
| `KANUN_YOLU` | Appeal chain (first instance → BAM → Yargıtay) |
| `CELISIK_KARAR` | Contradictory decisions between courts |
| `ILGILI_KAVRAM` | Node → legal concept association |
| `HUKUK_DALI` | Node → legal domain classification |
| `YARGILAYAN` | Decision → court that issued it |
| `DEGISTIREN` | Amendment relationship between versions |
| `BENZER_ANLAM` | Semantic similarity edge (optional, via `./scripts/build.sh similarity`) |

### Node JSON schema

```jsonc
{
  "node_id": "HMK_M6",
  "node_type": "madde",
  "embed_text": "HMK madde 6 genel yetkili mahkeme ...",
  "metadata": {
    "kanun_kisaltma": "HMK",
    "madde_no": "6",
    "baslik": "Genel yetkili mahkeme"
  }
}
```

---

## Project Structure

```
GraphRAG/
├── service/                  # Production service package
│   ├── __main__.py           # python -m service entrypoint
│   ├── api.py                # FastAPI REST endpoints
│   ├── config.py             # Pydantic Settings (reads .env)
│   ├── neo4j_driver.py       # Async Neo4j driver singleton
│   ├── graph_builder.py      # Ontology-driven build pipeline
│   ├── graph_store.py        # Neo4j CRUD, schema, stats
│   ├── vector_search.py      # Neo4j native vector search + expansion
│   ├── query_engine.py       # High-level query orchestration
│   ├── embeddings.py         # Embedder abstraction (ST / OpenAI)
│   ├── embedding_cache.py    # Content-hash embedding cache (.npz)
│   ├── pipeline.py           # Pipeline CLI (embed/build/update/validate)
│   ├── mcp_server.py         # MCP stdio server for AI agents
│   └── migrate.py            # Migration from old FAISS output/
├── graph_data/               # Graph source data
│   ├── ontology.json         # Master schema
│   ├── nodes/                # Node JSON files (393 nodes)
│   ├── edges/                # Edge rules + structural edges
│   ├── embeddings/           # Persistent cache (auto-generated)
│   └── validation/           # JSON Schema
├── scripts/                  # Shell scripts
│   ├── build.sh              # Build subcommands
│   ├── update.sh             # Incremental updates
│   ├── serve.sh              # Start API server
│   └── debug.sh              # Start API + CLI
├── tools/                    # Development utilities
│   └── cli.py                # Textual TUI debug client
├── tests/                    # Tests & benchmarks
│   └── test_api.py           # API endpoint tests
├── data/                     # Raw source material
│   └── raw/                  # Law text + court decisions
├── docker-compose.yml        # Neo4j 5.26 + APOC
├── requirements.txt
└── .env                      # Local config (not committed)
```

---

## Development

### Run tests

```bash
venv/bin/python tests/test_api.py
```

### Migrate from legacy FAISS output

```bash
python -m service migrate
```

Reads `output/` (old NetworkX + FAISS build) and imports into Neo4j.

### python -m service shortcuts

```bash
python -m service              # start API server
python -m service build        # build graph (with embedding cache)
python -m service embed        # embed only (no graph build)
python -m service update       # incremental update
python -m service stats        # show stats
python -m service mcp          # start MCP server
python -m service migrate      # migrate from old output/
python -m service pipeline <cmd>  # full pipeline (embed/build/update/validate/cache-info)
```

---

## License

Proprietary — © Apilex AI
