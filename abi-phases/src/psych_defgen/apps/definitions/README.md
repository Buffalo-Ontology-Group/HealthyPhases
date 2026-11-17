# Construct Definitions app

Nexus catalog ID: `psych_defgen:definitions`. Users start a definition run for a
psychological construct, follow it, browse past runs, read a result and download
its JSON. It calls `/psych_defgen/api/*` (see the module README) with the user's
Nexus session.

## Files

```
definitions/
├── manifest.json      # catalog entry: name, description, category, icon (no right-pane agent)
├── index.html         # both views: the form and the history, and one run
├── css/app.css        # light and dark tokens, layout, the phone-width runs list
├── js/
│   ├── main.js        # entry point
│   ├── app.js         # shows the view the route names; pauses polling while the page is hidden
│   ├── router.js      # hash routes; restores the last route after a reload
│   ├── api.js         # the only module that calls fetch: base URL, auth, errors
│   ├── ui.js          # escaping, safe links, dates and durations, stage labels, the poller
│   ├── form.js        # the new-definition form: settings, the missing-keys notice, validation
│   ├── history.js     # the runs table, its pages and its polling
│   ├── run.js         # one run: polling, the download
│   └── result.js      # markup for progress, a failure and a result; the download's file name
├── tests/             # node --test suites (*.cjs) and their small DOM stand-in
├── manifest_test.py   # catalog, serving and packaging checks
└── package.json       # marks js/ as ES modules for Node; not used by the browser
```

There is no build step and nothing loads from a CDN. `index.html` loads `js/main.js` as
a module and every import is relative, so the browser fetches each file from
`/app-html/psych_defgen/definitions/...`. Nexus serves any `.js`, `.css` or `.json`
file under an app folder there, authorised by the `abi_app_html_token` cookie that its
first response sets.

## Routes

| Route | Shows |
| --- | --- |
| `#/` | The new-definition form and the history of runs, newest first, 25 to a page |
| `#/runs/<run_id>` | One run: its progress, its error, or its result with **Download JSON** |

Nexus gives the iframe a new URL, with a new access token, each time it reloads the app,
which drops the hash. The router therefore keeps the last route in `sessionStorage` and
restores it when the app opens without one.

## What it shows

- **The form** takes its limits, defaults and models from `GET /settings`, and checks
  the values before sending them. When `missing` names unset keys, a notice names them
  (for example "NCBI_EMAIL is not set on this deployment") and the form is disabled; the
  history stays readable. Starting a run opens it.
- **The history** reads its page again every 5 seconds while a run on it is pending or
  running.
- **A run** reads itself again every 5 seconds while it is pending or running, showing its
  stage in words ("Searching PubMed", "Reading full texts", …). A failed run shows its
  error. A succeeded run shows:
  - the definition from the literature and how it was found ("Pattern match", "Selected
    by LLM"), or "None found";
  - the AI-synthesized definition, marked as generated, or "Not enough evidence";
  - the APA Dictionary status with its link, never APA text; "unavailable" reads as
    "Could not be confirmed";
  - the evidence passages with title, authors, journal and year, PubMed and PMC links
    built from `pmid` and `pmcid`, the source and the score to 3 decimals;
  - the counts, the token usage, the LLM and embedding models;
  - **Download JSON**: the result, saved as `<term>.json` with upstream's file naming
    (`re.sub(r"[^a-zA-Z0-9_-]+", "_", term.strip().lower())`).

Only the shown view polls, and nothing polls while the page is hidden; coming back reads
the view again at once. Every value from the API, literature text and the user's term
alike, reaches the page through `esc()`, and only `http(s)` links are followed.

## Tests

```bash
make test-psych-defgen   # the module, this app included
make test                # everything
```

The `.cjs` suites import the ES modules into Node with a small DOM stand-in
(`tests/harness.cjs`): elements are created on first lookup by id, so the page is
never parsed and the test inspects the same objects the module writes to. Timers are
fakes the test fires, and `app_test.cjs` runs the whole app against a fake API that
follows the contract.
