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

This module holds the pipeline only. Runs, storage, the Dagster job, the HTTP
API and the Nexus app build on it.

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
make test-psych-defgen              # fakes only, no network
make test-psych-defgen-network      # one real "loneliness" run (5 articles, top 3)
```

The network test needs `NCBI_EMAIL`, `OPENROUTER_API_KEY` and
`OPENAI_API_KEY` in the environment, and costs a few cents.
