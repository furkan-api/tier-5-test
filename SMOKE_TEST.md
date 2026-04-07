# Smoke Test — Ayağa Kaldırma Rehberi

Bu branch; 100.101 kararın Milvus'a yüklendiği, Neo4j citation graph'ının oluşturulduğu
ve API'nin cluster'lara bağlanmaya hazır olduğu durumu temsil eder.

---

## Gereksinimler

| Araç | Sürüm |
|------|-------|
| Python | ≥ 3.10 |
| [uv](https://docs.astral.sh/uv/) | herhangi |
| Docker Desktop | herhangi (sadece PostgreSQL için) |
| OpenVPN | furkanozen2.ovpn |
| OpenAI API Key | text-embedding-3-small erişimi olan |

---

## 1. VPN Bağlantısı

Cluster'lara (Milvus + Neo4j) erişim için VPN şart.

```bash
# macOS
sudo openvpn --config furkanozen2.ovpn

# ya da Tunnelblick / GUI client ile aç
```

Bağlandıktan sonra doğrula:

```bash
nc -zv 10.20.47.192 19530   # Milvus
nc -zv 10.20.32.34  7687    # Neo4j
```

---

## 2. Ortam Kurulumu

```bash
# Bağımlılıkları kur
uv sync

# .env oluştur
cp .env.example .env
```

`.env` içinde doldurulması gerekenler:

```dotenv
OPENAI_API_KEY=sk-...        # query embedding için
NEO4J_PASSWORD=...           # Neo4j cluster şifresi
```

Diğer tüm değerler `.env.example`'da hazır.

---

## 3. PostgreSQL Başlat (sadece lokal geliştirme için)

```bash
docker compose up -d db
```

> **Not:** Milvus ve Neo4j lokalde çalışmıyor — cluster'dan geliyor. EC2 üzerinden çalıştırıyorsanız bu adımı atlayın.

---

## 4. API'yi Başlat

### Seçenek A — Lokal (VPN gerekli)

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Seçenek B — EC2 üzerinden (VPN'e gerek yok)

EC2 instance zaten VPN'e bağlı. SSH tunnel ile lokalde erişebilirsiniz:

```bash
ssh -i data.pem -L 8000:localhost:8000 ubuntu@34.245.184.84
```

> **Not:** `data.pem` izni `chmod 400 data.pem` olmalı.

EC2'da API başlatmak için:

```bash
ssh -i data.pem ubuntu@34.245.184.84
cd ~/graph-rag-r-d
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Başarılı startup log'u:

```
INFO: Application startup complete.
INFO: Uvicorn running on http://0.0.0.0:8000
```

Neo4j bağlanamasa bile API çalışmaya devam eder (dense-only fallback).

---

## 5. Test

### Health check

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### Arama sorgusu (graph-augmented)

```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "iş kazası nedeniyle tazminat",
    "top_k": 5,
    "use_graph": true
  }'
```

### Arama sorgusu (dense-only)

```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "boşanma davası velayet",
    "top_k": 10,
    "use_graph": false
  }'
```

### Beklenen yanıt formatı

```json
{
  "query": "iş kazası nedeniyle tazminat",
  "total": 5,
  "results": [
    {
      "doc_id": "ad65ca16d2752f93",
      "score": 0.8721,
      "court": "Yargıtay",
      "daire": "21. Hukuk Dairesi",
      "decision_date": "15.03.2024",
      "esas_no": "2023/1234",
      "karar_no": "2024/5678",
      "graph_score": 0.0412,
      "is_graph_expansion": false,
      "pagerank_score": 0.000612
    }
  ]
}
```

---

## Cluster Bilgileri

| Servis | Host | Port | Protokol |
|--------|------|------|----------|
| Milvus | 10.20.47.192 | 19530 | gRPC |
| Neo4j  | 10.20.32.34  | 7687  | Bolt/neo4j:// |
| Neo4j Browser | 10.20.32.34 | 7474 | HTTP |

---

## Cluster'daki Mevcut Veri

| Tablo / Koleksiyon | Kayıt Sayısı |
|--------------------|-------------|
| PostgreSQL `documents` | 100.101 |
| PostgreSQL `chunks` | 276.194 |
| Milvus `chunks` (vektörler) | 276.194 |
| Neo4j Document node | 100.101 |
| Neo4j CITES ilişkisi | 343 |

---

## Ingestion Pipeline (ihtiyaç olursa)

Cluster'ı sıfırdan doldurmak için sırasıyla:

```bash
# 1. Corpus'u S3'ten indir (smoke-test-md/)
uv run python -m app.ingestion.download_corpus --workers 30

# 2. PostgreSQL'e ingest et
uv run python -m app.ingestion.ingest
uv run python -m app.ingestion.chunk

# 3. S3 pre-embedded verileri Milvus'a yükle (embed.py çalıştırmaz)
uv run python -m app.ingestion.load_embedded --recreate

# 4. Citation graph → Neo4j
uv run python -m app.ingestion.build_graph
```

> `load_embedded` adımı OpenAI API çağırmaz — S3'teki hazır vektörleri kullanır.
