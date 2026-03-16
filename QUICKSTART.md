# GraphRAG — Quick Start

## 1. Clone the repo and set up the environment

```bash
git clone https://github.com/ApilexAI/GraphRAG.git
cd GraphRAG

python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

## 2. Create your `.env` file

```bash
cp .env.example .env
```

## 3. Start Neo4j

```bash
docker compose up -d neo4j
```

Wait ~15 seconds for Neo4j to be ready. Check via browser:
[http://localhost:7474](http://localhost:7474) — user: `neo4j`, password: `graphrag_password`

## 4. Build the graph from cache

The `output/` directory already contains pre-computed embeddings — no need to run the embedding model:

```bash
./scripts/build_from_cache.sh
```

What it does:
- Loads `output/nodes/*.jsonl` → Neo4j
- Applies `output/edges/*.jsonl` + `edge_rules.json` → creates relationships
- Loads `output/embeddings/cache.npz` → no re-embedding
- Creates semantic similarity edges

Expected output:
```
  Nodes   : 416
  Edges   : 3569
  ...
  Similarity edges created: 2079
```

### Options

```bash
./scripts/build_from_cache.sh --no-clean    # upsert without wiping existing Neo4j data
./scripts/build_from_cache.sh --skip-sim    # skip semantic similarity edge creation
```

## 5. Start the API server

```bash
./scripts/serve.sh
```

Health check:
```bash
curl http://localhost:8000/health
```

Test query:
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "kıdem tazminatı şartları", "top_k": 5, "hops": 2}'
```

## 6. Run the CLI

With the API server running:

```bash
venv/bin/python tools/cli.py
```

To connect to a different API address:
```bash
venv/bin/python tools/cli.py --api http://localhost:8000
```

### CLI commands

| Command | Description |
|---|---|
| `<text>` | Semantic search query |
| `/node <id>` | Inspect a single node |
| `/nb <id> [hops]` | Neighborhood subgraph |
| `/stats` | Graph statistics |
| `/set top_k 10` | Set query parameter |
| `/set hops 2` | Graph expansion depth |
| `/set threshold 0.3` | Minimum similarity score |
| `/filter decision,article` | Filter by node type |
| `/build` | Trigger graph rebuild |
| `/help` | Show help screen |

### Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Escape` | Focus search bar |
| `Ctrl+S` | Show statistics |
| `Ctrl+B` | Go back |
| `Ctrl+L` | Clear screen |
| `Ctrl+Q` | Quit |

---

## Adding new data

After adding new `.jsonl` files to `output/`, refresh the embeddings and reload:

```bash
# Runs on output/ — only embeds new/changed nodes
./scripts/refresh_cache_with_data.sh

# Then reload Neo4j without wiping existing data
./scripts/build_from_cache.sh --no-clean
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Cannot connect to Neo4j` | Run `docker compose up -d neo4j` |
| `Index query vector has X dimensions, but indexed vectors have Y` | Run `./scripts/build_from_cache.sh` — the vector index is automatically recreated with the correct dimension |
| `google.genai.errors.ClientError: 404` | Use `EMBEDDING_MODEL=gemini-embedding-2-preview` |
| CLI cannot connect | Make sure the API server is running: `curl http://localhost:8000/health` |
