
## Project context

The project was designed to demonstrate end-to-end machine-learning engineering rather than to claim real financial-fraud performance. It uses a synthetic transaction generator with a configurable fraud rate and 28 anonymized numeric features. The system separates data generation, training-data extraction, feature preparation, model training, artifact verification, batch scoring, and online inference.

The primary objective was to show sound engineering decisions around reproducibility, model-serving safety, operational readiness, and honest evaluation boundaries.

Github: https://github.com/Jaswanth19-596/fraud-detection-mlsystem

## What I built

- Designed a data pipeline that generates labelled synthetic transactions in PostgreSQL and extracts a maturity-delayed, retraining-window snapshot as Parquet.
- Defined a DVC pipeline for reproducible training-data and model-artifact outputs.
- Implemented schema and data-quality validation, time-ordered train/validation/test splitting, leakage-safe scaling, and a class-balanced random-forest baseline for highly imbalanced data.
- Reserved validation data to select the decision threshold, maximizing recall at a target precision; reported final metrics on a separate held-out test partition.
- Persisted the trained estimator, scaler, feature contract, metadata, metrics, source-data checksum, and SHA-256 artifact manifest as one serving bundle.
- Built a batch-scoring CLI and FastAPI service for single and batch fraud-risk predictions.
- Added API-key enforcement for production mode, finite-value and feature-count validation, bounded batch sizes, request IDs, JSON logs, health probes, and Prometheus-compatible metrics.
- Containerized the service using a non-root image; the production-like Compose profile uses a read-only artifact mount, read-only root filesystem, and pinned infrastructure images.
- Added automated tests spanning feature preparation, model training, artifact integrity checks, API authentication, batch inference, metrics, and request correlation; CI compiles, tests, and builds the container image.

## Technical architecture

```text
Synthetic transaction generator
        -> PostgreSQL
        -> maturity-delayed Parquet snapshot (DVC)
        -> feature validation, split, and scaling
        -> class-balanced random-forest training
        -> signed-by-manifest model artifact bundle
        -> batch CLI or FastAPI inference service
```

The API exposes liveness, readiness, health, model-information, and metrics endpoints in addition to prediction endpoints. It verifies the artifact manifest before deserializing the model, preventing the service from loading incomplete or corrupted artifact bundles.

## Technologies

Python, FastAPI, Pydantic, scikit-learn, pandas, NumPy, SQLAlchemy, PostgreSQL, DVC, Docker, Docker Compose, GitHub Actions, Kafka (provisioned for a future streaming extension), joblib, PyArrow, and Prometheus-compatible metrics.

## Evidence and scale

- Trained the current reference artifact on **211,122 synthetic transactions**.
- The training, validation, and held-out test partitions contained **135,117**, **33,780**, and **42,225** rows respectively.
- The generated fraud prevalence was approximately **0.15%**, exercising an imbalanced-class workflow.
- The reference model used **200 trees** and a target precision of **90%** for threshold selection.
- The synthetic test set produced ROC AUC and average precision near 1.0. This result reflects intentionally separable synthetic features and must be described only as a pipeline-validation result, never as real-world fraud performance.
- Automated workflow coverage includes four end-to-end tests covering training, serving, authentication, metrics, request IDs, and artifact-tampering rejection.

## Optimization outcomes

The following outcomes were measured locally from the current verified artifact. They are appropriate for a portfolio or interview discussion when described as local, synthetic-data benchmarks—not production SLAs.

| Optimization | Why it was implemented | Measured outcome |
| --- | --- | --- |
| Vectorized batch scoring | Avoid repeated pandas, scaler, and model-call overhead for callers that can submit short batches. | Scoring 100 rows took **13.80 ms** versus **1,327.23 ms** in a one-record-at-a-time loop: **96.2× faster** and **7,249 rows/sec** locally. |
| Validation-selected decision threshold | A default 0.50 cutoff is not aligned automatically with a fraud-review operating point. | On the held-out synthetic test set, recall increased from **81.7%** at 0.50 to **100%** at the selected threshold; precision was **96.8%** at that recall-focused operating point. |
| Bounded-complexity random forest | Limit model size and serving cost while retaining a non-linear, class-balanced baseline. | A 200-tree model with maximum depth 12 and `min_samples_leaf=2` produced a complete four-file artifact bundle of **1.14 MB**. |
| Compact, verified serving bundle | Make startup and deployment predictable while rejecting incomplete or corrupted artifacts. | Model load plus SHA-256 manifest verification completed in **16.54 ms** locally. |
| Parquet + DVC pipeline | Avoid text-format overhead and make the training input/output reproducible. | The reference run processed **211,122** synthetic rows and recorded a DVC-tracked model bundle. |

## Engineering decisions worth highlighting

| Decision | Why it matters |
| --- | --- |
| Maturity-delayed extraction | Avoids training on very recent records whose labels may not yet be reliable. |
| Separate validation and test usage | Avoids selecting a decision threshold on the same held-out data used for final reporting. |
| Precision-aware threshold policy | Treats fraud detection as an operating-point problem, not only a ranking-metric problem. |
| Artifact manifest verification | Makes the deployed model bundle explicit and detects accidental corruption or partial deployment. |
| Explicit feature contract | Prevents silent feature-order drift between training and inference. |
| Readiness probe | Prevents traffic from reaching an API that has not loaded a verified model. |
| Structured logs and request IDs | Supports production debugging without logging sensitive transaction payloads. |
| Non-root, read-only container | Demonstrates least-privilege deployment practices. |

## Tailoring guidance for another model

When asked to create a job-application description from this brief:

1. Match the requested role. Prioritize ML lifecycle and evaluation for ML Engineer roles; API, containers, security, and CI for Backend/Platform roles; DVC, validation, and artifact lineage for MLOps roles.
2. Write in first person only if the user asks for a first-person statement; otherwise use concise action-led bullets.
3. Select 2–4 details most relevant to the role instead of listing every technology.
4. Preserve the synthetic-data limitation and avoid invented impact metrics.
5. Make technical decisions concrete: explain the problem, implementation, and reason rather than listing tools.
