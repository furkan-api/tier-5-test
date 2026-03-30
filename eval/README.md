# Evaluation Dataset

Gold standard evaluation dataset and harness for measuring retrieval quality. Two data files work together:

- **`gold_standard.json`** — 64 queries with relevance judgments. Each query lists which corpus documents are relevant and how relevant they are (graded 0-3).
- **`corpus_manifest.json`** — Parsed metadata for each document in `corpus/` (court, daire, law branch, esas/karar no, date). Built automatically by `scripts/build_corpus_manifest.py`.

The evaluation harness (`scripts/evaluate.py`) takes a retrieval system's ranked output, joins it against the gold standard's relevance grades, and computes metrics.

## Files

| File | Description |
|------|-------------|
| `gold_standard.json` | 64 queries with relevance judgments |
| `corpus_manifest.json` | Parsed metadata for each corpus document |
| `schema/` | JSON Schemas for validating both data files |
| `scripts/evaluate.py` | Evaluation harness: compute metrics, log to PostgreSQL, compare runs |
| `scripts/run_retrieval.py` | Batch retrieval runner: queries all gold standard queries through the pipeline → run file JSON |
| `scripts/validate_schema.py` | Validates gold_standard.json against acceptance criteria |
| `scripts/test_evaluate.py` | Tests for evaluate.py with hand-computed expected values |
| `scripts/build_corpus_manifest.py` | Parses `corpus/*.md` headers → corpus_manifest.json |
| `scripts/generate_candidate_queries.py` | LLM-assisted query generation → gold_standard.json |
| `scripts/migrate_filenames.py` | One-time migration script (already applied) for normalizing corpus filenames |
| `tests/` | Toy example data (3 queries, 10 docs) for harness testing |

## Acceptance Criteria

- [x] 50+ queries (currently 64)
- [x] 10+ queries with contradictory_pairs (currently 10)
- [x] All 3 judicial branches covered (hukuk, ceza, idari + anayasa)
- [x] 5+ unique daireler (currently 31)
- [ ] Second lawyer validation of 20+ queries (pending — see [Lawyer Validation Guide](#avukat-doğrulama-rehberi-lawyer-validation-guide) and [Doğrulanmış Sorgular](#doğrulanmış-sorgular) below)
- [x] Each query has at least 1 relevant doc and hard negatives

## Relevance Grading Scale

| Grade | Meaning | Example |
|-------|---------|---------|
| **3** | Directly on point | Query: muris muvazaası → doc is a 1. HD muris muvazaası ruling |
| **2** | Related | Query: muris muvazaası → doc is an HGK muvazaalı borç ikrarı decision |
| **1** | Tangentially relevant | Query: muris muvazaası → doc is a 1. HD property case not about muvazaa |
| **0** | Irrelevant (hard negative) | Query: muris muvazaası → doc is a 10. CD uyuşturucu kararı |

## Query Schema

| Field | Type | Description |
|-------|------|-------------|
| `query_id` | string | Unique identifier (Q001–Q064) |
| `query_text` | string | Natural language query in Turkish, as a lawyer would phrase it |
| `query_type` | enum | **topical** (find cases on a topic), **case_law_search** (find a specific known case), **statute_application** (how courts apply a specific article), **procedural** (procedural law questions), **contradictory_precedent** (find conflicting rulings) |
| `law_branch` | enum | **hukuk** (civil), **ceza** (criminal), **idari** (administrative), **anayasa** (constitutional), **cross_branch** |
| `relevant_court` | string[] | Expected courts, e.g. `["Yargıtay 1. HD", "HGK"]` |
| `relevance_judgments` | object[] | `{doc_id, relevance (0-3), rationale}` — the core eval data |
| `contradictory_pairs` | object[] | `{doc_a, doc_b, description}` — only on queries where opposing rulings exist |
| `difficulty` | enum | **easy** (keyword overlap with relevant docs), **medium** (related but different terms), **hard** (semantic understanding required) |

## Corpus Manifest Schema

Each entry in `corpus_manifest.json` describes one document in `corpus/`:

| Field | Type | Description |
|-------|------|-------------|
| `doc_id` | string | 16-char hex hash of `court\|daire\|esas_no` — deterministic identifier for each case |
| `filename` | string | Full filename including `.md` |
| `court` | string | Top-level court: Yargıtay, Danıştay, BAM, BİM, İlk Derece, AYM |
| `daire` | string | Specific chamber, e.g. "1. Hukuk Dairesi", "Hukuk Genel Kurulu" |
| `law_branch` | string | hukuk, ceza, idari, anayasa |
| `court_level` | int | 1=İlk Derece, 2=BAM/BİM, 3=Daire, 4=Kurul/İBK/AYM |
| `esas_no` | string | Case number, e.g. "2024/1977" |
| `karar_no` | string | Decision number, e.g. "2025/5810" |
| `decision_date` | string | DD.MM.YYYY format |
| `topic_keywords` | string[] | Keywords extracted from document header |

## Evaluation Harness

### Run File Format

A retrieval run is a JSON file mapping each query to a ranked list of document IDs:

```json
{
  "run_id": "baseline-bm25-v1",
  "config_label": "BM25 default params",
  "results": [
    {"query_id": "Q001", "retrieved_docs": ["doc-id-1", "doc-id-2", "..."]}
  ]
}
```

`retrieved_docs` is ordered by rank (index 0 = rank 1). Documents not in the gold standard's relevance_judgments are treated as relevance 0.

### Metrics

| Metric | Description |
|--------|-------------|
| Recall@K (5, 10, 20) | Fraction of relevant docs found in top K |
| NDCG@K (5, 10) | Ranking quality using graded relevance (0-3) |
| MRR | Reciprocal rank of the first relevant doc |
| Hit Rate@5 | 1.0 if any relevant doc in top 5, else 0.0 |

"Relevant" = relevance grade >= 1 for binary metrics (Recall, MRR, Hit Rate). NDCG uses the full graded scale.

Results are logged to PostgreSQL (same `legal_rag` database as the ingestion pipeline) with run ID, timestamp, config label, and git commit hash.

### Usage

```bash
# Evaluate a run file (results logged to PostgreSQL)
uv run python eval/scripts/evaluate.py --run-file path/to/run.json

# Compare two logged runs
uv run python eval/scripts/evaluate.py --run-id baseline-v1 --run-id experiment-v2

# Per-query breakdown for a stored run
uv run python eval/scripts/evaluate.py --run-id baseline-v1 --per-query
```

### Tests & Validation

```bash
# Run evaluation harness tests (toy example with hand-computed expected values)
uv run python eval/scripts/test_evaluate.py

# Validate gold_standard.json structure and acceptance criteria
uv run python eval/scripts/validate_schema.py
```

---

## Avukat Doğrulama Rehberi (Lawyer Validation Guide)

`gold_standard.json` dosyasındaki 64 sorgunun **en az 20 tanesinin** ikinci bir avukat tarafından bağımsız olarak incelenmesi gerekiyor. Bu, Tier 1'in tamamlanması için son kalan adım.

### Her sorgu için kontrol listesi

1. **Sorgu metni doğal mı?** `query_text` alanını okuyun. Bir Türk avukatı gerçekten böyle arar mı? Doğal değilse düzeltin.
2. **Relevance puanları doğru mu?** `relevance_judgments` dizisindeki her belge-puan eşleşmesini kontrol edin. Puanı değiştirirseniz `rationale` alanını da güncelleyin. Puanlama ölçeği yukarıdaki [Relevance Grading Scale](#relevance-grading-scale) tablosunda.
3. **Eksik belge var mı?** `corpus/` klasöründe sorguyla ilgili olup da listede bulunmayan bir karar varsa ekleyin.
4. **Çelişkili kararlar gerçek mi?** `contradictory_pairs` olan sorgularda çelişkinin gerçekliğini doğrulayın.
5. **Listeyi güncelleyin:** İncelediğiniz sorguyu aşağıdaki [Doğrulanmış Sorgular](#doğrulanmış-sorgular) listesine ekleyin.

### Hangi sorguları inceleyeyim?

Herhangi 20+ tanesini seçebilirsiniz. Farklı `law_branch` değerlerinden (hukuk, ceza, idari) karışık seçerseniz daha kapsamlı bir doğrulama olur.

### Somut örnek

Q015 sorgusunu inceleyelim:

```json
{
  "query_id": "Q015",
  "query_text": "Belirsiz alacak davası olarak açılan işçilik alacağı talebi",
  "relevance_judgments": [
    {"doc_id": "e-2016-6-k-2017-5-t-15-12-2017-1",                          "relevance": 3, "rationale": "directly on point"},
    {"doc_id": "9-hukuk-dairesi-e-2025-7970-k-2025-9649-t-8-12-2025-1",     "relevance": 3, "rationale": "directly on point"},
    {"doc_id": "9-hukuk-dairesi-e-2025-8279-k-2025-9607-t-8-12-2025-1",     "relevance": 3, "rationale": "directly on point"},
    {"doc_id": "22-hukuk-dairesi-e-2014-30303-k-2014-31288-t-12-11-2014-1", "relevance": 2, "rationale": "related topic/court"},
    {"doc_id": "22-hukuk-dairesi-e-2015-23643-k-2016-8185-t-16-3-2016-1",   "relevance": 2, "rationale": "related topic/court"},
    {"doc_id": "1-ceza-dairesi-e-2024-2954-k-2025-9052-t-16-12-2025-1",     "relevance": 0, "rationale": "same branch, different topic"}
  ],
  "contradictory_pairs": [
    {
      "doc_a": "9-hukuk-dairesi-e-2025-7970-k-2025-9649-t-8-12-2025-1",
      "doc_b": "22-hukuk-dairesi-e-2014-30303-k-2014-31288-t-12-11-2014-1",
      "description": "9. HD ve 22. HD arasında işçilik alacaklarının belirsiz alacak olarak talep edilip edilemeyeceği konusundaki görüş ayrılığı"
    }
  ]
}
```

**İnceleme süreci:**

1. **Sorgu metni:** "Belirsiz alacak davası olarak açılan işçilik alacağı talebi" — bir iş hukukçusu bunu böyle arar. **Doğal.**
2. **Puanlar:**
   - YİBBGK 2016/6 kararı = 3 → doğrudan belirsiz alacakla ilgili İBK. **Doğru.**
   - 9. HD kararları = 3 → doğrudan iş davası, belirsiz alacak bağlamı. **Doğru.**
   - 22. HD kararları = 2 → konuyla ilgili ama farklı daire. **Doğru.**
   - 1. Ceza kararları = 0 → ceza davası, hiç ilgisiz. **Doğru.**
3. **Eksik belge:** `corpus/` klasöründe başka bir 9. HD belirsiz alacak kararı var mı? Yok. **Tamam.**
4. **Çelişki:** 9. HD ve 22. HD arasındaki belirsiz alacak görüş ayrılığı bilinen bir içtihat çatışması. **Gerçek.**
5. **Sonuç — her şey doğru.** Sorguyu aşağıdaki [Doğrulanmış Sorgular](#doğrulanmış-sorgular) listesine ekleyin.

Eğer bir puan yanlış olsaydı — örneğin 22. HD kararının aslında doğrudan belirsiz alacak şartlarını tartıştığını düşünseydiniz:

```json
{"doc_id": "22-hukuk-dairesi-e-2014-30303-k-2014-31288-t-12-11-2014-1", "relevance": 3, "rationale": "belirsiz alacak şartları doğrudan tartışılmış"}
```

### Yeni belge ekleme formatı

```json
{"doc_id": "dosya-adi-md-uzantisi-olmadan", "relevance": 2, "rationale": "kısa açıklama"}
```

`doc_id` = `corpus/` klasöründeki dosya adı, `.md` uzantısı olmadan.

### Değişiklikten sonra doğrulama

```bash
uv run python eval/scripts/validate_schema.py
```

`ALL CHECKS PASSED ✓` çıktısını görmelisiniz. Görmüyorsanız JSON formatında bir hata var demektir — en yaygın sorun virgül eksikliğidir. Belgelerin tam metinleri: `corpus/<doc_id>.md`

### Doğrulanmış Sorgular

İncelenen sorguları bu listeye ekleyin (en az 20 gerekli):

<!-- İncelediğiniz sorgunun satırını [ ] → [x] olarak değiştirin -->
- [ ] Q001 – Q010
- [ ] Q011 – Q020
- [ ] Q021 – Q030
- [ ] Q031 – Q040
- [ ] Q041 – Q050
- [ ] Q051 – Q060
- [ ] Q061 – Q064
