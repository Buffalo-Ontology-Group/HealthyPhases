// The help pages: one per section, then a glossary, the Assistant and an FAQ.
//
// A page is { id, title, lede, section?, entries: [{ id, title, body }] }. Its
// route is #/help/<id>, and #/help/<id>/<entry> opens it at an entry. Bodies are
// HTML written here; every label of this app is marked with ui(), which renders
// it in bold and records it, so a test can check the label exists in the app.
// Labels of the Nexus pages around the app (its sign-in screen, its sidebar)
// are marked with nexus() instead: they are not in this app's source.

import { esc, helpHref } from '../ui.js';
import { ASSISTANT_EXAMPLES, ASSISTANT_HINT } from '../sections/overview.js';

export const UI_LABELS = new Set();
export const NEXUS_LABELS = new Set();

export function ui(label) {
  UI_LABELS.add(label);
  return `<strong class="ui-label">${esc(label)}</strong>`;
}

export function nexus(label) {
  NEXUS_LABELS.add(label);
  return `<strong class="ui-label">${esc(label)}</strong>`;
}

// A link to another help page or entry, and one into the app.
const page = (topic, entry, text) => `<a href="${helpHref(topic, entry)}">${text}</a>`;
const go = (route, text) => `<a href="${route}">${text}</a>`;
const list = (items) => `<ul>${items.map((item) => `<li>${item}</li>`).join('')}</ul>`;
const numbered = (items) => `<ol>${items.map((item) => `<li>${item}</li>`).join('')}</ol>`;
const p = (text) => `<p>${text}</p>`;
const term = (id, text = id) => page('glossary', id, text);

export const SIGN_IN_URL = 'https://nexus.healthyphases.org';

const gettingStarted = {
  id: 'getting-started',
  title: 'Getting started',
  lede: 'Sign in, open the app, and follow its four steps from papers to findings.',
  entries: [
    {
      id: 'sign-in',
      title: 'Sign in',
      body: numbered([
        `Go to <a href="${SIGN_IN_URL}" target="_blank" rel="noopener noreferrer">nexus.healthyphases.org</a>.`,
        `In ${nexus('Email')}, enter the email address you were invited with, then choose ${nexus('Send sign-in code')}.`,
        `Nexus emails you a 6-digit code. Enter it in ${nexus('Sign-in code')} and choose ${nexus('Verify code')}. The same email has a magic link, which signs you in too.`,
      ]) + p(`Typed the wrong address? Choose ${nexus('Use a different email')}. No email? See ${page('faq', 'sign-in-code', 'I didn’t receive a sign-in code')}.`),
    },
    {
      id: 'open-app',
      title: 'Open the HealthyPhases app',
      body: p(`In Nexus, open ${nexus('Apps')} in the sidebar and choose ${nexus('HealthyPhases')}. The app opens on ${go('#/overview', 'Overview')}.`)
        + p(`Its own sidebar lists the sections, one per step of the work, with ${ui('Help')} at the bottom. On a narrow screen the sidebar becomes a bar across the top. Every page has its own link: if Nexus reloads the app, it comes back to the page you had open.`),
    },
    {
      id: 'workflow',
      title: 'The four steps',
      body: numbered([
        `<strong>Collect papers.</strong> Find papers on PubMed and download them in ${page('library', '', 'Library')}, or upload your own PDFs, and gather them in a ${page('collections', '', 'collection')}.`,
        `<strong>Prepare prompts.</strong> Write or edit the instructions that pull findings out of each part of a paper, and try them on a few chunks first: ${page('prompts', '', 'Prompts')}.`,
        `<strong>Run.</strong> Queue a run of your prompts and a model on a collection, and follow it until it finishes: ${page('runs', '', 'Runs')}.`,
        `<strong>Explore.</strong> Open a paper to read what was found in it, chunk by chunk, in ${page('results', '', 'Results')}, or look for a finding across every paper in ${page('search', '', 'Search')}.`,
      ]) + p(`New words on the way are in the ${page('glossary', '', 'Glossary')}. ${page('assistant', '', 'The Assistant')} can do many of these steps for you.`),
    },
  ],
};

const overview = {
  id: 'overview',
  title: 'Overview',
  section: 'overview',
  lede: 'The workflow in four steps, with live counts and a way into each.',
  entries: [
    {
      id: 'steps',
      title: 'The four steps',
      body: p(`Each card is one step, with counts read when you open the page: papers in the library, saved PubMed searches and collections; prompts and their versions; runs queued or running, and those that succeeded or failed among the 100 latest; and the prompts, models and folders that Search has findings for.`)
        + p(`Its buttons lead to the sections that do the step: ${ui('Search PubMed')}, ${ui('Browse the library')}, ${ui('Manage collections')}, ${ui('Open the prompt library')}, ${ui('Write a new prompt')}, ${ui('Start a run')} and ${ui('Follow runs')}. The run step also shows the latest run and its status.`)
        + p('A dash instead of a number means that count could not be read; the message at the top says why. The other counts still show.'),
    },
    {
      id: 'quick-search',
      title: 'Quick search',
      body: p(`The box in the last step opens ${page('search', '', 'Search')} with your words, searching findings by meaning.`),
    },
    {
      id: 'ask',
      title: 'Ask the Assistant',
      body: p(`The ${ui('Ask the Assistant')} card says how to open the Assistant and gives example requests. See ${page('assistant', '', 'The Assistant')}.`),
    },
  ],
};

const library = {
  id: 'library',
  title: 'Library',
  section: 'library',
  lede: 'PubMed papers: search, download, keep searches up to date and add citation data. Each tab has its part below.',
  entries: [
    {
      id: 'papers',
      title: 'Papers',
      body: p(`${ui('Papers')} lists every paper of your saved searches and full ingestions, once each. By default it shows ${ui('Downloaded papers')}; ${ui('Papers to show')} also offers ${ui('All discovered records')} and ${ui('Not downloaded')}.`)
        + p(`Narrow the list with ${ui('Search your library')} (title, author, journal, PMID or DOI), ${ui('Saved search')}, the ingestion dates (whole days, in UTC), ${ui('Sort by')} and ${ui('Papers per page')}, then choose ${ui('Apply filters')}. ${ui('Clear filters')} shows everything again. When OpenAlex is enabled there are filters on its status, citations, topic and institution too.`)
        + list([
          `A title opens the paper on PubMed. ${ui('Files')} lists the PDF versions downloaded.`,
          `A downloaded paper has ${ui('Add to collection')}.`,
          `Once a run has read the paper, ${ui('Open results')} opens its page in ${page('results', 'paper', 'Results')}.`,
        ]),
    },
    {
      id: 'search',
      title: 'Search PubMed',
      body: p(`Type a query in ${ui('PubMed query')}, in PubMed’s own syntax (for example <code>solitude AND mental health</code>). Set ${ui('Published from')} and ${ui('Published until')}, ${ui('Sort results')} and ${ui('Maximum results')} if you need to, then choose ${ui('Search')}.`)
        + p(`Searching saves the search and its paper records. Nothing is downloaded yet. The ${ui('Results')} card previews the papers, and then:`)
        + list([
          `${ui('Ingest previewed papers')} downloads the PDFs of the papers shown, as one request in ${page('library', 'downloads', 'Downloads')}.`,
          `${ui('Ingest all matching papers')} downloads every paper the search matches, beyond the preview, in batches. It asks first.`,
          `${ui('Add search to a collection')} adds the whole search to a ${term('collection')}. Papers it downloads later join the collection too.`,
          `${ui('Schedule this search')} opens ${page('library', 'schedules', 'Schedules')} with this search chosen.`,
          `${ui('Browse downloaded papers')} opens ${ui('Papers')} on this search only.`,
        ])
        + p(`${ui('Previous searches')} opens any saved search again. Only papers with a PubMed Central record can be downloaded; the others say ${ui('No PMC record')}. Downloading stores a paper’s PDF. A run then reads it: see ${term('ingested')}.`),
    },
    {
      id: 'downloads',
      title: 'Downloads',
      body: list([
        `${ui('Ingestion requests')} are downloads of previewed papers, with how many PDFs are published out of those requested, and each paper’s outcome. A failed or partial request has ${ui('Retry remaining')}.`,
        `${ui('Full ingestions')} download every match of a search in batches, with their progress. An interrupted one has ${ui('Resume')}, and ${ui('View papers')} opens what it found in ${ui('Papers')}.`,
      ]) + p(`Downloads start when the ${term('orchestrator')} is free, and this tab refreshes every 10 seconds while it is open.`),
    },
    {
      id: 'schedules',
      title: 'Schedules',
      body: p(`A schedule runs a saved search again ${ui('Every hour')}, ${ui('Every day')} or ${ui('Every week')}, and with ${ui('Download newly found papers')} downloads the new papers it finds. Create one under ${ui('Schedule a saved search')}: choose the search, give the schedule a name, then ${ui('Create schedule')}. Its first run is one interval later.`)
        + list([
          `${ui('Disable')} stops future runs; work already started can finish. ${ui('Enable')} starts a fresh interval.`,
          `${ui('Delete')} removes the schedule after asking. Saved searches and downloaded papers stay.`,
          `A search keeps its result limit. When a schedule says ${ui('Result limit reached')}, narrow its query so no paper is missed.`,
        ]) + p('Schedules only search and download. They never start a pipeline run.'),
    },
    {
      id: 'enrichment',
      title: 'Enrichment',
      body: p(`This tab shows only when OpenAlex is enabled. ${ui('Enrich a saved search')} adds citations, topics, affiliations, references and open-access locations from OpenAlex to every record of a search, with or without a PDF. ${ui('Queue enrichment')} asks first; tick ${ui('Refresh recent matches too')} to check recent matches again.`)
        + p(`${ui('Enrichment requests')} shows the progress and refreshes every 10 seconds. A failed request has ${ui('Resume')}, which keeps the papers already done. Papers a search finds later need another request.`)
        + p(`Enriched papers have an OpenAlex button in ${ui('Papers')} that opens the details.`),
    },
  ],
};

const collections = {
  id: 'collections',
  title: 'Collections',
  section: 'collections',
  lede: 'A collection is the set of papers that runs and searches work on.',
  entries: [
    {
      id: 'create',
      title: 'Create a collection',
      body: p(`Choose ${ui('New collection')}, fill in ${ui('Collection name')} and, if you like, a description, add papers, then choose ${ui('Create collection')}. Until then, what you add waits on the page. On a saved collection every addition is saved straight away, and ${ui('Save name and description')} renames it.`),
    },
    {
      id: 'add-papers',
      title: 'Add papers',
      body: p(`${ui('Add papers')} offers four ways, and a collection can mix them:`)
        + list([
          `<strong>Your own PDFs:</strong> choose the files (up to 25 MiB each) and then ${ui('Upload PDFs')}. They join as one member, ${ui('PDFs uploaded into this collection')}.`,
          `<strong>A saved PubMed search:</strong> pick it in ${ui('PubMed search')} and choose ${ui('Add search')}. The search stays live: papers it downloads later join on their own. Only downloaded papers count.`,
          `<strong>A storage folder:</strong> pick it in ${ui('Storage folder')} and choose ${ui('Add folder')}. A folder includes its subfolders.`,
          `<strong>Papers from the library:</strong> type a title, PMID or file name in ${ui('From the library')}, choose ${ui('Find')}, then ${ui('Add')} beside a paper.`,
        ])
        + p(`From ${page('library', 'papers', 'Library')}, ${ui('Add to collection')} on a paper and ${ui('Add search to a collection')} do the same. Adding papers does not start a run: see ${page('collections', 'run', 'Run on this collection')}.`),
    },
    {
      id: 'members',
      title: 'Members',
      body: p(`${ui('Members')} lists what you added, with its type and how many papers it brings. A paper that several members include is counted once.`)
        + p(`${ui('Remove')} takes a member out after asking. Its papers leave the collection unless another member includes them. Nothing is deleted from storage or the library, and queued runs keep their papers.`),
    },
    {
      id: 'papers',
      title: 'Papers and their states',
      body: p(`${ui('Papers')} lists every paper of the collection once, with where it is stored and its state:`)
        + list([
          `${ui('Not ingested yet')}: no run has read it so far.`,
          `${ui('Ingested')}: a run has read its text and cut it into ${term('chunk', 'chunks')}, but no findings are stored for it.`,
          `${ui('Has results')}: findings are stored for it, and their number is shown.`,
        ])
        + p(`An ingested paper’s name opens its page in ${page('results', 'paper', 'Results')}. ${ui('Refresh')} reads the list again. Why a paper stays not ingested: ${page('faq', 'not-ingested', 'see the FAQ')}.`),
    },
    {
      id: 'run',
      title: 'Run on this collection',
      body: p(`${ui('Run on this collection')} opens ${page('runs', 'new-run', 'Runs → New run')} with the collection chosen. The run covers the collection’s papers as they are when you submit it.`),
    },
    {
      id: 'results',
      title: 'Results of this collection',
      body: p(`${ui('Results of this collection')} opens ${page('results', 'filters', 'Results')} with only this collection’s papers listed.`),
    },
    {
      id: 'search',
      title: 'Search this collection',
      body: p(`${ui('Search this collection')} opens ${page('search', '', 'Search')} limited to this collection’s papers.`),
    },
    {
      id: 'archive',
      title: 'Archive collection',
      body: p(`${ui('Archive collection')} asks first, then takes the collection off this list, off new runs and off search filters. Its papers, storage folders and queued runs are kept.`),
    },
  ],
};

const prompts = {
  id: 'prompts',
  title: 'Prompts',
  section: 'prompts',
  lede: 'A prompt tells the model what to extract from each chunk of a paper. Every saved change is a new version.',
  entries: [
    {
      id: 'library',
      title: 'The prompt library',
      body: p(`The ${ui('Prompt library')} lists every prompt version. Versions share their prompt’s name; when there are several, a short code tells them apart. ${ui('Find a prompt')} filters by name, and ${ui('New prompt')} opens an empty editor.`),
    },
    {
      id: 'editor',
      title: 'The editor',
      body: list([
        `${ui('Prompt name')}: letters, digits, spaces, _ or -, starting with a letter or digit.`,
        `${ui('Output format')}: the shape of the answer, such as <code>results</code>, <code>effects</code> or <code>relations</code>. The line below the field shows what that format returns.`,
        `${ui('Prompt text')}: the instructions, with <code>{chunk_text}</code> where the text of each chunk goes. Text without it cannot be saved or tried.`,
      ]),
    },
    {
      id: 'built-in',
      title: 'Built in',
      body: p(`The prompts that come with the app are marked ${ui('Built in')} in ${page('prompts', 'versions', 'Versions')}. Open one and save changed text to make a version of your own; the built-in text stays as it is.`),
    },
    {
      id: 'save',
      title: 'Save as new version',
      body: p(`${ui('Save as new version')} saves the text as a new version of the prompt. The version it was edited from stays, with its results, for the runs that used it. Saving text that is already saved gives back that same version.`)
        + p('The saved version is selected for your next run, in place of the one it was edited from.'),
    },
    {
      id: 'duplicate',
      title: 'Duplicate prompt',
      body: p(`${ui('Duplicate prompt')} starts a new prompt from the open one, named the same with “copy” added. Change the name and the text, then save it.`),
    },
    {
      id: 'use',
      title: 'Use in new run',
      body: p(`${ui('Use in new run')} opens ${page('runs', 'new-run', 'New run')} with this version selected. It needs a saved version: after editing, save first.`),
    },
    {
      id: 'try-it',
      title: 'Try it',
      body: p(`${ui('Try it')} runs the text in the editor, saved or not, on a few chunks of an ingested paper and shows what each chunk gives. Nothing is saved.`)
        + numbered([
          `Choose the ${ui('Chunker')} and the ${ui('Model')}.`,
          `Pick a paper under ${ui('Find an ingested paper')}.`,
          `Pick up to 10 ${ui('Chunks')}, or leave them unpicked to try the first few (${ui('If none is picked, try the first')}).`,
          `Choose ${ui('Try it')}. It can take a minute.`,
        ])
        + p(`Each chunk shows its findings, or the error and the ${ui('Raw output')}. Liked the result? ${ui('Save as new version')} and ${ui('Use in new run')} are right there.`),
    },
    {
      id: 'compare-with',
      title: 'Compare with',
      body: p(`${ui('Compare with')} picks a saved version to show beside each tried chunk: what that version stored for the same chunk, with the same model, in earlier runs. A chunk it never ran on with that model says so. By default it is the version the editor was opened from; ${ui('Nothing')} turns the comparison off.`),
    },
    {
      id: 'versions',
      title: 'Versions',
      body: p(`${ui('Versions')} lists every text saved under the prompt’s name, newest first: when it was ${ui('First seen')} (${ui('Built in')} for those that came with the app), what it was ${ui('Used by')}, and its ${ui('Items')} per model.`)
        + p(`${ui('Open')} edits from a version, ${ui('Use in new run')} selects it for a run, and ${ui('Compare')} shows what changed between it and the editor. Below, pick any two in ${ui('From')} and ${ui('To')}, including ${ui('Text in the editor')}, to see the line diff: added lines are highlighted, removed lines struck through.`),
    },
  ],
};

const runs = {
  id: 'runs',
  title: 'Runs',
  section: 'runs',
  lede: 'A run sends every chunk of the chosen papers through each chosen prompt with a model.',
  entries: [
    {
      id: 'requests',
      title: 'Requests',
      body: p(`${ui('Requests')} lists the 100 latest runs with their papers, status, prompts and model; ${ui('Status')} filters them. The list refreshes every 10 seconds while it is open.`),
    },
    {
      id: 'statuses',
      title: 'Statuses',
      body: list([
        `${ui('pending')}: queued. The ${term('orchestrator')} looks for queued runs every 30 seconds and starts each one it finds.`,
        `${ui('running')}: the run is reading papers and sending chunks to the model.`,
        `${ui('succeeded')}: every step finished. Some chunks may still have failed: the run’s page counts them.`,
        `${ui('failed')}: the run stopped. Its page says why. What it finished before stopping is kept.`,
      ]) + p(`More in ${page('faq', 'run-failed', 'A run failed, or is stuck as pending')}.`),
    },
    {
      id: 'new-run',
      title: 'New run',
      body: numbered([
        `${ui('1. Choose papers')}: in ${ui('Paper source')}, choose a ${ui('Collection')}, ${ui('Storage folders')} or a downloaded ${ui('PubMed search')}. ${ui('Documents in this selection')} previews the papers. Folders can be kept as a collection with ${ui('Save as collection')}.`,
        `${ui('2. Configure extraction')}: choose the ${ui('Prompts')} (all are selected at first), the ${ui('Model')} and the ${ui('Chunker')}.`,
        `Choose ${ui('Request run')}, then ${ui('Follow this run')}.`,
      ]) + p('A run on a collection or a PubMed search keeps exactly the papers it had when you submitted it. Work already done, the same chunk with the same prompt version and model, is skipped, so running again costs only what is new or failed.'),
    },
    {
      id: 'saved-configurations',
      title: 'Saved configurations',
      body: p(`In ${ui('New run')}, name your choices and choose ${ui('Save configuration')} to keep them (a PubMed search source cannot be saved). Under ${ui('Saved configurations')}, ${ui('Load into the form')} brings them back to change them, and ${ui('Run saved configuration')} queues them as they are, after asking. On a collection it takes the collection’s papers as they are then.`),
    },
    {
      id: 'run-page',
      title: 'A run’s page',
      body: p('Opening a run shows its papers, status and times, its prompts, model and chunker, and what it did: chunks that succeeded, failed, or were skipped because they were already done. A failed run shows its error. The page refreshes every 10 seconds.')
        + list([
          `${ui('Papers in this run')} opens ${page('results', 'filters', 'Results')} on the papers the run covered.`,
          `${ui('Open the collection')} opens the collection it ran on.`,
          `${ui('Search with this run\'s prompts and model')} opens Search on what it found, once it has succeeded.`,
        ]),
    },
  ],
};

const results = {
  id: 'results',
  title: 'Results',
  section: 'results',
  lede: 'Every ingested paper, and for each one what every prompt version and model found in it.',
  entries: [
    {
      id: 'filters',
      title: 'Find papers',
      body: p(`The list shows each ingested paper with its chunks, ${ui('Extractions')} (failed ones in red) and findings. Narrow it with ${ui('Paper')} (title, PMID, file name or folder), ${ui('Collection')}, ${ui('Results')} (${ui('With results')} or ${ui('Without results')}), ${ui('Prompt')}, ${ui('Model')} and ${ui('Sort by')}, then choose ${ui('Show papers')}. With a prompt or model chosen, only the papers it ran on are listed, and the counts cover it alone.`)
        + p(`Coming from a run’s ${ui('Papers in this run')}, the list shows that run’s papers; ${ui('Show every paper')} lifts it. ${ui('Clear filters')} starts again.`),
    },
    {
      id: 'paper',
      title: 'A paper’s page',
      body: p(`A paper’s page starts with its record: authors and journal, PMID and DOI links, its source file, when it was ingested, its chunks, and OpenAlex data when there is some. ${ui('Back to the papers')} returns to the list as you left it.`),
    },
    {
      id: 'what-ran',
      title: 'What ran on this paper',
      body: p(`${ui('What ran on this paper')} lists each prompt version and model that ran on it, with the ${ui('Chunks read')} (and failed), the ${ui('Items found')} and the runs. ${ui('Runs that included it')} links to each run.`),
    },
    {
      id: 'by-chunk',
      title: 'Results chunk by chunk',
      body: p(`Under ${ui('Results')}, choose one ${ui('Prompt version and model')}. Its findings are listed chunk by chunk, in reading order, and ${ui('Show the chunk')} shows the text each came from. A chunk that failed shows the error and ${ui('What the model returned')}. Long lists show the first 200 findings; ${ui('Show all')} shows the rest.`),
    },
    {
      id: 'compare',
      title: 'Compare two versions or models',
      body: p(`${ui('Compare two versions or models')} puts two of them side by side, chunk by chunk: pick ${ui('First')} and ${ui('Second')}. A finding both made is marked ${ui('Also found by the other')}, and the line on top counts what each found alone.`),
    },
    {
      id: 'downloads',
      title: 'Downloads',
      body: list([
        `${ui('Download PDF')}: the paper itself.`,
        `${ui('Download all results (CSV)')}: every finding stored for this paper.`,
        `${ui('Download these results (CSV)')}: only those of the chosen prompt version and model.`,
      ]),
    },
  ],
};

const search = {
  id: 'search',
  title: 'Search',
  section: 'search',
  lede: 'Find which papers state a finding, and where in them.',
  entries: [
    {
      id: 'modes',
      title: 'By meaning, by keyword or by effect',
      body: list([
        `${ui('Semantic')} finds findings that mean something close to your words, best first, each with a score. ${ui('Minimum score')} under ${ui('Filters')} drops weak matches.`,
        `${ui('Keyword')} finds the words themselves. Put quotes around an exact word or phrase.`,
        `${ui('Effects')} searches the relations that relation prompts extract: who or what (${ui('From / subject')}) increases, decreases or has no effect on what (${ui('Target process (optional)')}), for which ${ui('Participant')}, in which ${ui('Direction')}. Leave them blank to browse every effect.`,
      ]) + p('Search covers the findings of runs that have finished.'),
    },
    {
      id: 'filters',
      title: 'Filters',
      body: p(`Open ${ui('Filters')} to limit a search by prompt (by name, so every version of it), by model (${ui('All models')} by default), by ${ui('Collection')} or by ${ui('Source folders')} (subfolders included; ${ui('All folders')} clears them). ${ui('Batch size')} sets how many matches come at a time.`),
    },
    {
      id: 'matches',
      title: 'Each match',
      body: p(`A match shows the finding, its prompt, model and paper, and the chunk it came from. ${ui('Show source chunk')} shows that chunk with your words highlighted, and ${ui('View extraction prompt')} the prompt text that produced it, with ${ui('Open this prompt version')}.`)
        + p(`${ui('Open paper')} opens the paper’s page in ${page('results', 'paper', 'Results')}; ${ui('Download paper')} downloads its PDF.`),
    },
    {
      id: 'export',
      title: 'Export all matches (CSV)',
      body: p(`${ui('Export all matches (CSV)')} downloads every match of the current search, not only those on screen.`),
    },
    {
      id: 'load-more',
      title: 'Load more',
      body: p(`Matches come in batches. More load as you scroll to the end, or with ${ui('Load more')}. A search reads its matches as they were when it started, so new results do not shuffle the list; search again to include them.`),
    },
  ],
};

const glossary = {
  id: 'glossary',
  title: 'Glossary',
  lede: 'The words this app uses, in plain terms, and where each one shows.',
  entries: [
    { id: 'paper', title: 'Paper', body: p(`One PDF. The same PDF stored in two places is one paper. Papers are listed in ${go('#/results', 'Results')} and in a collection’s ${page('collections', 'papers', 'Papers')}.`) },
    { id: 'ingested', title: 'Ingested', body: p(`A run has read the paper’s PDF into text and cut it into chunks. Downloading a PubMed paper, uploading a PDF or adding a paper to a collection does not ingest it; the first run that includes it does. Shown in a collection’s ${page('collections', 'papers', 'Papers')}.`) },
    { id: 'chunk', title: 'Chunk', body: p(`A short passage of a paper’s text: what a prompt reads at a time. Every finding points back to its chunk, as on a ${page('results', 'by-chunk', 'paper’s page')}.`) },
    { id: 'chunker', title: 'Chunker', body: p(`The way a paper is cut into chunks, with its settings such as the size of each chunk. A paper can be cut by several chunkers; a run and a try each use one. Chosen in ${page('runs', 'new-run', 'New run')} and in ${page('prompts', 'try-it', 'Try it')}.`) },
    { id: 'prompt', title: 'Prompt', body: p(`The instructions that say what to extract from a chunk, with <code>{chunk_text}</code> where the chunk goes and an output format for the answer. Kept in ${page('prompts', 'editor', 'Prompts')}.`) },
    { id: 'prompt-version', title: 'Prompt version', body: p(`One saved text of a prompt. Saving changed text adds a version; findings stay with the version that produced them. Listed in ${page('prompts', 'versions', 'Versions')}.`) },
    { id: 'model', title: 'Model', body: p(`The service that reads each chunk with the prompt and answers with findings. You choose it for each run and each try. Findings are kept per model, so two models can be compared on the same paper in ${page('results', 'compare', 'Results')}.`) },
    { id: 'run', title: 'Run, run request', body: p(`A request to process papers with chosen prompt versions, a model and a chunker. It waits as pending, runs, and ends succeeded or failed. Followed in ${page('runs', 'requests', 'Runs')}.`) },
    { id: 'findings', title: 'Findings (items)', body: p(`What a prompt returned for a chunk: short claims, or relations such as “solitude decreases stress”. Each is kept with its chunk, paper, prompt version and model. Read them in ${go('#/results', 'Results')} and search them in ${go('#/search', 'Search')}.`) },
    { id: 'collection', title: 'Collection', body: p(`A named set of papers that runs and searches work on, built from uploads, saved PubMed searches, storage folders and library papers. Made in ${page('collections', 'create', 'Collections')}.`) },
    { id: 'collection-member', title: 'Collection member', body: p(`One thing added to a collection: uploaded PDFs, a saved search, a folder or a single paper. Each brings its papers. Listed in ${page('collections', 'members', 'Members')}.`) },
    { id: 'saved-search', title: 'Saved search', body: p(`A PubMed search with its query, dates and result limit, saved when you run it. It can be opened again, scheduled, enriched and added to a collection. Made in ${page('library', 'search', 'Search PubMed')}.`) },
    { id: 'orchestrator', title: 'Orchestrator', body: p(`The background service that carries out runs, downloads and enrichment. It picks up what you queue on its own; see ${page('runs', 'statuses', 'Statuses')}.`) },
  ],
};

const assistant = {
  id: 'assistant',
  title: 'The Assistant',
  lede: 'The Assistant knows this app. It looks things up and does things for you, and asks before anything that costs time or money.',
  entries: [
    {
      id: 'open',
      title: 'Open it',
      body: p(esc(ASSISTANT_HINT))
        + p('It opens in the Nexus pane on the right, beside the app.'),
    },
    {
      id: 'screen',
      title: 'It doesn’t see your screen',
      body: p('It is not told which page or item you have open. Name what you mean: “my Sleep collection”, “the latest Claims prompt”, “my last run”. When something is unclear, it asks.'),
    },
    {
      id: 'on-its-own',
      title: 'What it does on its own',
      body: p('It looks up your collections, saved searches, prompts and versions, runs, papers and their results, and searches the findings. When you ask, it also, without asking again:')
        + list([
          'searches PubMed, which saves the search and downloads nothing;',
          'creates a collection, renames it or changes its description, and adds papers to it;',
          'saves a prompt version, and tries a prompt on up to 10 chunks of a paper;',
          'saves a run configuration.',
        ]),
    },
    {
      id: 'needs-yes',
      title: 'What needs your yes first',
      body: list([
        `queuing a ${page('runs', 'new-run', 'run')};`,
        `downloading PubMed papers, previewed or all matching (${page('library', 'search', 'Search PubMed')});`,
        `creating a ${page('library', 'schedules', 'schedule')};`,
        `queuing ${page('library', 'enrichment', 'OpenAlex enrichment')}.`,
      ]) + p('For each, it first tells you what would happen and waits. It goes ahead only when your next message is a clear yes. If you change anything, it prepares the new version and asks again. An offer lapses after 10 minutes.'),
    },
    {
      id: 'cannot',
      title: 'What it can’t do',
      body: p('It cannot delete, archive or remove anything, turn a schedule off, or upload files. Do these yourself:')
        + list([
          `archive a collection: ${page('collections', 'archive', 'Archive collection')};`,
          `remove a member: ${ui('Remove')} in ${page('collections', 'members', 'Members')};`,
          `turn a schedule off or delete it: ${ui('Disable')} or ${ui('Delete')} in ${page('library', 'schedules', 'Schedules')};`,
          `upload your PDFs: ${ui('Upload PDFs')} in ${page('collections', 'add-papers', 'Add papers')}.`,
        ]),
    },
    {
      id: 'examples',
      title: 'Example requests',
      body: `<ul class="examples">${ASSISTANT_EXAMPLES.map((example) => `<li>“${esc(example)}”</li>`).join('')}</ul>`,
    },
    {
      id: 'changes',
      title: 'Seeing its changes',
      body: p('What it changes appears when you click back into the app: the open page reads its data again whenever the app regains focus.'),
    },
  ],
};

const faq = {
  id: 'faq',
  title: 'FAQ',
  lede: 'Answers to the questions people ask most, and what to do when something looks wrong.',
  entries: [
    {
      id: 'sign-in-code',
      title: 'I didn’t receive a sign-in code',
      body: list([
        'Check that you typed the address you were invited with. A code is sent only to an invited address.',
        'Look in your spam or junk folder.',
        `Wait a few minutes, then request a new code: choose ${nexus('Use a different email')}, enter the address again and choose ${nexus('Send sign-in code')}.`,
        'If still nothing comes, contact the person who invited you to HealthyPhases.',
      ]),
    },
    {
      id: 'not-ingested',
      title: 'A paper shows “not ingested”',
      body: p(`${ui('Not ingested yet')} means no run has read the paper yet. Uploading it, downloading it from PubMed or adding it to a collection does not; the first run that includes it does.`)
        + list([
          `Run on a collection that holds it: ${page('collections', 'run', 'Run on this collection')}. Reading papers is the run’s first step, so the state changes as the run goes.`,
          `If a run that included it failed, its page says why: ${page('faq', 'run-failed', 'A run failed')}.`,
          `If its page in Results says ${ui('Text could not be read')}, the PDF holds no text that can be read, such as a scan of printed pages.`,
        ]),
    },
    {
      id: 'run-failed',
      title: 'A run failed, or is stuck as pending',
      body: p(`<strong>Pending.</strong> The ${term('orchestrator')} looks for queued runs every 30 seconds and starts each one it finds, so a run should leave ${ui('pending')} within a minute. If it stays pending for several minutes, the orchestrator is not running: contact the person who invited you. Don’t request the same run again; the queued one starts once the orchestrator is back.`)
        + p(`<strong>Running for a long time.</strong> That is normal for a large collection: each chunk goes through each prompt. Its page refreshes every 10 seconds.`)
        + p(`<strong>Failed.</strong> Open the run: its error says why, for example a paper that could not be read or found. Everything finished before it stopped is kept: papers read, chunks made, and the findings already stored. A failed run is not retried on its own. Fix what the error names, then request the same run again: it skips every chunk that already has findings for that prompt version and model, and does the rest.`)
        + p(`<strong>Succeeded, with failed chunks.</strong> The run’s page counts them, and the paper’s page shows each error. Running again tries only those chunks.`),
    },
    {
      id: 'no-results',
      title: 'Why does a paper have no results?',
      body: list([
        `No run has included it yet: ${ui('What ran on this paper')} is empty. Run on a collection that holds it.`,
        'Its run is still pending or running. Results shows findings once the run has stored them, and Search once the run has finished.',
        `Its chunks failed: the paper’s page shows the error for each one. See ${page('faq', 'run-failed', 'A run failed')}.`,
        `The prompt found nothing: the page says the version and model ran on the paper and found nothing. That is an answer, not a fault: the paper may not discuss what the prompt looks for. Try it on a chunk you expect to give findings, in ${page('prompts', 'try-it', 'Try it')}.`,
        `A filter is on: in Results, a prompt or model filter counts only its own findings. ${ui('Clear filters')} shows everything.`,
      ]),
    },
    {
      id: 'older-version',
      title: 'Where did the results from an older prompt version go?',
      body: p('Nowhere: each version keeps its own findings. Saving a new version adds one; it replaces nothing, and a run with the new version leaves the old findings as they are.')
        + list([
          `On a paper’s page, choose the older version in ${ui('Prompt version and model')}, or put the two side by side in ${page('results', 'compare', 'Compare two versions or models')}.`,
          `${page('prompts', 'versions', 'Versions')} shows how many findings each version made and which runs used it.`,
          `In Search, a prompt filter covers every version of that prompt; ${ui('View extraction prompt')} shows which one made each match.`,
        ]),
    },
    {
      id: 'upload-pdfs',
      title: 'How do I upload my own PDFs?',
      body: numbered([
        `Open a collection in ${go('#/collections', 'Collections')}, or start one with ${ui('New collection')}.`,
        `Under ${ui('Add papers')}, choose your PDF files (up to 25 MiB each), then ${ui('Upload PDFs')}. Each file says whether it was uploaded. Sending the same file again is safe.`,
        `Uploading does not start a run. Choose ${ui('Run on this collection')} to read them and extract their findings.`,
      ]) + p(`To put PDFs in a storage folder instead, use ${ui('Upload PDFs to a folder')} in the storage folders panel at the foot of Collections. The Assistant cannot upload files.`),
    },
  ],
};

// In the order the help lists them. Getting started is the index's own content.
export const TOPICS = [gettingStarted, overview, library, collections, prompts, runs, results, search, glossary, assistant, faq];

export const INDEX_TOPIC = gettingStarted.id;

export function findTopic(id) {
  return TOPICS.find((topic) => topic.id === id);
}

// The help page of each sidebar section.
export const SECTION_TOPICS = Object.fromEntries(TOPICS.filter((t) => t.section).map((t) => [t.section, t.id]));
