MultiModal Visual Search System
An semantic image search system built with CLIP, Qdrant, FastAPI, and React. It finds images from either a natural-language description or a reference image, then makes the system's performance visible in the product itself.

Github : https://github.com/Jaswanth19-596/VisualSearchSystem

What changed and why it matters

| Capability | Problem it solves | Evidence exposed by the app |
| --- | --- | --- |
| TTL/LRU query cache + single-flight requests | Repeated and concurrent searches were recomputing a CLIP embedding and Qdrant lookup. | `cache_status`, embedding/vector timing per result, hit rate, p50 and p95. |
| Safe upload pipeline | A large or malformed image could consume memory and model time before rejection. | Requests are streamed, limited to 12 MB by default, pixel-limited, and rejected before inference. |
| Incremental index manifest | Every CSV re-run embedded every document, even when nothing changed. | Ingestion reports `indexed`, `unchanged`, `invalid_images`, and uploaded batches. |
| Stable Qdrant IDs | Positional IDs change meaning whenever CSV rows move. | UUIDv5 IDs are derived from `image_name`, so updates upsert the same source document. |

## Architecture

```text
React search UI
     │ POST text/image
     ▼
FastAPI → SearchService → TTL/LRU cache ── hit → response + trace
                  │ miss
                  ▼
             CLIP embedder → Qdrant → cache → response + trace

CSV + images → fingerprint manifest → changed documents → CLIP batch → Qdrant upsert
```


## 1. Online search: reduce repeated inference and vector reads

### Baseline problem

Each search always ran two expensive remote/compute stages: CLIP embedding generation and a Qdrant nearest-neighbour lookup. The service logged elapsed time but returned no timings, did not normalize equivalent text, and did not protect against concurrent duplicate requests.

### Measured baseline and impact — 17 August 2026

I parsed the 11 warm text-search samples already recorded in `app.log`; the cold-start request was excluded because it includes one-time model initialization.

| Measurement | Result | Meaning |
| --- | ---: | --- |
| Historical uncached p50 | **260 ms** | Typical warm query before the cache. |
| Historical uncached p95 | **320 ms** | Tail latency before the cache. |
| Historical uncached mean | **264.55 ms** | Baseline used for the time-saved estimate. |
| Repeat-query trial | **100 requests → 1 retrieval** | **99 CLIP + Qdrant executions avoided (99%)**. |
| Cache-hit service overhead | **0.0037 ms p50**, **0.0042 ms p95** | In-process lookup only; HTTP/network time is not included. |

The repeat trial used the production `SearchService`, canonicalization, TTL/LRU cache, and single-flight logic with an in-memory counting retriever. It therefore proves avoided work exactly, but does **not** claim that 0.0037 ms is the browser-to-server latency. The real historical baseline is retained separately so the comparison is honest.

### Decision

I added a process-local, thread-safe TTL/LRU cache keyed by a SHA-256 digest of the canonical query. Text canonicalization case-folds and collapses whitespace; images use a content hash, so a renamed copy still reuses the result. A per-key single-flight gate makes concurrent identical queries share the first computation. I also moved text requests from a URL path to a JSON body so the query is less likely to appear in proxy access logs.

### Trade-offs

- A five-minute TTL favors fast, stable results; it can return stale results until expiration. This is appropriate when the image collection changes through the controlled ingestion pipeline. Reduce `SEARCH_CACHE_TTL_SECONDS` if freshness dominates.
- The cache is in-memory, which keeps latency low and avoids another network dependency. It is intentionally per-process; production horizontal scale would use Redis with the same key contract.
- Results, not tensors, are cached. This bounds memory and avoids retaining model or database resources.

### Ongoing measurement

`POST /query/text` returns `meta.cache_status` and per-stage milliseconds. `GET /metrics/search` returns hit rate plus a bounded rolling p50/p95. The React view displays the same trace after every search.

The automated test proves `"  RED   FOX "` and `"red fox"` result in **one** retriever invocation. A cache hit reports `0 ms` for both embedding and vector search because both operations were avoided. After deployment, use the observed `cache.hit_rate` from `/metrics/search` rather than assuming the 99% controlled-trial rate.

### Savings calculation

For `N` queries in a period, cache hit rate `H`, model/infrastructure cost per uncached request `C`, and average uncached end-to-end latency `L_miss` versus cached latency `L_hit`:

```text
avoided CLIP + Qdrant operations = N × H
estimated infrastructure savings = N × H × C
average latency saved per request = H × (L_miss - L_hit)
```

I have not measured a dollar figure because this repository has no Qdrant invoice, GPU pricing, or production request volume. Use the formula with observed traffic and billing data; the measurable result today is **99 avoided retrievals out of 100 equivalent requests** in the controlled trial.

### Interview-ready explanation

> I instrumented the search path first because I did not want to optimize blind. The existing warm-query logs had a 260 ms p50 and 320 ms p95. I canonicalized text, content-hashed images, and used a bounded TTL/LRU cache with a single-flight gate to stop cache stampedes. In a 100-request repeat trial, the service executed the retriever once—avoiding 99 CLIP and Qdrant operations. The client now shows whether a result was fresh or cached, and the API exports hit rate and p95, so I can recompute that impact from production traffic. I chose an in-memory cache for the single-service stage and would promote the same contract to Redis when scaling horizontally.

## 2. Data ingestion: make re-indexing incremental and safe

### Baseline problem

The original indexer embedded every CSV row on every run. Its Qdrant point IDs were positional (`i + j`), so a row reorder could make an old identity point to a different source image. It also modified batches while processing them, which made the code hard to reason about when images failed.

### Decision

I built an atomic JSON manifest. For each source image it records a fingerprint of the image name, caption, file size, and modification time. On later runs unchanged documents are skipped before loading the image or calling CLIP. Changed documents use a deterministic UUIDv5 generated from `image_name`, so Qdrant upserts replace the same source record independent of CSV order. The indexer returns an `IndexingReport` for every run.

### Trade-offs

- File metadata is far cheaper than hashing full image contents. It reliably detects normal image edits, but it intentionally trusts filesystem timestamps; use a content hash if the storage system can preserve timestamps across mutations.
- The manifest is local and atomic, ideal for one ingest worker. A distributed ingest pipeline would keep the fingerprint/version alongside the record in Qdrant or a shared metadata store.
- The source key assumes each `image_name` is unique in the source CSV. If one image needs multiple captions, model it as a distinct document ID first.

### Measured impact — 17 August 2026

I ran the production `Indexer` twice against the same controlled 12-document image corpus using in-memory counting substitutes for CLIP and Qdrant:

| Run | Indexed | Unchanged | CLIP embeddings | Qdrant upserts |
| --- | ---: | ---: | ---: | ---: |
| First ingestion | 12 | 0 | 12 | 12 |
| Second identical ingestion | 0 | 12 | 0 | 0 |

The repeat run avoided **12 of 12 embeddings and 12 of 12 upserts (100%)**. This validates the no-change path; measure the actual `IndexingReport` on the real corpus to calculate production savings.

### Ongoing measurement

Run `python ingest.py` twice without source changes. The first `IndexingReport` establishes the baseline; the second should show `indexed: 0`, `unchanged: N`, and `batches_uploaded: 0`. This is a direct count of embeddings and Qdrant upserts avoided.

```text
avoided embeddings = documents unchanged
avoided Qdrant upserts = documents unchanged
ingestion compute saved = unchanged × (embedding cost + upsert cost)
```

### Interview-ready explanation

> Once serving was efficient, I optimized the offline side. Re-indexing a mostly unchanged corpus wasted the same model and database work, and positional point IDs were unsafe under CSV reordering. I introduced a per-document manifest and deterministic UUIDs. In a 12-document repeat-ingestion measurement, the first run embedded and upserted all 12 documents; the second embedded and upserted zero, so it avoided 100% of repeat work. On the real corpus, the ingest report gives us the same count every run.

## Demo script

1. Search for `laughing penguin`; point out the **Fresh search** trace and the stage timings.
2. Search the same phrase again; show **Cache hit**, `0 ms` embedding/vector work, and the hit-rate change.
3. Run `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 scripts/measure_impact.py` to reproduce the measured figures (or use the project Python that has the dependencies installed).
4. Run ingestion twice against the same small dataset and compare the two `IndexingReport` lines.

This sequence shows a full loop: build a feature, observe its bottleneck, make a measured optimization, then apply the same discipline to the data pipeline.
