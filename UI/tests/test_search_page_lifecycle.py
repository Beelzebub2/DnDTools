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


def test_app_runtime_names_support_settings_reload_and_windows_foreground() -> None:
    harness = r"""
import sys
import types

sys.path.insert(0, sys.argv[1])
import app

assert callable(app.macros.load_tab_mapping)

calls = []

class User32:
    def ShowWindow(self, hwnd, mode):
        calls.append(("show", hwnd, mode))
        return 1

    def SetForegroundWindow(self, hwnd):
        calls.append(("foreground", hwnd))
        return 1

class Window:
    native = types.SimpleNamespace(Handle=123)
    title = "Dark and Darker Stash Organizer"

    def restore(self):
        calls.append(("restore",))

    def show(self):
        calls.append(("window-show",))

api = object.__new__(app.Api)
api.window = Window()
app.sys.platform = "win32"
app.ctypes = types.SimpleNamespace(
    windll=types.SimpleNamespace(user32=User32())
)

assert api.bring_window_to_front() is True
assert ("show", 123, 9) in calls
assert ("foreground", 123) in calls
"""

    result = subprocess.run(
        [sys.executable, "-c", harness, str(UI_DIR)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_object_json_routes_reject_non_object_payloads_with_400() -> None:
    harness = r"""
import sys
import types

sys.path.insert(0, sys.argv[1])
import app

app.api = types.SimpleNamespace(_developer_mode_enabled=True)
client = app.server.test_client()

routes = [
    "/api/market/prices/bulk",
    "/api/quests/progress",
    "/api/quests/active-merchants",
    "/api/capture/settings",
    "/api/character/hero/stash/transfer/check",
    "/api/character/hero/stash/transfer/execute",
    "/api/sort-feedback",
    "/api/settings",
    "/api/sort_order",
    "/api/packet_viewer/hidden",
]

for route in routes:
    response = client.post(route, json=[{"unexpected": "array"}])
    assert response.status_code == 400, (route, response.status_code, response.get_data(as_text=True))

response = client.post(
    "/api/capture/settings",
    data="{",
    content_type="application/json",
)
assert response.status_code == 400, response.get_data(as_text=True)

# Rejected bodies must not partially change the current stash selection.
app.api._current_char_id = "before"
app.api._current_stash_id = 9
response = client.post("/api/character/hero/current-stash/4", json=[{"unexpected": "array"}])
assert response.status_code == 400
assert app.api._current_char_id == "before"
assert app.api._current_stash_id == 9
"""

    result = subprocess.run(
        [sys.executable, "-c", harness, str(UI_DIR)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_sort_operation_ownership_blocks_overlap_and_releases_early_cancel() -> None:
    harness = r"""
import sys
import threading
import types

sys.path.insert(0, sys.argv[1])
import app

api = object.__new__(app.Api)
api.settings_manager = types.SimpleNamespace(get=lambda _key, default=None: default)
api._current_char_id = "hero"
api._current_stash_id = "4"
api._is_combined_view = False
api.window = None
api._sort_state_lock = threading.RLock()

cancelled_but_unwinding = threading.Event()
cancelled_but_unwinding.set()
api.current_sort_event = cancelled_but_unwinding

started = []
class FailThread:
    def __init__(self, *args, **kwargs):
        started.append((args, kwargs))
    def start(self):
        raise AssertionError("a second sort must not start while cancellation is unwinding")

original_thread = app.threading.Thread
app.threading.Thread = FailThread
try:
    api._trigger_sort_current()
finally:
    app.threading.Thread = original_thread

assert started == []
assert api.current_sort_event is cancelled_but_unwinding

# Cancellation can win before the worker starts. Its early return must release
# ownership so a later sort is not blocked forever.
api._sort_worker(cancelled_but_unwinding, "hero", "4")
assert api.current_sort_event is None

old_event = threading.Event()
replacement_event = threading.Event()

class Session:
    def wait_for_countdown(self): return False
    def update_status(self, *args, **kwargs): pass
    def finish(self, *args, **kwargs): pass

class Manager:
    def get_character_details(self, _character_id): return None
    def check_transfer_feasibility(self, *_args, **_kwargs): return {"feasible": True}
    def sort_stash(self, *args, **kwargs):
        # Simulate a newer owner appearing before the old call unwinds. Cleanup
        # must never erase another operation's event.
        api.current_sort_event = replacement_event
        return True, None, None

api.stash_manager = Manager()
api.overlay_manager = types.SimpleNamespace(begin_sort_session=lambda **_kwargs: Session())
api._run_calibration_before_sort = lambda: None
api.get_pack_mode = lambda: False
api.get_stack_mode = lambda: False
api.set_pack_mode = lambda _value: True
api.set_stack_mode = lambda _value: True

result = api.sort_stash("hero", "4", cancel_event=old_event)
assert result["success"] is True
assert api.current_sort_event is replacement_event

blocked = api.sort_stash("hero", "4", cancel_event=threading.Event())
assert blocked["success"] is False
assert "already running" in blocked["error"].lower()

transfer_blocked = api.transfer_items("hero", "2", "4")
assert transfer_blocked["success"] is False
assert "already running" in transfer_blocked["error"].lower()
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
