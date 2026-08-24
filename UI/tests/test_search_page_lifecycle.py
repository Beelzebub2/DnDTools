"""Regression tests for the AJAX-routed item search page."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


NODE = shutil.which("node")
SEARCH_SCRIPT = Path(__file__).resolve().parents[1] / "static" / "js" / "search.js"
UI_DIR = Path(__file__).resolve().parents[1]


def test_flask_search_route_forwards_structured_rarity_and_legacy_query() -> None:
    # Run in a clean interpreter so the sort-suite's lightweight platform
    # stubs do not replace modules imported by the real Flask application.
    harness = r"""
import json
import sys

sys.path.insert(0, sys.argv[1])
import app

class SearchManagerStub:
    def __init__(self):
        self.calls = []

    def search_items(self, query, rarity=None):
        self.calls.append((query, rarity))
        return [{"item": {"name": "Training Sword", "rarity": rarity}}]

manager = SearchManagerStub()
api = object.__new__(app.Api)
api.stash_manager = manager
app.api = api
client = app.server.test_client()

structured = client.get(
    "/api/search_items",
    query_string={"query": "training sword", "rarity": "Common"},
)
legacy = client.get(
    "/api/search_items",
    query_string={"query": "weapon damage"},
)

assert structured.status_code == 200
assert structured.get_json()[0]["item"]["rarity"] == "Common"
assert legacy.status_code == 200
assert manager.calls == [("training sword", "Common"), ("weapon damage", "")]
print(json.dumps(manager.calls))
"""

    result = subprocess.run(
        [sys.executable, "-c", harness, str(UI_DIR)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(NODE is None, reason="Node.js is required for browser-script regression tests")
def test_search_rebinds_after_router_cleanup_and_script_reload() -> None:
    """The same script can be evaluated twice and binds only the active page."""

    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');

class FakeElement {
    constructor(id) {
        this.id = id;
        this.value = '';
        this.innerHTML = '';
        this.textContent = '';
        this.style = {};
        this.dataset = {};
        this.listeners = new Map();
        this.classList = { toggle() {}, add() {}, remove() {} };
    }
    addEventListener(type, listener) {
        const listeners = this.listeners.get(type) || [];
        listeners.push(listener);
        this.listeners.set(type, listeners);
    }
    removeEventListener(type, listener) {
        const listeners = this.listeners.get(type) || [];
        this.listeners.set(type, listeners.filter(candidate => candidate !== listener));
    }
    dispatch(type) {
        for (const listener of [...(this.listeners.get(type) || [])]) {
            listener({ target: this, stopPropagation() {} });
        }
    }
    focus() {}
    querySelectorAll() { return []; }
}

function buildPage() {
    const ids = ['searchInput', 'searchResults', 'clearSearch', 'searchMeta', 'resultsCount', 'filterRarity'];
    return Object.fromEntries(ids.map(id => [id, new FakeElement(id)]));
}

let page = buildPage();
const queries = [];
const requests = [];
const windowObject = {
    __pageCleanup: [],
    pywebview: {
        api: {
            async get_characters() { return []; },
            async search_items(query, rarity) {
                queries.push(`${query}|${rarity}`);
                if (query === 'slow') {
                    await new Promise(resolve => setTimeout(resolve, 400));
                }
                return [];
            }
        }
    },
    setTimeout,
    clearTimeout,
    addEventListener() {},
    removeEventListener() {},
    innerWidth: 1200,
    innerHeight: 800,
    location: { href: '', pathname: '/search' }
};
const documentObject = {
    readyState: 'complete',
    body: { appendChild() {} },
    getElementById(id) { return page[id] || null; },
    addEventListener() {},
    createElement(id) { return new FakeElement(id); }
};

const context = vm.createContext({
    window: windowObject,
    document: documentObject,
    console,
    setTimeout,
    clearTimeout,
    AbortController,
    URLSearchParams,
    fetch: async url => {
        requests.push(url);
        return { ok: true, status: 200, statusText: 'OK', json: async () => [] };
    }
});
const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

(async () => {
    new vm.Script(source, { filename: 'search.js' }).runInContext(context);
    const firstInput = page.searchInput;
    firstInput.value = 'sword';
    firstInput.dispatch('input');
    await wait(250);
    if (queries.join(',') !== 'sword|') {
        throw new Error(`first route did not search: ${queries.join('|')}`);
    }

    const cleanups = windowObject.__pageCleanup.splice(0);
    cleanups.forEach(cleanup => cleanup());

    // A detached input must no longer own a debounced search listener.
    firstInput.value = 'stale';
    firstInput.dispatch('input');
    await wait(250);
    if (queries.join(',') !== 'sword|') {
        throw new Error(`detached route still searched: ${queries.join('|')}`);
    }

    // The router swaps the DOM and evaluates search.js again in the same
    // JavaScript global environment. This used to throw a lexical redeclaration
    // SyntaxError before the second input listener could be attached.
    page = buildPage();
    new vm.Script(source, { filename: 'search.js' }).runInContext(context);
    page.searchInput.value = 'boots';
    page.searchInput.dispatch('input');
    await wait(250);
    if (queries.join(',') !== 'sword|,boots|') {
        throw new Error(`re-entered route did not search: ${queries.join('|')}`);
    }

    // Rarity is a second, structured argument. It must never be appended to
    // the free-text query where "Common" would also match "Uncommon".
    page.searchInput.value = '';
    page.filterRarity.value = 'Common';
    page.filterRarity.dispatch('change');
    await wait(10);
    if (queries.join(',') !== 'sword|,boots|,|Common') {
        throw new Error(`rarity was not sent separately: ${queries.join(',')}`);
    }
    page.filterRarity.value = '';

    // A slow pywebview bridge call cannot be cancelled, but its eventual
    // completion must not replace a newer query's results.
    page.searchInput.value = 'slow';
    page.searchInput.dispatch('input');
    await wait(250);
    page.searchInput.value = 'fast';
    page.searchInput.dispatch('input');
    await wait(500);
    if (queries.join(',') !== 'sword|,boots|,|Common,slow|,fast|') {
        throw new Error(`overlapping searches were not issued as expected: ${queries.join('|')}`);
    }
    if (!page.searchMeta.textContent.includes('fast') || page.searchMeta.textContent.includes('slow')) {
        throw new Error(`stale query replaced current results: ${page.searchMeta.textContent}`);
    }

    // The browser/Flask path carries the same explicit query parameters.
    delete windowObject.pywebview;
    page.searchInput.value = 'training sword';
    page.filterRarity.value = 'Common';
    page.searchInput.dispatch('input');
    await wait(250);
    if (requests.length !== 1) {
        throw new Error(`expected one Flask request, got ${requests.length}`);
    }
    const requestUrl = new URL(requests[0], 'http://localhost');
    if (requestUrl.searchParams.get('query') !== 'training sword'
            || requestUrl.searchParams.get('rarity') !== 'Common') {
        throw new Error(`structured Flask query was malformed: ${requests[0]}`);
    }
})().catch(error => {
    console.error(error && error.stack ? error.stack : error);
    process.exitCode = 1;
});
"""

    result = subprocess.run(
        [NODE, "-e", harness, str(SEARCH_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
