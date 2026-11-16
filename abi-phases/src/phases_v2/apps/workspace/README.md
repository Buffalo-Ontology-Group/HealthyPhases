# HealthyPhases workspace app

Nexus catalog ID: `phases_v2:workspace`. The one app of this project: collect papers,
prepare prompts, run the pipeline and explore what it found. It calls the existing
`/phases_v2/api/*`, `/phases_v2/api/search/*`, `/pubmed/api/*` and `/openalex/api/*`
endpoints with the user's Nexus session.

## Files

```
workspace/
├── manifest.json         # catalog entry: name, description, category, icon, and the right-pane agent
├── index.html            # the shell: sidebar, one <section> per section, toasts, confirm and collection-picker dialogs
├── css/app.css           # light and dark tokens, layout, components
├── js/
│   ├── main.js           # entry point
│   ├── shell.js          # mounts sections, follows the route, refreshes on focus/visibility
│   ├── router.js         # hash routes and deep links; restores the last route after a reload
│   ├── api.js            # the only module that calls fetch: base URL, auth, errors
│   ├── ui.js             # escaping, messages, pills, empty states, pagination, toasts, confirmation, owners
│   ├── documents.js      # previews of what a run reads (folders, a PubMed search, a collection)
│   ├── diff.js           # line diff of two prompt texts
│   ├── help/topics.js    # the help pages: one per section, Getting started, Glossary, The Assistant, FAQ
│   └── sections/         # overview, library, collections, prompts, runs, results, search, help
│                         # (+ search_render; prompt_try and prompt_versions for Prompts;
│                         # paper_view for Results)
├── tests/                # node --test suites (*.cjs) and their small DOM stand-in
├── manifest_test.py      # catalog, serving and packaging checks
└── package.json          # marks js/ as ES modules for Node; not used by the browser
```

The manifest's `"agent": "phases_v2 WorkspaceAssistantAgent"` makes Nexus load the
Assistant in its right pane while the app is open (see the module README, "The
Assistant"). Nothing passes between the app and the pane, so the app only says how to
open it: the sidebar's foot and the Overview name the pane's button at the top right of
Nexus and ⌘K / Ctrl+K, which Nexus catches only once focus is outside the app's frame,
and link to Help → The Assistant.
Changes the Assistant makes appear when the user clicks back into the app, like any
change made elsewhere.

There is no build step and nothing loads from a CDN. `index.html` loads `js/main.js` as
a module and every import is relative, so the browser fetches each file from
`/app-html/phases_v2/workspace/...`. Nexus serves any `.js`, `.css` or `.json` file
under an app folder there, authorised by the `abi_app_html_token` cookie that its first
response sets.

## Routes

| Route | Shows |
| --- | --- |
| `#/overview` | The workflow in four steps with live counts, and how to ask the Assistant with example requests |
| `#/library`, `#/library?query_id=...&page=2` | The paper library, with its filters and page |
| `#/library/search?query=<query_id>` | PubMed search, with a saved search previewed |
| `#/library/downloads` | Ingestion requests and full ingestions |
| `#/library/schedules` | Scheduled searches |
| `#/library/enrichment` | OpenAlex enrichment (only when the module is enabled) |
| `#/collections`, `#/collections/<collection_id>`, `#/collections/new` | Collections |
| `#/prompts`, `#/prompts/<prompt_id>`, `#/prompts/new` | The prompt library: editor, Try it on a few chunks of a paper, and the version history with a line diff |
| `#/runs`, `#/runs/<request_id>` | Run requests, with one opened |
| `#/runs/new` | The new-run form and saved configurations |
| `#/results?q=...&collection=...&prompt=...&model=...&has_results=true\|false&request=...&sort=...&page=2` | Ingested papers with their counts, filtered |
| `#/results/<paper_id>?group=...&a=...&b=...` | One paper: its records, what ran, results by chunk for one prompt version × model (`group`, as `<prompt_id>\|<model_id>`), and two compared side by side (`a`, `b`) |
| `#/search?q=...&mode=semantic\|keyword\|effects&prompt=...&model=...&path=...&collection=...` | Search results |
| `#/help` | Help: Getting started (signing in, opening the app, the four steps), then every help page |
| `#/help/<topic>` | A help page: `overview`, `library`, `collections`, `prompts`, `runs`, `results`, `search`, `glossary`, `assistant`, `faq` (`getting-started` is the index) |
| `#/help/<topic>/<entry>` | A help page opened at one entry, scrolled to it with focus on its heading, for example `#/help/library/schedules` or `#/help/faq/run-failed` |

A paper's page is reached from Results and from the other sections: a search hit's
**Open paper**, a paper in a collection's Papers table, a run's **Papers in this run**
(`#/results?request=<request_id>`), a collection's **Results of this collection** and
**Open results** on a Library paper whose PDF has been ingested.

Nexus gives the iframe a new URL, with a new access token, each time it reloads the app,
which drops the hash. The router therefore keeps the last route in `sessionStorage` and
restores it when the app opens without one.

## Help

The help pages are in `js/help/topics.js` and `js/sections/help.js` renders them. Each page
lists its entries at the top ("On this page") and, for a section's page, links to it
("Open Library"). The hash is the route, so an entry is a route part rather than an
`#anchor`. An unknown topic shows the index and an unknown entry its page, each with a
note. Entering a page focuses its heading and an entry link its entry's heading; the
section's `keepsFocus()` tells the shell not to move focus back to the `h1`. On the app's
first show (`show(route, { first: true })`, for example after a reload at an entry) the
entry is only scrolled to: focus stays with Nexus so ⌘K / Ctrl+K keeps working. A link to
the entry already open fires no `hashchange`, so the section catches the click and
navigates to it again, which scrolls to and focuses the entry.

Every section's page head ends its lede with a **Learn more** link to its page, and
Library's tabs, the parts of Prompts, Runs, Collections and Results, and the empty states
link to their entry. Build these with `learnMore(topic, entry)` and `helpHref(topic, entry)`
from `ui.js`, with literal names: `tests/help_test.cjs` reads every call and checks the
page and entry exist.

The help and the Assistant's instructions describe the same app, and tests catch them
drifting apart:

- Labels of the app are written `ui('Run on this collection')`, which bolds and records
  them; `help_test.cjs` checks each one appears as a whole UI string (an element's text
  or a string literal, so `Add` does not match `Add papers`) in `index.html`, `js/ui.js`
  or `js/sections/*.js`. Labels of the Nexus pages around the app (its sign-in screen and
  sidebar) use `nexus()` instead and are pinned to a short list checked against Nexus.
- Every sidebar section has a help page and a Learn more link to it, and every `#/help/...`
  link in the app or the help opens a page or an entry.
- `manifest_test.py` checks the sidebar lists the router's sections, Help included;
  `agents/WorkspaceAssistantAgent_test.py` checks each is named in the Assistant's
  instructions, and that each place they name with arrows ends on a whole label that
  exists in the app outside the help, or, for `Help → X`, on a help page title (not an
  entry's).

When a label changes, change it in the help too; when a section gains a part, give it an
entry and a Learn more link.

## Sections

Each module in `js/sections/` exports `create(root, ctx)` and returns
`{ show(route, { entering }), hide(), refresh() }`. The shell creates a section on its
first visit and keeps it, so a draft survives a visit elsewhere. When the user comes back
to a section from another one, `show` receives `entering: true` and the section re-reads
its lists first, so a collection, folder, prompt or saved search created elsewhere is
there; an open draft is kept. Within a section, following a link to what is already open
(New collection, New prompt, a list item) re-renders it. The shell also calls `refresh()`
on the open section when the page becomes visible again or the window regains focus, so
changes made outside the app appear. Sections that follow work in progress also poll every 10 seconds while
they are open and visible: run requests, downloads and enrichment progress.

`ctx` gives a section `href`, `navigate`, `replace` (record state in the URL without
re-rendering), `section(name)` (reach another section, for example to hand a collection
to the new-run form with `useCollection(id)`; hand-offs are applied the next time Runs
loads its choices, which entering it does), `toast`, `confirm` and `every`.
`ctx.section('collections').addToCollection(commands, what)` opens the collection picker
from any section: it adds members to a chosen collection or a new one, and resolves with
that collection, or `null` when cancelled.

Every destructive or costly action asks first: archiving a collection, removing a
collection member, deleting a
schedule, starting a full ingestion, queueing OpenAlex enrichment and running a saved
configuration. While the confirmed action is being sent, Cancel and Escape are held
back, so the dialog only ever reports what actually happened.

## Owners

Prompts, collections and saved configurations belong to whoever created them, and runs
record who requested them (module README, "Owners"). The API gives each row `owner_name`,
`owned_by_you` and `shared` (runs: `requested_by_name`, `requested_by_you`), and prompts
and collections `can_change` with a `read_only_reason`. The app decides nothing on its
own: it shows what these say, and the server refuses anything else with a 403 whose
message the app shows.

- **Mine / Everyone's.** `ownerFilter(id, about)` in `ui.js` renders the **Show** choice,
  Mine first. Prompts, Collections and Saved configurations filter the lists they already
  load (`shownFor`); Requests asks the server (`/requests?limit=100&owner=mine`), so Mine is
  the user's 100 latest runs. The pickers that choose what to run on (prompts, collections)
  always offer everyone's.
- **Who owns it.** `ownerLabel(item)` gives "You", the owner's name, or "Shared";
  `requesterLabel(request)` gives "You", the name, or a dash.
- **Read-only.** A prompt name that is shared or someone else's disables both
  **Save as new version** buttons, says why under the editor and makes
  **Duplicate prompt** the main action, which proposes a free "<name> copy" name. Someone
  else's collection disables every control that changes it (`CHANGE_CONTROLS`), hides
  **Archive collection** and the members' **Remove**, and says why; running, results and
  search stay. **Add to collection** offers only collections the user can change.
- **Empty Mine.** Each list says how to make one's own, or to show everyone's.

## Tests

```bash
make test-app      # this folder only
make test          # the whole module, this app included
```

The `.cjs` suites import the ES modules into Node with a small DOM stand-in
(`tests/harness.cjs`): elements are created on first lookup by id, so a section's
template is never parsed and the test inspects the same objects the module writes to.
