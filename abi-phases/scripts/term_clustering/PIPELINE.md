# From conditional statements to candidate terms: how the pipeline works

This note explains the mechanism behind the scripts in this directory: how a
sentence becomes two nodes and an edge, why a cluster is a candidate term, how
edges between clusters are counted, and what the merges, the hierarchy and the
stability check add. `README.md` has the commands.

The running example is one sentence from the corpus:

> If adolescents engage in intrinsically motivated solitary activities, then
> they tend to report lower feelings of aloneliness.

## 1. A sentence becomes one edge and two nodes

The `conditional_statement` prompt asks for exactly the form "If X, then Y.",
so the split is mechanical (`split_conditionals.py`, a regular expression that
cuts at the last ", then"):

- X = "adolescents engage in intrinsically motivated solitary activities"
- Y = "they tend to report lower feelings of aloneliness"

Each half is embedded on its own (`embed_parts.py`: `text-embedding-3-large`
at 3,072 dimensions, the model the corpus vectors use). The sentence is kept as
the record that these two halves were joined, in this direction, by this
paper.

That is the whole idea: **halves are nodes, sentences are edges.** A sentence
embedding is a blend of X and Y: two claims that share X but conclude
differently land somewhere between their parts. After the split, X can sit next
to other phrasings of adolescent solitude and Y next to other phrasings of
loneliness going down, and the link between them is not lost.

Halves are deduplicated on a normalised key (case, spacing and the final period
folded), so a phrase that recurs is embedded once and counted with the number
of sentences and papers that use it.

## 2. A cluster is a candidate term

In the vector space, halves that mean the same thing land close together
whatever their wording: "adolescents engage in solitary activities",
"adolescents spend time alone", "young people seek solitude". `cluster.py`
scales the vectors to unit length, reduces them to 10 dimensions with UMAP
(cosine metric), and runs HDBSCAN, which finds regions that are dense relative
to their surroundings. Halves in no dense region stay unassigned: that is
noise, read as "no term applies yet", not as an error. HDBSCAN is chosen for
exactly that: it does not force every point into a cluster.

A cluster is therefore a set of paraphrases of one idea, with:

- a centroid (the average vector: where the idea lives);
- members (the paraphrases, each traceable to a paper and a chunk);
- support: distinct papers. Cluster size counts paraphrases and repeated
  phrasings, so it is not a measure of agreement;
- keywords (class-based TF-IDF, one document per cluster, the prompt's hedges
  removed) and central members (nearest the centroid in the original space).
  These are handles for a reviewer, not names.

Nothing about the domain is used. The same code that finds "behavioral
inhibition" also finds "tobacco control" for a paper on smoking.

The `sentences` variant clusters the whole sentences with the vectors already
in the store. It is the baseline: what the corpus gives without the split.

## 3. Edges between clusters come from the sentences

Every sentence connects the cluster of its X to the cluster of its Y.
Aggregating the sentences by (X cluster, Y cluster) gives a weighted, directed
graph over clusters:

```
[adolescent engagement in solitude]  --8 sentences, 3 papers-->  [decrease in loneliness]
```

An edge counts its sentences and its distinct papers; edges are ranked by
papers first. A sentence with either half in noise forms no edge. When both
halves fall in the same cluster the edge is a self-loop, usually a definition
or a measurement statement, and it is kept apart from the graph.

## 4. Merges and a broader/narrower tree

Density finds regions, not kinds. Two things a list of terms needs are missing
from HDBSCAN's output, and `hierarchy.py` proposes both from the cluster
centroids:

- **merges**: clusters whose centroids are at or above a cosine similarity
  threshold (0.90 by default) are one term, closed under transitivity;
- **a tree**: average-linkage agglomeration over the merged centroids, cut at
  cosine distances 0.25 and 0.40, gives parents that group two or more terms.

Both are proposals. A parent's keywords are a handle, not a name.

## 5. Stability

UMAP is random, so the same data with another seed gives slightly different
clusters. `stability.py` runs the pipeline with several seeds and scores each
pair of runs on the points both clustered: the majority mapping (each cluster
of one run mapped to the cluster of the other that holds most of its points,
and the share of points that land there) and the adjusted Rand index. High
agreement on what is clustered, with soft edges, is what to expect; membership
at a cluster's edge should be read as provisional.

## 6. Limits

- Consequents often keep a pronoun subject ("they tend to report lower
  loneliness"), so consequent clusters group by predicate and the subject
  lives in the antecedent. The current prompt version asks for explicit
  subjects, which reduces this.
- Cluster size counts paraphrases; distinct papers are the honest support.
- `min_cluster_size` trades the number of clusters against noise and has to
  grow with the corpus; the README records the value chosen and why.
- Methodological clusters ("model fit indices", "regression coefficients")
  are coherent, but they name the method of a study, not its subject. The
  report flags them with a word-list heuristic for review.
- The merges and the tree come from geometry only. They are not is-a
  judgements until someone reviews them.
