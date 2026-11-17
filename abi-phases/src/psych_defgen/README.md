# psych_defgen: evidence-based construct definitions

For a psychological construct such as "loneliness", this module returns:

- a **definition from the literature**: a sentence quoted from PubMed or PMC
  articles, found by linguistic patterns or, failing those, picked out of the
  evidence by an LLM that must quote it word for word;
- an **AI-synthesized definition**: an LLM summary of the top-ranked
  evidence passages, or none when the model finds the evidence insufficient;
- the ranked **evidence** behind both, with titles, authors, PMIDs and links;
- whether the **APA Dictionary of Psychology** has an entry for the term
  (only the link is returned, never the APA text).

It is our copy of [psych-defgen](https://github.com/uflcod/psych-defgen)
0.0.1b1 with every model call on a hosted service: NCBI E-utilities, the
OpenAI embeddings API, OpenRouter and the APA Dictionary website. Nothing
runs a local model or a browser. `UPSTREAM.md` lists what was kept, changed
and dropped.

Users ask for definitions through the HTTP API (and the Construct
Definitions app in Nexus): each request is a **run**, stored in a DuckLake
dataset and carried out by a Dagster job, because a definition takes minutes.

## How a definition is made

`factory.definition_service(configuration).define(term, max_results=…,
top_k=…, model=…, on_stage=…)` runs upstream's `run_pipeline` steps, and
reports each stage to `on_stage`:

| Stage | What happens |
| --- | --- |
| `apa` | Look the term up on dictionary.apa.org: `found`, `not_found`, `unavailable` or `request_error`. |
| `search` | Search PubMed for `"term"[Title/Abstract]` by relevance (up to `max_results`), then map PMIDs to PMC IDs. |
| `full_text` | Fetch PMC full texts that mention the term, falling back to PubMed abstracts for the rest; extract definition candidates and cut full texts into 180-word chunks (40 overlapping). |
| `ranking` | Embed five definition questions and every passage; rank by mean cosine similarity, keeping at most 2 passages per article, up to `top_k`. |
| `selection` | Fetch authors for the ranked evidence; pick the definition from the literature by pattern, else by LLM selection. |
| `synthesis` | Write the AI-synthesized definition from the top-`top_k` evidence. |

The result is upstream's dictionary (`term`, `apa_entry`, `definition`,
`definition_method`, `generated_definition`, `llm_usage`, `evidence`, `pmids`,
`pmid_to_pmcid`) plus:

- `embedding_model`: the model that ranked the evidence;
- `counts`: `pubmed_articles` found, `pmc_full_texts` used, PMIDs that needed
  `abstract_fallbacks`, `definition_candidates`, `retrieval_items` ranked;
- `upstream`: `"psych-defgen 0.0.1b1 (a625984)"`.

With no explicit definition, `definition` is upstream's sentence "No explicit
definition matching the selection criteria was identified in the retrieved
literature." and `definition_method` is null. `generated_definition` is null
when there is no evidence or the model answers upstream's "Insufficient
evidence to generate a definition."

A run with the defaults (100 articles) makes about two hundred NCBI requests and
takes minutes, mostly waiting on NCBI's rate limit: it belongs in a
background job, not a web request.

## Runs

A run is one definition request, a row of the dataset table
`psych_defgen.definition_runs`:

| Column | Holds |
| --- | --- |
| `run_id` | 32 hex digits that sort by creation time. |
| `term`, `max_results`, `top_k`, `model` | The request, with defaults filled in. |
| `embedding_model` | The configured embedding model when the run was created. |
| `status` | `pending` → `running` → `succeeded` or `failed`. |
| `stage` | The stage reached (`apa` … `synthesis`); kept once the run ends, so a failure shows where it happened. |
| `error` | Why a failed run failed: readable, never a secret value. |
| `created_at`, `started_at`, `finished_at` | ISO-8601 UTC (`2026-11-16T10:00:00Z`), null until reached. |
| `result` | The definition as JSON text, once the run succeeded. |

`ABIModule.on_initialized` creates the table if it is missing
(`DefinitionRunStore.ensure()`, safe on every start). Without a dataset
service the module logs a warning and stays inert: no table, no API, and the
sensors skip.

A run is written a handful of times: created, started, once per stage, and
finished, because DuckLake commits serialise (#635). Each change re-reads the
row and writes it against the catalog snapshot it read, retrying on a newer
one, so the job and the sensors never overwrite each other: a finished run
stays finished. Snapshots are catalog-wide, so other modules' commits cause
retries too. A stage write gives up after about 1.4 s and the run goes on
without it (stages only report progress); the writes that finish a run keep
trying for about 50 s, so a busy catalog does not cost a finished definition.

`application/runs.py` (`DefinitionRuns`) validates requests, lists and reads
runs, and moves them through their states; `execute()` is the job's body.

## Dagster job and sensors

`orchestrations/psych_defgen_orchestration.py`, loaded into the Dagster code
location like the other modules' orchestrations:

- **`psych_defgen_run_request_sensor`**, every 30 s, launches
  `psych_defgen_run` for the oldest pending run, with the run ID as the run
  key (and the `psych_defgen/run_id` tag). Runs go one at a time: they share
  the deployment's NCBI rate limit, which each Dagster run paces on its own.
  It also fails any pending or running run whose Dagster runs have all ended
  (canceled, or lost in a restart), so a stuck run never holds the queue.
- **`psych_defgen_run`** (op `define_construct`) marks the run running, calls
  `define()` with each stage written to the run, and stores the result, or
  the error. Launched by hand (from the launchpad, with only the run config),
  it tags its Dagster run itself so that the sensors recognise it. If the
  run was failed meanwhile, the result is not stored, a warning says so and
  the Dagster run fails. Errors:
  - a `DefinitionStepError` or `NotConfiguredError` message is stored as is
    (it names the step or the missing keys, never a value), and the Dagster
    run fails too;
  - any other exception is a bug: the run's error names its type and stage
    and points to the Dagster run's logs, and the exception is raised again.
- **`psych_defgen_run_failure_sensor`** marks the run failed when its Dagster
  run fails, for failures the job could not record itself (a crash, an
  out-of-memory kill). A run the job already failed keeps its own message.

## HTTP API

Under `/psych_defgen/api`, mounted by `ABIModule.api()`; every endpoint needs
the ABI API token (`Depends(require_abi_api_token)`). Errors are
`{"detail": "..."}`. A wiring error is logged and the engine keeps serving.

| Endpoint | Returns |
| --- | --- |
| `GET /settings` | `models`, `default_model`, `embedding_model`, the `max_results` and `top_k` limits (`default`, `min`, `max`), and `missing`: the unset required keys, out of `NCBI_EMAIL`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`. |
| `POST /runs` | `201` with the new pending run. Takes `{"term", "max_results"?, "top_k"?, "model"?}`. |
| `GET /runs?page=1&page_size=25` | `{"runs", "total", "page", "page_count"}`, newest first, runs without `result`. `page_count` is 0 when there are no runs. |
| `GET /runs/{run_id}` | The run with `result` (null until it succeeds), or `404`. |

`POST /runs` answers:

- `422` when `term`, trimmed, is not 1–200 characters, `max_results` is not
  1–200, `top_k` is not 1–20, or `model` is not one of `llm_models`;
- `409` naming the missing keys when any required key is unset; nothing is
  stored.

A body that is not JSON is a `422` too, with a string `detail` like every
other error.

`GET /runs` answers `422` when `page` is below 1, `page_size` is not 1–100,
or either is not plain digits.
No response holds a key or the NCBI email.

## Configuration

```yaml
  - module: psych_defgen
    enabled: true
    config:
      ncbi_email: "{{ secret.NCBI_EMAIL }}"
      ncbi_api_key: "{{ secret.NCBI_API_KEY }}"            # optional: NCBI allows 10 requests/s with it, 3 without
      openrouter_api_key: "{{ secret.OPENROUTER_API_KEY }}"
      openai_api_key: "{{ secret.OPENAI_API_KEY }}"        # embeddings
      llm_model: "openai/gpt-4.1-mini"
      llm_models: ["openai/gpt-4.1-mini"]                  # what the app offers; llm_model must be one of them
      embedding_model: "text-embedding-3-small"
```

| Key | Needed | Used for |
| --- | --- | --- |
| `NCBI_EMAIL` | yes | NCBI asks every E-utilities client for a contact address. |
| `NCBI_API_KEY` | no | Raises NCBI's limit from 3 to 10 requests/s. |
| `OPENROUTER_API_KEY` | yes | Definition selection and synthesis (`llm_model`, or one of `llm_models`). |
| `OPENAI_API_KEY` | yes | Embeddings for ranking (`embedding_model`). |

The keys and the email are `SecretStr`: they are not logged, and no error
message holds them. An unset `{{ secret.NAME }}` renders as an empty string,
so the engine still boots; the module then logs which keys are missing,
`factory.missing_settings(configuration)` names them, and
`factory.definition_service(configuration)` raises `NotConfiguredError`
naming them.

## What differs from upstream

- **Embeddings.** OpenAI's `text-embedding-3-small` instead of the local
  `all-MiniLM-L6-v2`, so similarity scores, and possibly the ranking, differ
  from upstream's.
- **LLM.** OpenRouter only, with the deployment's key; upstream also offered
  Ollama (its default) and custom OpenAI-compatible servers.
- **APA check.** Plain HTTP instead of a headless browser. A term without an
  entry comes back `unavailable`, since the site answers with its generic
  page rather than a "not in the dictionary" message.
- **No files, no prints.** Progress is logged and reported to `on_stage`; the
  result is returned, not written to `./outputs`.
- **Errors.** Search, embedding and LLM failures raise `DefinitionStepError`
  naming the stage; a full text that fails is skipped, as upstream does.
- **Dropped:** the CLI, the Streamlit app, the Ollama modules and the dotenv
  configuration.

## Tests

```bash
make test-psych-defgen              # fakes and a local DuckLake, no network
make test-psych-defgen-network      # one real "loneliness" run (5 articles, top 3)
```

The network test needs `NCBI_EMAIL`, `OPENROUTER_API_KEY` and
`OPENAI_API_KEY` in the environment, and costs a few cents.
