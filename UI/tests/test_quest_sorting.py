"""Browser-script regression tests for deterministic quest ordering."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


NODE = shutil.which("node")
QUEST_SCRIPT = Path(__file__).resolve().parents[1] / "static" / "js" / "quest.js"


@pytest.mark.skipif(NODE is None, reason="Node.js is required for browser-script regression tests")
def test_prerequisite_sort_is_stable_when_api_order_changes() -> None:
    """Available siblings sort consistently while prerequisites stay first."""

    harness = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const startMarker = '    const rebuildQuestDependencyIndex = () => {';
const endMarker = '    const computeQuestCompletionIndex = () => {';
const start = source.indexOf(startMarker);
const end = source.indexOf(endMarker, start);
if (start < 0 || end < 0) {
    throw new Error('Unable to locate production quest dependency sorter');
}
const productionSorter = source.slice(start, end);

function sortQuests(input) {
    const state = {
        quests: input.map(quest => ({ ...quest })),
        questDisplayOrder: new Map()
    };
    const questAliasIndex = new Map();
    let questAliasEntries = [];
    const questTitleIndex = new Map();
    const questKeyFor = quest => String(quest.id || '');
    const collectAliasesForQuest = quest => [quest.id];
    const resolvePrerequisiteReferences = quest => quest.dependsOn || [];
    let result = [];
    eval(`${productionSorter}\nrebuildQuestDependencyIndex();\nresult = Array.from(state.questDisplayOrder.keys());`);
    return result;
}

const quests = [
    { id: 'quest-a', title: 'Zulu Root', dependsOn: [] },
    { id: 'quest-b', title: 'Alpha Root', dependsOn: [] },
    { id: 'quest-c', title: 'Beta Child', dependsOn: ['quest-a'] },
    { id: 'quest-d', title: 'Able Child', dependsOn: ['quest-b'] },
    { id: 'quest-f', title: 'Charlie Cycle', dependsOn: ['quest-g'] },
    { id: 'quest-g', title: 'Delta Cycle', dependsOn: ['quest-f'] }
];
const shuffled = [quests[5], quests[2], quests[0], quests[4], quests[3], quests[1]];
const first = sortQuests(quests);
const second = sortQuests(shuffled);
const expected = ['quest-b', 'quest-d', 'quest-a', 'quest-c', 'quest-f', 'quest-g'];

if (JSON.stringify(first) !== JSON.stringify(expected)) {
    throw new Error(`unexpected prerequisite order: ${JSON.stringify(first)}`);
}
if (JSON.stringify(second) !== JSON.stringify(expected)) {
    throw new Error(`API insertion order changed output: ${JSON.stringify(second)}`);
}
if (first.indexOf('quest-b') > first.indexOf('quest-d') || first.indexOf('quest-a') > first.indexOf('quest-c')) {
    throw new Error(`a dependent quest preceded its prerequisite: ${JSON.stringify(first)}`);
}
"""

    result = subprocess.run(
        [NODE, "-e", harness, str(QUEST_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(NODE is None, reason="Node.js is required for browser-script regression tests")
def test_packet_concrete_grade_matches_v2_quest_item_family() -> None:
    """Concrete packet IDs reconcile with archetype-only v2 objectives."""

    harness = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const startMarker = "            const normId = (s) =>";
const endMarker = "            // Strategy: match captured missions";
const start = source.indexOf(startMarker);
const end = source.indexOf(endMarker, start);
if (start < 0 || end < 0) {
    throw new Error('Unable to locate production packet/objective matcher');
}
const matcher = source.slice(start, end);
const objectives = [
    { item_id: 'Bandage' },
    { item_id: 'Bandage' },
    { monster: 'Skeleton Champion' }
];
eval(`${matcher}
const first = firstUnclaimedIndex('Bandage_4001');
if (first !== 0) throw new Error('concrete grade did not match family objective');
claimedObjectives.add(first);
const second = firstUnclaimedIndex('Bandage_1001');
if (second !== 1) throw new Error('duplicate family objective was not available');
const monster = firstUnclaimedIndex('SkeletonChampion');
if (monster !== 2) throw new Error('existing fuzzy match regressed');
`);
"""

    result = subprocess.run(
        [NODE, "-e", harness, str(QUEST_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(NODE is None, reason="Node.js is required for browser-script regression tests")
def test_stale_progress_write_fetches_and_merges_server_winner_before_retry() -> None:
    """A rejected browser snapshot must not overwrite newer packet progress."""

    harness = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const sendStart = source.indexOf('    const sendProgressToServer = async');
const sendEnd = source.indexOf('    const scheduleServerPersistProgress =', sendStart);
const syncStart = source.indexOf('    async function syncProgressFromServer(');
const syncEnd = source.indexOf('    async function fetchItems(', syncStart);
if ([sendStart, sendEnd, syncStart, syncEnd].some(index => index < 0)) {
    throw new Error('Unable to locate production progress synchronization functions');
}
const production = source.slice(sendStart, sendEnd) + '\n' + source.slice(syncStart, syncEnd);

const PROGRESS_SYNC_ENDPOINT = '/api/quests/progress';
let lastServerSyncPayload = '';
let progressSyncRevision = 1;
let progressSyncInFlight = false;
let progressSyncRequest = null;
let progressSyncNeedsReconcile = false;
let persistSchedules = 0;
const calls = [];
const state = {
    activeMerchantIds: new Set(['browser-merchant']),
    progress: { objectives: { browser: 1 }, items: {} },
    questsLoaded: false,
    itemsLoaded: false
};
function sanitizeProgressData(value) {
    return {
        objectives: { ...((value && value.objectives) || {}) },
        items: { ...((value && value.items) || {}) }
    };
}
function mergeProgressData(base, incoming) {
    return {
        objectives: { ...(base.objectives || {}), ...(incoming.objectives || {}) },
        items: { ...(base.items || {}), ...(incoming.items || {}) }
    };
}
function progressPayloadForServer() { return sanitizeProgressData(state.progress); }
function schedulePersistProgress() {}
function scheduleServerPersistProgress() { persistSchedules += 1; }
function renderMerchantView() {}
function renderItemsList() {}
const fetch = async (_url, options = {}) => {
    if (options.method === 'POST') {
        calls.push('POST');
        return {
            ok: true,
            async json() { return { saved: false, revision: Number.MAX_SAFE_INTEGER }; }
        };
    }
    calls.push('GET');
    return {
        ok: true,
        async json() {
            return {
                success: true,
                revision: Number.MAX_SAFE_INTEGER,
                progress: { objectives: { packet: 1 }, items: {} },
                active_merchants: ['packet-merchant']
            };
        }
    };
};

let run;
eval(`${production}\nrun = (async () => {
    await sendProgressToServer();
    if (JSON.stringify(calls) !== JSON.stringify(['POST', 'GET'])) {
        throw new Error('stale write was not reconciled in order: ' + JSON.stringify(calls));
    }
    if (state.progress.objectives.browser !== 1 || state.progress.objectives.packet !== 1) {
        throw new Error('winner was not merged with browser progress: ' + JSON.stringify(state.progress));
    }
    if (!state.activeMerchantIds.has('browser-merchant') || !state.activeMerchantIds.has('packet-merchant')) {
        throw new Error('active merchant union was not preserved');
    }
    if (persistSchedules !== 1) {
        throw new Error('merged snapshot was not scheduled once: ' + persistSchedules);
    }
    if (progressSyncNeedsReconcile) {
        throw new Error('successful reconciliation left writes blocked');
    }
})();`);
run.catch(error => {
    process.stderr.write((error && error.stack ? error.stack : String(error)) + '\n');
    process.exitCode = 1;
});
"""

    result = subprocess.run(
        [NODE, "-e", harness, str(QUEST_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(NODE is None, reason="Node.js is required for browser-script regression tests")
def test_catalog_fetches_ignore_stale_results_and_report_refresh_truthfully() -> None:
    """Only current quest/item requests may render or report refresh success."""

    harness = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const startMarker = '    async function fetchQuests({ force = false, silent = false } = {}) {';
const endMarker = '    function switchView(view) {';
const start = source.indexOf(startMarker);
const end = source.indexOf(endMarker, start);
if (start < 0 || end < 0) {
    throw new Error('Unable to locate production quest catalog fetchers');
}
const productionFetchers = source.slice(start, end);

let questPageDisposed = false;
let questFetchGeneration = 0;
let itemFetchGeneration = 0;
let refreshGeneration = 0;
let questFetchController = null;
let itemFetchController = null;
let progressSyncInFlight = false;
let lastServerSyncPayload = '';
const state = {
    quests: [],
    merchants: [],
    aggregatedItems: [],
    questsLoaded: false,
    itemsLoaded: false,
    activeMerchantIds: new Set(),
    progress: { objectives: {}, items: {} }
};
const element = () => ({ style: {}, classList: { add() {}, remove() {}, toggle() {} } });
const elements = {
    questLoading: element(),
    galleryLoading: element(),
    questList: element(),
    itemsLoading: element(),
    itemsList: element()
};
const notifications = [];
function showNotification(message, type) { notifications.push({ message, type }); }
function toggleLoading() {}
function setRefreshing() {}
function showProgressBar() {}
function hideProgressBar() {}
function showQuestDataWarning() {}
function archiveMissingCompletedQuests() {}
function rebuildQuestDependencyIndex() {}
function renderMerchantOptions() {}
function renderMerchantGallery() {}
function renderMerchantView() {}
function renderItemsList() {}
function ensureItemsLoaderAttached() {}
function renderError() {}
function isQuestTimeLimited() { return false; }
function sanitizeProgressData(value) { return value; }
function mergeProgressData(_base, incoming) { return incoming; }
function schedulePersistProgress() {}
const FORCED_HIDDEN_MERCHANTS = new Set();

let fetchImpl;
const fetch = (...args) => fetchImpl(...args);
function response(payload, ok = true) {
    return { ok, async json() { return payload; } };
}
function deferred() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
}

eval(productionFetchers);

(async () => {
    const pending = [];
    fetchImpl = (url, options) => {
        const request = deferred();
        pending.push({ ...request, url, options });
        return request.promise;
    };

    const staleRequest = fetchQuests();
    const currentRequest = fetchQuests();
    if (!pending[0].options.signal.aborted) {
        throw new Error('superseded quest request was not aborted');
    }

    pending[1].resolve(response({
        success: true,
        quests: [{ id: 'current' }],
        merchants: []
    }));
    if (await currentRequest !== true) {
        throw new Error('current quest request did not report success');
    }

    pending[0].resolve(response({
        success: true,
        quests: [{ id: 'stale' }],
        merchants: []
    }));
    if (await staleRequest !== false) {
        throw new Error('stale quest request was treated as successful');
    }
    if (state.quests.length !== 1 || state.quests[0].id !== 'current') {
        throw new Error(`stale quest response replaced current state: ${JSON.stringify(state.quests)}`);
    }

    state.questsLoaded = true;
    state.itemsLoaded = true;
    notifications.length = 0;
    fetchImpl = async url => {
        if (url.startsWith('/api/quests/items')) {
            return response({ success: true, items: [] });
        }
        return response({ success: false, error: 'quest refresh failed' }, false);
    };
    if (await refreshAll({ force: true }) !== false) {
        throw new Error('partial catalog refresh incorrectly reported success');
    }
    if (notifications.some(entry => entry.message === 'Quest data refreshed')) {
        throw new Error('success toast was shown for a failed catalog refresh');
    }

    notifications.length = 0;
    fetchImpl = async url => url.startsWith('/api/quests/items')
        ? response({ success: true, items: [] })
        : response({ success: true, quests: [], merchants: [] });
    if (await refreshAll({ force: true }) !== true) {
        throw new Error('successful catalog refresh did not report success');
    }
    if (notifications.filter(entry => entry.message === 'Quest data refreshed').length !== 1) {
        throw new Error(`unexpected refresh notifications: ${JSON.stringify(notifications)}`);
    }
})().catch(error => {
    process.stderr.write((error && error.stack ? error.stack : String(error)) + '\n');
    process.exitCode = 1;
});
"""

    result = subprocess.run(
        [NODE, "-e", harness, str(QUEST_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_quest_route_cleanup_removes_document_listener_and_aborts_catalog_requests() -> None:
    """The AJAX cleanup owns every global listener and in-flight catalog request."""

    source = QUEST_SCRIPT.read_text(encoding="utf-8")

    assert "document.addEventListener('keydown', _onQuestKeydown);" in source
    assert "document.removeEventListener('keydown', _onQuestKeydown);" in source

    cleanup_start = source.index("window.__pageCleanup.push(function () {")
    cleanup_end = source.index("    });", cleanup_start)
    cleanup = source[cleanup_start:cleanup_end]
    assert "questPageDisposed = true;" in cleanup
    assert "questFetchController.abort();" in cleanup
    assert "itemFetchController.abort();" in cleanup
    assert "disposeHoldingsPrefetch();" in cleanup
    assert "hideItemHoldingsModal();" in cleanup
    assert "removeHoldingsElementsFromBody();" in cleanup
    assert "elements.itemHoldingsClose.removeEventListener('click', hideItemHoldingsModal);" in cleanup
    assert "elements.itemHoldingsOverlay.removeEventListener('click', hideItemHoldingsModal);" in cleanup


@pytest.mark.skipif(NODE is None, reason="Node.js is required for browser-script regression tests")
def test_holdings_nodes_are_owned_by_each_quest_route_lifecycle() -> None:
    """Body-reparented holdings nodes disappear on cleanup and do not stack on re-entry."""

    harness = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const startMarker = '    const ensureHoldingsElementsInBody = () => {';
const endMarker = '    ensureHoldingsElementsInBody();';
const start = source.indexOf(startMarker);
const end = source.indexOf(endMarker, start);
if (start < 0 || end < 0) {
    throw new Error('Unable to locate production holdings node ownership helpers');
}
const ownershipSource = source.slice(start, end);

const body = {
    children: [],
    appendChild(element) {
        if (element.parentElement) {
            element.parentElement.removeChild(element);
        }
        this.children.push(element);
        element.parentElement = this;
    },
    removeChild(element) {
        const index = this.children.indexOf(element);
        if (index < 0) throw new Error('attempted to remove an unowned node');
        this.children.splice(index, 1);
        element.parentElement = null;
    }
};
const document = { body };

function makeLifecycle(label) {
    const elements = {
        itemHoldingsOverlay: { id: `${label}-overlay`, parentElement: null },
        itemHoldingsModal: { id: `${label}-modal`, parentElement: null }
    };
    return new Function('elements', 'document', `${ownershipSource}
        return { elements, ensureHoldingsElementsInBody, removeHoldingsElementsFromBody };
    `)(elements, document);
}

const first = makeLifecycle('first');
first.ensureHoldingsElementsInBody();
first.ensureHoldingsElementsInBody();
if (body.children.length !== 2) {
    throw new Error(`first route attached ${body.children.length} holdings nodes instead of 2`);
}
first.removeHoldingsElementsFromBody();
if (body.children.length !== 0 || first.elements.itemHoldingsOverlay.parentElement || first.elements.itemHoldingsModal.parentElement) {
    throw new Error('first route left holdings nodes in document.body');
}

const second = makeLifecycle('second');
second.ensureHoldingsElementsInBody();
if (body.children.length !== 2 || body.children.some(node => node.id.startsWith('first-'))) {
    throw new Error(`route re-entry retained stale holdings nodes: ${JSON.stringify(body.children.map(node => node.id))}`);
}
second.removeHoldingsElementsFromBody();
if (body.children.length !== 0) {
    throw new Error('second route cleanup did not restore the original node count');
}
"""

    result = subprocess.run(
        [NODE, "-e", harness, str(QUEST_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(NODE is None, reason="Node.js is required for browser-script regression tests")
def test_holdings_prefetch_cleanup_aborts_and_ignores_stale_response() -> None:
    """A late bulk-holdings response cannot mutate a disposed quest page."""

    harness = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const variablesStart = source.indexOf('    const holdingsPrefetchQueue = new Set();');
const variablesEnd = source.indexOf('    const getCachedHoldingsTotal =', variablesStart);
const functionsStart = source.indexOf('    const enqueueHoldingsPrefetch =', variablesEnd);
const functionsEnd = source.indexOf('    const updateMerchantViewToggle =', functionsStart);
if ([variablesStart, variablesEnd, functionsStart, functionsEnd].some(index => index < 0)) {
    throw new Error('Unable to locate production holdings prefetch lifecycle');
}
const variablesSource = source.slice(variablesStart, variablesEnd);
const functionsSource = source.slice(functionsStart, functionsEnd);

const requests = [];
const fetch = (url, options) => {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    requests.push({ url, options, resolve });
    return promise;
};
const state = { itemHoldingsCache: {}, itemsOwnedFirst: true };
const HOLDINGS_BULK_CHUNK_SIZE = 20;
let updates = 0;
let renders = 0;
const getCachedHoldingsTotal = () => null;
const isHoldingsCacheFresh = () => false;
const updateOwnedLabels = () => { updates += 1; };
const renderItemsList = () => { renders += 1; };

const lifecycle = new Function(
    'fetch',
    'state',
    'HOLDINGS_BULK_CHUNK_SIZE',
    'getCachedHoldingsTotal',
    'isHoldingsCacheFresh',
    'updateOwnedLabels',
    'renderItemsList',
    `${variablesSource}
     let questPageDisposed = false;
     ${functionsSource}
     return {
         enqueueHoldingsPrefetch,
         disposeHoldingsPrefetch,
         markDisposed() { questPageDisposed = true; },
         queueSize() { return holdingsPrefetchQueue.size; },
         isInFlight() { return holdingsPrefetchInFlight; }
     };`
)(fetch, state, HOLDINGS_BULK_CHUNK_SIZE, getCachedHoldingsTotal, isHoldingsCacheFresh, updateOwnedLabels, renderItemsList);

(async () => {
    const ids = Array.from({ length: 21 }, (_, index) => `item-${index}`);
    const pending = lifecycle.enqueueHoldingsPrefetch(ids);
    if (requests.length !== 1 || !requests[0].options.signal) {
        throw new Error('holdings prefetch did not create an abortable request');
    }
    if (lifecycle.queueSize() !== 1 || !lifecycle.isInFlight()) {
        throw new Error('test did not leave a queued follow-up holdings batch');
    }

    lifecycle.markDisposed();
    lifecycle.disposeHoldingsPrefetch();
    if (!requests[0].options.signal.aborted) {
        throw new Error('cleanup did not abort the in-flight holdings request');
    }
    if (lifecycle.queueSize() !== 0 || lifecycle.isInFlight()) {
        throw new Error('cleanup did not clear the holdings queue/in-flight state');
    }

    requests[0].resolve({
        ok: true,
        async json() {
            return { success: true, items: { 'item-0': { total: 99, characters: [] } } };
        }
    });
    if (await pending !== false) {
        throw new Error('disposed holdings prefetch reported success');
    }
    if (Object.keys(state.itemHoldingsCache).length !== 0 || updates !== 0 || renders !== 0) {
        throw new Error('stale holdings response mutated disposed page state');
    }

    if (await lifecycle.enqueueHoldingsPrefetch(['after-dispose']) !== false || requests.length !== 1) {
        throw new Error('disposed page accepted a new holdings prefetch');
    }
})().catch(error => {
    process.stderr.write((error && error.stack ? error.stack : String(error)) + '\n');
    process.exitCode = 1;
});
"""

    result = subprocess.run(
        [NODE, "-e", harness, str(QUEST_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
