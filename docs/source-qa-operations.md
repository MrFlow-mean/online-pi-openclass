# Source QA operations

Source QA is an independent full-document retrieval path. It does not rewrite the existing directory catalog, `source_chapters`, or board content.

## Feature flags

| Variable | Default | Effect |
|---|---:|---|
| `OPENCLASS_SOURCE_QA_ENABLED` | `1` | Enables full-document indexing and scoped retrieval. Set to `0` to return to the legacy path. |
| `OPENCLASS_OPENDATALOADER_SHADOW` | `0` | Runs the pinned local OpenDataLoader worker for comparison metadata only. |
| `OPENCLASS_SOURCE_QA_FAST_PARSER` | `native` | Set to `opendataloader` after shadow acceptance. |
| `OPENCLASS_SOURCE_QA_ENHANCEMENT_ENABLED` | `0` | Enables persisted MinerU and Docling page enhancement. |
| `OPENCLASS_SOURCE_QA_MODEL_URL` | empty | Loopback URL for the BGE-M3 embedding and reranker worker. Empty uses the deterministic fallback. |
| `OPENCLASS_SOURCE_QA_SQLITE_VEC_ENABLED` | `1` | Uses `sqlite-vec` when the 1024-dimensional BGE provider is active. |

Parser commands are configured with `OPENCLASS_OPENDATALOADER_COMMAND`, `OPENCLASS_MINERU_COMMAND`, and `OPENCLASS_DOCLING_COMMAND`. Each command must be an executable local path. The API passes only local file paths and receives `ParsedDocumentV2` JSON.

## Rollout

1. Install each worker in its own Python 3.12 virtual environment.
2. Start the BGE model server on `127.0.0.1` and warm both models.
3. Enable OpenDataLoader shadow mode and save parser comparison metadata.
4. Set OpenDataLoader as the fast parser only after the digital-PDF acceptance subset passes.
5. Enable enhancement only after MinerU and Docling pass their respective scan and complex-layout subsets.
6. Keep each parser worker at concurrency one on the 32 GB Apple Silicon baseline.

The fast index reaches `ready` before enhancement. Page jobs are persisted in `source_qa_page_quality`; interrupted `running` pages return to `pending` on API startup. Enhanced pages publish in one SQLite transaction, increment `qa_index_version`, and leave the previous fast page available if parsing fails.

## Locality and authorization

Parser workers and model workers run locally. Retrieval always filters by `owner_user_id`, `package_id`, and selected `source_ingestion_id`; `sqlite-vec` uses a per-source partition key in addition to the relational filters. The API revalidates content hashes and chapter ownership before search.
