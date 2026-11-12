# Candidate terms from the conditional statements

Question: do the conditional statements extracted from the corpus ("If X, then
Y.") form clusters dense enough to be named as terms, and do those clusters
relate to each other? These scripts answer it offline, from a copy of the
vector store. Nothing here is imported by the application, and nothing writes
back to the stores.

Every item of the `conditional_statement` prompt has the same shape, so
phrasing is constant and only meaning separates the points. Each sentence is
split into its antecedent X and its consequent Y; the halves are embedded and
clustered, each cluster is a candidate term, and each sentence is an edge from
its antecedent's cluster to its consequent's. `PIPELINE.md` explains the
method.

## Run

Every script declares its own dependencies (PEP 723), so `uv run` needs no
project sync. Data and outputs land in `storage/spikes/term_clustering/`,
which is gitignored. Run from `abi-phases/`.

```sh
T=scripts/term_clustering
D=storage/spikes/term_clustering

# 1. Copy the conditional statements' vectors and payloads out of Qdrant (read-only).
#    The address comes from --host/--port or QDRANT_HOST/QDRANT_PORT; there is no default.
#    For production, open a tunnel first, e.g. ssh -N -L 16333:localhost:6333 -L 16334:localhost:6334 <host>
QDRANT_HOST=localhost QDRANT_PORT=16333 QDRANT_GRPC_PORT=16334 uv run $T/dump_vectors.py

# 2. Paper titles (read-only), where the DuckLake catalog is reachable, e.g. in the API
#    container with PGPASSWORD set; then copy the file to $D/papers.parquet.
python paper_titles.py --catalog "host=<db host> dbname=<catalog db> user=<user>" \
    --data-path storage/datasets/ --out /tmp/papers.parquet

# 3. Select and split. Default: the current conditional_statement version and the
#    default model, both read from the code (declared.py prints them).
uv run $T/split_conditionals.py
#    wider: --versions declared|all|<prompt ids>  --models all|<model ids>

# 4. Embed the distinct halves (text-embedding-3-large, 3072 dimensions, like the corpus).
#    --estimate prints the exact token count and price and stops; a run above
#    --max-cost (20 USD) stops before any call.
uv run $T/embed_parts.py --estimate
OPENAI_API_KEY=... uv run $T/embed_parts.py

# 5. Cluster both variants; several sizes at once share one cached reduction.
uv run $T/cluster.py --variant sentences parts --min-cluster-size 25 40 60

# 6. Merge near-duplicates and build the tree (parts), then check stability.
uv run $T/hierarchy.py --variant parts --min-cluster-size 40
uv run $T/stability.py --variant parts sentences --min-cluster-size 40 --seeds 42 1 2

# 7. Optional: rebuild an earlier run and compare its terms with the current ones.
uv run $T/split_conditionals.py --dir $D/baseline26 --dump-dir $D --discovered-before 2026-09-11 \
    --versions <earlier prompt ids> --models all
OPENAI_API_KEY=... uv run $T/embed_parts.py --dir $D/baseline26 --reuse $D
uv run $T/cluster.py --dir $D/baseline26 --variant parts --min-cluster-size 15
uv run $T/hierarchy.py --dir $D/baseline26 --variant parts --min-cluster-size 15
uv run $T/compare_runs.py --earlier $D/baseline26 --earlier-mcs 15 --later $D --later-mcs 40

# 8. The report: report.md, candidate_terms.csv/.md and term_graph.csv in $D.
uv run $T/report.py --min-cluster-size 40 --compare compare_parts_mcs15_umap10_vs_parts_mcs40_umap10.json
```

| step | script | writes |
|---|---|---|
| copy the vector store | `dump_vectors.py` | `phases_v2_extracted_items.{vectors.npy,meta.parquet}` |
| paper titles | `paper_titles.py` | `papers.parquet` |
| select and split | `split_conditionals.py` | `conditionals.parquet`, `sentences.vectors.npy`, `selection.json` |
| embed the halves | `embed_parts.py` | `parts.parquet`, `parts.vectors.npy`, `embedding.json` |
| cluster, keywords, term graph | `cluster.py` | `report_<stem>.md`, `clusters_<stem>.json`, `assignments_<stem>.parquet`, `edges_<stem>.parquet` |
| merges and tree | `hierarchy.py` | `merges_<stem>.parquet`, `terms_<stem>.parquet`, `hierarchy_<stem>.parquet`, `report_hierarchy_<stem>.md` |
| seed stability | `stability.py` | `stability_<stem>.json` |
| compare two runs | `compare_runs.py` | `compare_<stem>_vs_<stem>.json` |
| the report | `report.py` | `report.md`, `candidate_terms.csv`, `candidate_terms.md`, `term_graph.csv` |

`<stem>` is `<variant>_mcs<min_cluster_size>_umap<dimensions>`.

## Tests

```sh
make test-terms
# the same, without make:
uv run --no-project --with pytest --with numpy --with pandas --with pyarrow \
    --with scikit-learn --with scipy pytest scripts/term_clustering/tests -q
```

They cover the pure parts with small fixtures (the split, the selection and
its reasons, distinct halves, keywords, merge closure, the tree, edge
aggregation with paper support, the stability scores, run comparison and the
methodological flag) and run in seconds. They are not part of `make test`
because they need scikit-learn and scipy, which the project does not depend
on. `make test` does run `tests/term_clustering_declared_test.py`, which checks
that `declared.py` resolves the same prompt version and default model as the
module itself. `make lint` covers this directory.

## Method

L2-normalise, UMAP to 10 dimensions (cosine, 15 neighbours, `min_dist` 0),
HDBSCAN (excess of mass). HDBSCAN is used on purpose: it does not force every
point into a cluster, so "no term applies" shows up as noise instead of being
hidden in a bad cluster. Keywords are class-based TF-IDF (one document per
cluster, the prompt's hedges removed); a term's handle is its top keywords.
Central members are the ones nearest the cluster centroid in the original
space.

- `sentences` clusters whole sentences with the corpus vectors; `parts`
  clusters the distinct halves.
- In `parts`, each sentence whose two halves are clustered is an edge;
  edges are counted in sentences and in distinct papers and ranked by papers.
- Merges: clusters with centroids at 0.90 cosine similarity or more are one
  term (closed under transitivity). Tree: average linkage over the merged
  centroids, cut at cosine distances 0.25 and 0.40.
- Stability: three seeds; each pair scored on the points both clustered, by
  majority mapping (both directions) and adjusted Rand index.
- Methodological terms ("model fit indices") are flagged when at least half
  of their halves use words about how a study was done; the flag is a
  word list, for review.

## Results
