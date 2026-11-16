# Upstream provenance

Copied from `src/psych_defgen/` of
[uflcod/psych-defgen](https://github.com/uflcod/psych-defgen) v0.0.1b1, commit
`a62598441bac2ae5266fe44b5e3f3da9d6e76bce` (2026-11-03, "Merge pull request
#21 from uflcod/refine-definition-retrieval"), on 2026-11-16. MIT licence,
University of Florida College of Dentistry: see `LICENSE`.

Why a copy rather than the PyPI package: the package depends on torch,
sentence-transformers, scikit-learn, playwright and streamlit, defaults to a
local Ollama model, and writes its results into `./outputs`, which on
production is the bind-mounted git checkout. This copy calls hosted services
only.

Upstream's logic and prompts are kept as written. Changes are limited to
imports and to the calls that moved behind the ports in
`application/ports.py` (`Literature`, `Embedder`, `LanguageModel`,
`Dictionary`). Vendored code keeps upstream's formatting; files written here
are formatted with ruff.

## Scores differ from upstream's

Upstream ranks evidence with `all-MiniLM-L6-v2` embeddings (sentence-
transformers, which reads at most 256 word pieces of a passage) and
scikit-learn's cosine similarity. This copy uses the OpenAI embeddings API
(`embedding_model`, default `text-embedding-3-small`, inputs cut at 16,000
characters) and the same cosine similarity computed with numpy. The five
queries, their mean, the sort and the 2-per-article cap are upstream's, but
the `score` values are on a different scale and the ranking, and so the
evidence passed to selection and synthesis, can differ from what upstream
returns for the same articles.

## Files

| Upstream file | Here | Status | Why |
| --- | --- | --- | --- |
| `__init__.py` | `__init__.py` | changed | Holds the ABI module and its configuration. |
| `definition_patterns.py` | `domain/definition_patterns.py` | kept | |
| `models.py` | `domain/models.py` | kept | |
| `chunk_text.py` | `domain/chunk_text.py` | changed (imports) | |
| `extract_definition_candidates.py` | `domain/extract_definition_candidates.py` | changed (imports) | |
| `select_definition_sentence.py` | `domain/select_definition_sentence.py` | changed | Imports; the LLM selector is passed in as `select_definition_with_llm(term, items)` instead of being imported with provider, model, key and URL arguments. |
| `llm_definition_selector.py` | `domain/llm_definition_selector.py` | changed | Prompt, parsing and the check that the answer occurs in the evidence are upstream's; the model call is a `generate(messages, json_mode=True)` argument bound to the `LanguageModel` port. |
| `llm_definition_generator.py` | `domain/llm_definition_generator.py` | changed | Same: the prompt is upstream's, the call goes through `generate`. |
| `parse_fulltext.py` | `domain/parse_fulltext.py`, `adapters/secondary/ncbi_entrez.py` | changed | `extract_paragraphs` and `parse_pmc_xml` are kept in the domain; the PMC `efetch` moved to the NCBI adapter. |
| `pubmed_search.py` | `adapters/secondary/ncbi_entrez.py` | changed | `search_pubmed` and `fetch_pubmed_authors` as `NcbiEntrez.search` and `.authors`. |
| `get_pmc_articles.py` | `adapters/secondary/ncbi_entrez.py` | changed | `get_pmc_ids` as `NcbiEntrez.pmc_ids`: still one `elink` per PMID and a failed lookup still skips that PMID; the retries are the adapter's (below) instead of three tries on `IncompleteRead`. |
| `retrieve_abstracts.py` | `adapters/secondary/ncbi_entrez.py` | changed | `fetch_pubmed_abstracts` as `NcbiEntrez.abstracts`; the author, abstract and year helpers are kept. |
| `entrez_config.py` | — | dropped | Read `.env` with python-dotenv and set `Bio.Entrez` globals. Email and key now come from the module configuration and travel with each request. |
| `rag_retrieval.py` | `application/rag_retrieval.py` | changed | `retrieve_relevant_texts` takes an `Embedder`; see "Scores differ". The filters, queries, mean, sort, cap and `context_text` are upstream's. |
| `llm_provider.py` | `adapters/secondary/openrouter_chat.py` | changed | Only the `openrouter` branch is kept: openai SDK, `https://openrouter.ai/api/v1`, 300 s timeout, `response_format` in JSON mode, upstream's usage shape. The Ollama and custom-server branches are dropped: nothing runs a local model. |
| `apa_dictionary.py` | `adapters/secondary/apa_dictionary.py` | changed | Plain HTTP (requests + BeautifulSoup) instead of Playwright + Chromium. The slug, statuses, missing-entry and generic-page detection and heading selectors are upstream's (see below). |
| `pipeline.py` | `application/definition_service.py`, `domain/evidence.py` | changed | `run_pipeline` as `DefinitionService.define`, same order, filters and result (see below). The evidence-record builders moved to the domain. `save_pipeline_result` is dropped: the caller stores the result. |
| `main.py` | — | dropped | Command-line interface; the Nexus app replaces it. |
| `streamlit_app.py` | — | dropped | Streamlit interface; the Nexus app replaces it. |
| `ollama_definition_generator.py`, `ollama_definition_selector.py` | — | dropped | Older variants of the `llm_*` modules that nothing upstream imports. |
| `tests/test_definition_selection.py` | `tests/definition_selection_test.py` | changed | Passes the LLM selector instead of patching it. |
| `LICENSE` | `LICENSE` | kept | |

Not copied: `README.md`, `pyproject.toml`, `uv.lock`, `.gitignore` and
`images/`, which describe or build the upstream package.

## Behaviour that differs

- **`define()`** adds `embedding_model`, `counts` and `upstream` to
  `run_pipeline`'s result, reports the stages `apa`, `search`, `full_text`,
  `ranking`, `selection`, `synthesis` to an optional callback, and logs
  counts instead of printing them. `llm_usage.provider` is always
  `openrouter`.
- **Errors.** As upstream, a full text that fails is skipped and an author
  lookup that fails leaves the evidence's authors. A failure of the search,
  the abstracts, the embeddings or the LLM raises `DefinitionStepError`
  naming the stage; its message comes from the adapter and holds no key,
  email or provider response body.
- **NCBI.** Requests are sent with `requests` and parsed with
  `Bio.Entrez.read`, so that each has a 60 s timeout, 429 and 5xx answers are
  retried with back-off (4 attempts), the 3 requests/s limit (10 with an API
  key) is kept across threads, and email and key go in a POST body rather
  than a URL.
- **APA Dictionary.** The entry markup upstream waited for is in the HTML
  the server sends, so entries are found the same way. A term without an
  entry gets the site's generic page over plain HTTP, which upstream's rules
  classify as `unavailable` rather than `not_found`. Script and style text is
  removed before the missing-entry check, as a rendered page would not show
  it. Request errors name the exception type rather than repeat its text.
