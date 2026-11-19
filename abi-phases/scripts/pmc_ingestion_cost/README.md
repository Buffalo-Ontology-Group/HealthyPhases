# What PubMed Central would cost through our ingestion

The scripts behind [`PMC_INGESTION_COST.md`](../../PMC_INGESTION_COST.md).
Nothing here is imported by the application, and nothing writes to production.

The cost model (`cost_model.py`) reads only committed files, so the report's
tables can be regenerated offline:

| file | what it holds |
|---|---|
| `prices.yaml` | every price, rate limit and deprecation date, each with its source URL and retrieval date |
| `assumptions.yaml` | the choices that are neither a price nor a measurement (instance types, tier, corpus) |
| `measurements/*.json` | what the collection steps below measured |

```sh
S=scripts/pmc_ingestion_cost
uv run $S/cost_model.py report   # rewrite the tables in PMC_INGESTION_COST.md
uv run $S/cost_model.py check    # exit 1 when they are stale
uv run $S/cost_model.py tables   # print them
make test-pmc-cost               # unit tests, and the report-is-current check
```

Change a price in `prices.yaml` (or an assumption), run `report`, and every
table in the report follows. The prose around the tables does not: re-read it.

## Collecting the measurements again

Run from `abi-phases/`. Each step is separate; each writes one file under
`measurements/`. Paper text and model answers are only ever written under the
gitignored `storage/spikes/pmc_ingestion_cost/`.

| step | command | writes | touches |
|---|---|---|---|
| PMC counts and growth | `uv run $S/collect_pmc.py entrez` | `pmc_entrez.json` | NCBI E-utilities, ≤ 3 requests/s |
| bucket inventory | `uv run $S/collect_pmc.py inventory --cache storage/spikes/pmc_ingestion_cost/inventory` | `pmc_inventory.json` | `pmc-oa-opendata` (downloads ~500 MB) |
| metadata sample | `uv run $S/collect_pmc.py metadata-sample --cache … --size 3000 --seed 855` | `pmc_metadata_sample.json` | `pmc-oa-opendata` |
| paper lengths | `uv run python $S/sample_lengths.py $S/measurements/pmc_metadata_sample.json --texts 1000 --pdfs 200 --chunks-out storage/spikes/pmc_ingestion_cost/pmc_chunks.json` | `pmc_lengths.json` | `pmc-oa-opendata` |
| production datasets | `ssh phases '… .venv/bin/python - aggregates' < $S/collect_prod.py` | `prod_datasets.json` | production, read-only |
| production sample | `ssh phases '… .venv/bin/python - sample 400 855' < $S/collect_prod.py` | `storage/…/prod_sample.jsonl` | production, read-only |
| production stores | `uv run $S/collect_prod_infra.py` | `prod_infra.json` | production, read-only |
| offline token counts | `uv run python $S/count_tokens.py storage/…/prod_sample.jsonl` | `offline_tokens.json` | nothing |
| paid calibration | see below | `calibration*.json` | OpenAI and OpenRouter, ≤ 5 USD |

Each script's docstring has its full command line. `collect_pmc.py` sends
`NCBI_EMAIL`, when set, as the contact address NCBI asks for.

### Production, read-only

`collect_prod.py` runs in the API container on its own interpreter, piped on
stdin (never copied under `/app`, which is a bind mount of the checkout):

```sh
ssh phases 'docker exec -i -e LOG_LEVEL=WARNING -w /app abi-phases-abi-1 .venv/bin/python - aggregates' \
    < $S/collect_prod.py > $S/measurements/prod_datasets.json
```

It attaches the DuckLake catalog `READ_ONLY` and only runs `SELECT`s.
`collect_prod_infra.py` reads Qdrant's collection info, counts triples with
a SPARQL `SELECT`, runs `du` and `docker stats`, and reads Dagster's step
timings inside a `READ ONLY` transaction.

### Paid calibration

`calibrate.py` sends the 8 current prompts over a seeded set of chunks to
each model, through the extraction adapter's own tool binding, answer reader
and retry policy, and records each reply's billed usage. It runs in the API
container because that is where the provider keys are; it reads them from
`/app/.env` into memory and never prints them. It does not start the engine:
an engine start publishes the module's declarations, which is a dataset write.

```sh
PRICES=$(uv run $S/cost_model.py calibration-prices)
SPENT=$(uv run $S/cost_model.py calibration-spent)   # every committed run's spend
ssh phases "docker exec -i -e LOG_LEVEL=WARNING -w /app abi-phases-abi-1 .venv/bin/python - \
    --chunks 10 --seed 855 --cap 0.3 --already-spent $SPENT --prices '$PRICES'" \
    < $S/calibrate.py > $S/measurements/calibration_<name>.json
```

Every calibration run shares one 5 USD budget: the script refuses to start
when `--cap` plus `--already-spent` would pass it. Within a run:

- the client's own retries are off (`max_retries=0`), so every HTTP request
  goes through the ledger; the domain's retry policy does the retrying;
- before each request its worst case is reserved (its input with slack for a
  vendor's preamble, tool schema included, plus `--max-output` tokens, at list
  price), and a request that would take the spend past `--cap` is not sent;
- a request that fails or times out is charged its worst case;
- a record that fails for any other reason is kept as an error, and the run
  still prints its report and spend.

`--chunks-file` takes chunks of random PMC papers instead of production's
(from `sample_lengths.py --chunks-out`, copied to the container's `/tmp`):

```sh
ssh phases 'docker exec -i abi-phases-abi-1 sh -c "cat > /tmp/pmc_chunks.json"' \
    < storage/spikes/pmc_ingestion_cost/pmc_chunks.json
ssh phases "docker exec -i … .venv/bin/python - --chunks 40 --chunks-file /tmp/pmc_chunks.json \
    --models openai/gpt-4.1-mini … --cap … --prices '$PRICES'" < $S/calibrate.py > $S/measurements/calibration_pmc.json
ssh phases 'docker exec abi-phases-abi-1 rm /tmp/pmc_chunks.json'
```

The committed runs: `calibration.json` (production chunks, every model),
`calibration_pmc.json` and `calibration_pmc_responses.json` (PMC chunks), and
the single-chunk smoke test and probes that found the replacement models'
tool-call restriction. The report adds up the spend of all of them: 4.61 USD.

Those runs were made before the cap was hardened as above: the client could
retry on its own, a failed request was charged nothing, and each run's cap was
chosen by hand to keep the total under 5 USD. Their 64 failed requests are all
HTTP 400 rejections of the request's parameters, which the providers refuse
before generating anything, and no request timed out, so the recorded spend
stands.

## Tests

`make test-pmc-cost` runs `tests/` with no network and no production: the
collectors' parsers on canned replies, the cost model on small hand-made
inputs, and a check that the report's tables are what the committed data and
prices produce.
