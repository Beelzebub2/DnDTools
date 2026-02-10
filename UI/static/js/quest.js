/* global showNotification */
(() => {
    const PROGRESS_STORAGE_KEY = 'dndtools.questProgress.v1';
    const ARCHIVE_STORAGE_KEY = 'dndtools.questArchived.v1';
    const PROGRESS_SYNC_ENDPOINT = '/api/quests/progress';
    const SERVER_SYNC_DEBOUNCE = 400;
    const HOLDINGS_CACHE_TTL = 5 * 60 * 1000;
    const HOLDINGS_BULK_CHUNK_SIZE = 20;

    const createDefaultProgress = () => ({
        objectives: {},
        items: {}
    });

    const loadProgress = () => {
        if (typeof window === 'undefined' || !window.localStorage) {
            return createDefaultProgress();
        }
        try {
            const raw = window.localStorage.getItem(PROGRESS_STORAGE_KEY);
            if (!raw) {
                return createDefaultProgress();
            }
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object') {
                return createDefaultProgress();
            }
            return {
                objectives: parsed.objectives && typeof parsed.objectives === 'object' ? parsed.objectives : {},
                items: parsed.items && typeof parsed.items === 'object' ? parsed.items : {}
            };
        } catch (error) {
            console.warn('Failed to load quest progress from local storage', error);
            return createDefaultProgress();
        }
    };

    let persistTimeout = null;
    let serverPersistTimeout = null;
    let lastServerSyncPayload = '';
    let progressSyncInFlight = false;
    const schedulePersistProgress = (progress) => {
        if (typeof window === 'undefined' || !window.localStorage) {
            return;
        }
        if (persistTimeout) {
            window.clearTimeout(persistTimeout);
        }
        persistTimeout = window.setTimeout(() => {
            try {
                window.localStorage.setItem(PROGRESS_STORAGE_KEY, JSON.stringify(progress));
            } catch (error) {
                console.warn('Failed to persist quest progress', error);
            }
        }, 150);
    };

    const sanitizeProgressData = (raw) => {
        const sanitized = {
            objectives: {},
            items: {}
        };

        if (!raw || typeof raw !== 'object') {
            return sanitized;
        }

        if (raw.objectives && typeof raw.objectives === 'object') {
            Object.entries(raw.objectives).forEach(([key, value]) => {
                if (!value || typeof value !== 'object') {
                    return;
                }
                let objectiveIndex = null;
                if (value.objective_index !== null && value.objective_index !== undefined && value.objective_index !== '') {
                    const numericIndex = Number(value.objective_index);
                    if (Number.isFinite(numericIndex)) {
                        objectiveIndex = numericIndex;
                    }
                }
                const submittedValue = Number(value.submitted);
                const entry = {
                    quest_id: value.quest_id ? String(value.quest_id) : null,
                    objective_index: objectiveIndex,
                    type: value.type ? String(value.type) : null,
                    item_id: value.item_id ? String(value.item_id) : null,
                    submitted: Number.isFinite(submittedValue) ? Math.max(0, Math.round(submittedValue)) : 0,
                    completed: Boolean(value.completed)
                };
                sanitized.objectives[String(key)] = entry;
            });
        }

        if (raw.items && typeof raw.items === 'object') {
            Object.entries(raw.items).forEach(([key, value]) => {
                const numeric = Number(value);
                if (Number.isFinite(numeric) && numeric >= 0) {
                    sanitized.items[String(key)] = Math.max(0, Math.round(numeric));
                }
            });
        }

        return sanitized;
    };

    const mergeProgressData = (base, incoming) => {
        const resultObjectives = { ...(base && base.objectives ? base.objectives : {}) };
        const incomingObjectives = incoming && incoming.objectives ? incoming.objectives : {};
        Object.entries(incomingObjectives).forEach(([key, value]) => {
            const existing = resultObjectives[key] || {};
            const merged = { ...existing, ...value };
            const existingSubmitted = Number(existing.submitted);
            const incomingSubmitted = Number(value && value.submitted);
            if (Number.isFinite(existingSubmitted) || Number.isFinite(incomingSubmitted)) {
                const maxSubmitted = Math.max(
                    Number.isFinite(existingSubmitted) ? existingSubmitted : 0,
                    Number.isFinite(incomingSubmitted) ? incomingSubmitted : 0
                );
                merged.submitted = Math.max(0, Math.round(maxSubmitted));
            }
            if ((existing && existing.completed) || (value && value.completed)) {
                merged.completed = Boolean((existing && existing.completed) || (value && value.completed));
            }
            resultObjectives[key] = merged;
        });

        const resultItems = { ...(base && base.items ? base.items : {}) };
        const incomingItems = incoming && incoming.items ? incoming.items : {};
        Object.entries(incomingItems).forEach(([key, value]) => {
            const existingValue = Number(resultItems[key]);
            const incomingValue = Number(value);
            if (Number.isFinite(existingValue) || Number.isFinite(incomingValue)) {
                resultItems[key] = Math.max(
                    0,
                    Math.round(
                        Math.max(
                            Number.isFinite(existingValue) ? existingValue : 0,
                            Number.isFinite(incomingValue) ? incomingValue : 0
                        )
                    )
                );
            } else {
                resultItems[key] = value;
            }
        });

        return {
            objectives: resultObjectives,
            items: resultItems
        };
    };

    const progressPayloadForServer = () => {
        const sourceProgress = state && state.progress ? state.progress : { objectives: {}, items: {} };
        return sanitizeProgressData({
            objectives: sourceProgress.objectives,
            items: sourceProgress.items
        });
    };

    const sendProgressToServer = async ({ keepalive = false } = {}) => {
        if (typeof fetch !== 'function') {
            return;
        }

        const normalized = progressPayloadForServer();
        const payload = JSON.stringify({ progress: normalized });
        if (payload === lastServerSyncPayload) {
            return;
        }

        const attemptSendBeacon = () => {
            if (!keepalive || typeof navigator === 'undefined' || typeof navigator.sendBeacon !== 'function') {
                return false;
            }
            try {
                const blob = new Blob([payload], { type: 'application/json' });
                const sent = navigator.sendBeacon(PROGRESS_SYNC_ENDPOINT, blob);
                if (sent) {
                    lastServerSyncPayload = payload;
                }
                return sent;
            } catch (error) {
                console.warn('Failed to persist quest progress via sendBeacon', error);
                return false;
            }
        };

        if (attemptSendBeacon()) {
            return;
        }

        try {
            const response = await fetch(PROGRESS_SYNC_ENDPOINT, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: payload,
                keepalive
            });
            if (!response.ok) {
                throw new Error(`Server responded with ${response.status}`);
            }
            lastServerSyncPayload = payload;
        } catch (error) {
            console.warn('Failed to persist quest progress to backend', error);
        }
    };

    const scheduleServerPersistProgress = ({ immediate = false } = {}) => {
        if (immediate) {
            if (serverPersistTimeout) {
                window.clearTimeout(serverPersistTimeout);
                serverPersistTimeout = null;
            }
            sendProgressToServer({ keepalive: true });
            return;
        }

        if (serverPersistTimeout) {
            window.clearTimeout(serverPersistTimeout);
        }
        serverPersistTimeout = window.setTimeout(() => {
            serverPersistTimeout = null;
            sendProgressToServer();
        }, SERVER_SYNC_DEBOUNCE);
    };

    const state = {
        quests: [],
        merchants: [],
        aggregatedItems: [],
        selectedMerchant: '',
        itemSearch: '',
        itemsOwnedFirst: false,
        questsLoaded: false,
        itemsLoaded: false,
        merchantViewMode: 'active',
        progress: sanitizeProgressData(loadProgress()),
        // archived completed quests that were present previously but no longer returned by the server
        archivedCompletedQuests: [],
        itemHoldingsCache: {},
        activeHoldingsItemId: null,
        activeHoldingsAnchor: null,
        bodyScrollLock: null,
        hideLockedQuests: false
    };

    let globalRenderTimeout = null;
    const scheduleGlobalRender = () => {
        if (globalRenderTimeout) {
            window.clearTimeout(globalRenderTimeout);
        }
        globalRenderTimeout = window.setTimeout(() => {
            globalRenderTimeout = null;
            renderMerchantView();
            if (state.itemsLoaded) {
                renderItemsList();
            }
        }, 120);
    };

    // Load archived completed quests from localStorage (if any)
    const loadArchivedCompletedQuests = () => {
        if (typeof window === 'undefined' || !window.localStorage) return [];
        try {
            const raw = window.localStorage.getItem(ARCHIVE_STORAGE_KEY);
            if (!raw) return [];
            const parsed = JSON.parse(raw);
            if (!Array.isArray(parsed)) return [];
            return parsed.filter(Boolean).map(q => (typeof q === 'object' ? q : null)).filter(Boolean);
        } catch (e) {
            console.warn('Failed to load archived completed quests', e);
            return [];
        }
    };

    const persistArchivedCompletedQuests = (arr) => {
        if (typeof window === 'undefined' || !window.localStorage) return;
        try {
            window.localStorage.setItem(ARCHIVE_STORAGE_KEY, JSON.stringify(Array.isArray(arr) ? arr : []));
        } catch (e) {
            console.warn('Failed to persist archived completed quests', e);
        }
    };

    // initialize archived list from storage
    state.archivedCompletedQuests = loadArchivedCompletedQuests();

    const elements = {
        questList: document.getElementById('questList'),
        questLoading: document.getElementById('questLoading'),
        merchantSelect: document.getElementById('merchantSelect'),
        merchantStats: document.getElementById('merchantStats'),
        merchantRefresh: document.getElementById('merchantRefresh'),
        questTabs: document.querySelectorAll('.quest-tab'),
        views: {
            merchant: document.getElementById('merchantView'),
            items: document.getElementById('itemsView')
        },
        refreshAll: document.getElementById('refreshAll'),
        itemsLoading: document.getElementById('itemsLoading'),
        itemsList: document.getElementById('itemsList'),
        itemsSearch: document.getElementById('itemSearch'),
        clearItemSearch: document.getElementById('clearItemSearch'),
        itemsMeta: document.getElementById('itemsMeta'),
        itemsRefresh: document.getElementById('itemsRefresh'),
        itemsOwnedFirst: document.getElementById('itemsOwnedFirst'),
        merchantViewToggle: document.querySelectorAll('.merchant-view-toggle .view-toggle-btn'),
        itemHoldingsOverlay: document.getElementById('itemHoldingsOverlay'),
        itemHoldingsModal: document.getElementById('itemHoldingsModal'),
        itemHoldingsDialog: document.getElementById('itemHoldingsDialog'),
        itemHoldingsTitle: document.getElementById('itemHoldingsTitle'),
        itemHoldingsSummary: document.getElementById('itemHoldingsSummary'),
        itemHoldingsBody: document.getElementById('itemHoldingsBody'),
        itemHoldingsClose: document.getElementById('itemHoldingsClose'),
        prerequisiteToggle: document.getElementById('prerequisiteFilter'),
        progressBar: document.getElementById('questProgressBar')
    };

    const questAliasIndex = new Map();

    /* ─── Progress Bar ─── */
    let progressBarRefCount = 0;
    const showProgressBar = () => {
        progressBarRefCount += 1;
        if (elements.progressBar) {
            elements.progressBar.classList.add('active');
        }
    };
    const hideProgressBar = () => {
        progressBarRefCount = Math.max(0, progressBarRefCount - 1);
        if (progressBarRefCount === 0 && elements.progressBar) {
            elements.progressBar.classList.remove('active');
        }
    };

    /* Mark a list container as refreshing (dims it but keeps content visible) */
    const setRefreshing = (container, isRefreshing) => {
        if (!container) return;
        if (isRefreshing) {
            container.classList.add('is-refreshing');
        } else {
            container.classList.remove('is-refreshing');
        }
    };

    let questAliasEntries = [];
    const questTitleIndex = new Map();
    const questLockIndex = new Map();
    let questCompletionIndex = new Map();

    if (!elements.questList || !elements.itemsList) {
        return;
    }

    const ensureItemsLoaderAttached = () => {
        if (!elements.itemsList || !elements.itemsLoading) {
            return;
        }
        if (elements.itemsLoading.parentElement !== elements.itemsList) {
            elements.itemsList.insertBefore(elements.itemsLoading, elements.itemsList.firstChild || null);
        }
    };

    const ensureHoldingsElementsInBody = () => {
        if (!document || !document.body) {
            return;
        }
        if (elements.itemHoldingsOverlay && elements.itemHoldingsOverlay.parentElement !== document.body) {
            document.body.appendChild(elements.itemHoldingsOverlay);
        }
        if (elements.itemHoldingsModal && elements.itemHoldingsModal.parentElement !== document.body) {
            document.body.appendChild(elements.itemHoldingsModal);
        }
    };

    ensureHoldingsElementsInBody();
    const updateItemsOwnedToggleUI = () => {
        if (!elements.itemsOwnedFirst) {
            return;
        }
        const isChecked = Boolean(state.itemsOwnedFirst);
        elements.itemsOwnedFirst.checked = isChecked;
        elements.itemsOwnedFirst.setAttribute('aria-checked', isChecked ? 'true' : 'false');
        const wrapper = elements.itemsOwnedFirst.closest('.custom-checkbox');
        if (wrapper) {
            wrapper.classList.toggle('active', isChecked);
        }
    };

    updateItemsOwnedToggleUI();

    const updatePrerequisiteToggleUI = () => {
        if (!elements.prerequisiteToggle) {
            return;
        }
        const isChecked = Boolean(state.hideLockedQuests);
        elements.prerequisiteToggle.checked = isChecked;
        elements.prerequisiteToggle.setAttribute('aria-checked', isChecked ? 'true' : 'false');
    };

    updatePrerequisiteToggleUI();

    const runWithButtonLoading = async (button, task) => {
        const target = button instanceof HTMLElement ? button : null;
        if (target) {
            target.classList.add('is-loading');
            if ('disabled' in target) {
                target.disabled = true;
            }
        }
        try {
            return await Promise.resolve().then(() => task && task());
        } finally {
            if (target) {
                target.classList.remove('is-loading');
                if ('disabled' in target) {
                    target.disabled = false;
                }
            }
        }
    };

    if (elements.itemHoldingsClose) {
        elements.itemHoldingsClose.addEventListener('click', hideItemHoldingsModal);
    }
    if (elements.itemHoldingsOverlay) {
        elements.itemHoldingsOverlay.addEventListener('click', hideItemHoldingsModal);
    }
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && state.activeHoldingsItemId && elements.itemHoldingsModal && !elements.itemHoldingsModal.classList.contains('hidden')) {
            hideItemHoldingsModal();
        }
    });

    const clampNumber = (value, min, max) => {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) {
            return min;
        }
        if (Number.isFinite(min) && numeric < min) {
            return min;
        }
        if (Number.isFinite(max) && numeric > max) {
            return max;
        }
        return numeric;
    };

    const cssEscape = (value) => {
        if (value === undefined || value === null) {
            return '';
        }
        const stringValue = String(value);
        if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
            return CSS.escape(stringValue);
        }
        return stringValue.replace(/[^a-zA-Z0-9_-]/g, match => `\\${match}`);
    };

    const holdingsPrefetchQueue = new Set();
    let holdingsPrefetchInFlight = false;

    const getCachedHoldingsTotal = (itemId, allowedLootStates = null) => {
        if (!itemId) {
            return null;
        }
        const cached = state.itemHoldingsCache[itemId];
        if (!cached || !cached.data) {
            return null;
        }

        if (!allowedLootStates) {
            const total = Number(cached.data.total);
            return Number.isFinite(total) ? total : 0;
        }

        let filteredTotal = 0;
        const characters = Array.isArray(cached.data.characters) ? cached.data.characters : [];
        characters.forEach(char => {
            const stashes = Array.isArray(char.stashes) ? char.stashes : [];
            stashes.forEach(stash => {
                const count = Number(stash.count) || 0;
                if (count <= 0) return;

                const lootState = stash.loot_state !== undefined ? Number(stash.loot_state) : null;
                if (allowedLootStates.has(lootState)) {
                    filteredTotal += count;
                }
            });
        });
        return filteredTotal;
    };

    const updateOwnedLabels = (itemId) => {
        if (!itemId) {
            return;
        }
        const selector = `.item-owned-value[data-item-id="${cssEscape(itemId)}"]`;
        document.querySelectorAll(selector).forEach(node => {
            const filterRaw = node.dataset.lootFilter;
            let allowedLootStates = null;
            if (filterRaw) {
                try {
                    const parsed = JSON.parse(filterRaw);
                    if (Array.isArray(parsed)) {
                        allowedLootStates = new Set(parsed.map(Number));
                    }
                } catch (e) {
                    // ignore
                }
            }

            const total = getCachedHoldingsTotal(itemId, allowedLootStates);
            node.textContent = total !== null ? total : '—';
        });
    };

    const isHoldingsCacheFresh = (itemId) => {
        const cached = state.itemHoldingsCache[itemId];
        if (!cached) {
            return false;
        }
        const timestamp = Number(cached.timestamp);
        if (!Number.isFinite(timestamp)) {
            return false;
        }
        return (Date.now() - timestamp) < HOLDINGS_CACHE_TTL;
    };

    const getOwnedTotalForItem = (item) => {
        if (!item) {
            return 0;
        }
        const itemId = item.item_id || item.itemId || '';
        const total = getCachedHoldingsTotal(itemId);
        return total !== null ? total : 0;
    };

    const enqueueHoldingsPrefetch = (ids) => {
        if (!Array.isArray(ids) || !ids.length) {
            return;
        }
        ids.forEach((rawId) => {
            const normalized = (rawId || '').toString().trim();
            if (!normalized) {
                return;
            }
            const cachedTotal = getCachedHoldingsTotal(normalized);
            if (cachedTotal !== null) {
                updateOwnedLabels(normalized);
            }
            if (isHoldingsCacheFresh(normalized)) {
                return;
            }
            holdingsPrefetchQueue.add(normalized);
        });
        if (!holdingsPrefetchInFlight) {
            processHoldingsPrefetchQueue();
        }
    };

    async function processHoldingsPrefetchQueue() {
        if (holdingsPrefetchInFlight || holdingsPrefetchQueue.size === 0 || typeof fetch !== 'function') {
            return;
        }
        let shouldRerenderItems = false;
        holdingsPrefetchInFlight = true;
        try {
            while (holdingsPrefetchQueue.size) {
                const batch = Array.from(holdingsPrefetchQueue).slice(0, HOLDINGS_BULK_CHUNK_SIZE);
                batch.forEach(id => holdingsPrefetchQueue.delete(id));
                try {
                    const response = await fetch(`/api/quests/items/holdings?ids=${encodeURIComponent(batch.join(','))}`, {
                        cache: 'no-store'
                    });
                    let payload = null;
                    try {
                        payload = await response.json();
                    } catch (parseError) {
                        console.warn('Failed to parse holdings response', parseError);
                    }
                    if (!response.ok || !payload || payload.success === false || !payload.items) {
                        console.warn('Failed to load holdings batch', response.status, payload && payload.error);
                        continue;
                    }
                    const now = Date.now();
                    batch.forEach((id) => {
                        const summary = payload.items[id] || { total: 0, characters: [] };
                        state.itemHoldingsCache[id] = {
                            data: summary,
                            timestamp: now
                        };
                        updateOwnedLabels(id);
                        if (state.itemsOwnedFirst) {
                            shouldRerenderItems = true;
                        }
                    });
                } catch (error) {
                    console.warn('Error prefetching holdings batch', error);
                }
            }
        } finally {
            holdingsPrefetchInFlight = false;
            if (holdingsPrefetchQueue.size) {
                processHoldingsPrefetchQueue();
            }
            if (shouldRerenderItems) {
                renderItemsList();
            }
        }
    }

    const updateMerchantViewToggle = () => {
        if (!elements.merchantViewToggle || !elements.merchantViewToggle.length) {
            return;
        }
        elements.merchantViewToggle.forEach(button => {
            const mode = button.dataset.mode || 'active';
            const isActive = mode === state.merchantViewMode;
            button.classList.toggle('active', isActive);
            button.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });
    };

    const makeObjectiveKey = (quest, index, objective) => {
        const questId = quest.id || quest.title || `quest-${index}`;
        const parts = [questId, objective.type || 'Objective', index];
        if (objective.item_id) {
            parts.push(objective.item_id);
        } else if (objective.monster) {
            parts.push(objective.monster);
        } else if (objective.module) {
            parts.push(objective.module);
        } else if (objective.interact) {
            parts.push(objective.interact);
        }
        return parts.join('::');
    };

    const getObjectiveProgress = (key) => state.progress.objectives[key] || null;

    const setObjectiveProgress = (key, payload) => {
        const current = state.progress.objectives[key] || {};
        const questId = payload.quest_id ?? current.quest_id;
        const objectiveIndex = payload.objective_index ?? current.objective_index;
        const type = payload.type ?? current.type;
        const rawItemId = payload.item_id !== undefined ? payload.item_id : current.item_id;

        let submitted = payload.submitted;
        if (submitted === undefined) {
            submitted = current.submitted;
        }
        submitted = Number(submitted);
        if (!Number.isFinite(submitted) || submitted < 0) {
            submitted = 0;
        }

        const completed = payload.completed !== undefined ? Boolean(payload.completed) : Boolean(current.completed);

        const record = {
            quest_id: questId,
            objective_index: objectiveIndex,
            type,
            submitted,
            completed,
        };

        if (rawItemId) {
            record.item_id = rawItemId;
        }

        if (!record.completed && submitted <= 0 && !record.item_id) {
            delete state.progress.objectives[key];
        } else {
            state.progress.objectives[key] = record;
        }

        schedulePersistProgress(state.progress);
        scheduleServerPersistProgress();
    };

    const distributeItemProgress = (itemId, totalValue) => {
        if (!itemId) {
            return;
        }

        // Clear manual override first
        if (state.progress.items && state.progress.items[itemId] !== undefined) {
            delete state.progress.items[itemId];
        }

        const sanitizedTotal = Math.max(0, Number(totalValue) || 0);
        let remainingToDistribute = sanitizedTotal;

        // Find all active objectives for this item
        const targets = [];
        state.quests.forEach(quest => {
            if (!quest.objectives) return;
            quest.objectives.forEach((obj, index) => {
                if (obj.type === 'Fetch' && obj.item_id === itemId && obj.count > 0) {
                    targets.push({ quest, obj, index });
                }
            });
        });

        // Sort targets? Maybe by quest ID to be deterministic
        targets.sort((a, b) => {
            const qA = String(a.quest.id || '');
            const qB = String(b.quest.id || '');
            return qA.localeCompare(qB) || (a.index - b.index);
        });

        targets.forEach(({ quest, obj, index }) => {
            const key = makeObjectiveKey(quest, index, obj);
            const needed = obj.count;
            const take = Math.min(needed, remainingToDistribute);

            const completed = take >= needed;

            setObjectiveProgress(key, {
                quest_id: quest.id,
                objective_index: index,
                type: obj.type,
                item_id: obj.item_id,
                submitted: take,
                completed: completed
            });

            remainingToDistribute -= take;
        });

        schedulePersistProgress(state.progress);
        scheduleServerPersistProgress();
    };

    const getObjectiveSubmissionsForItem = (itemId, options = {}) => {
        if (!itemId) {
            return 0;
        }
        const entries = state.progress && state.progress.objectives
            ? Object.values(state.progress.objectives)
            : [];
        const allowedQuestIds = Array.isArray(options.allowedQuestIds) && options.allowedQuestIds.length
            ? new Set(options.allowedQuestIds.map(value => (value !== undefined && value !== null ? String(value) : '')).filter(Boolean))
            : null;
        return entries.reduce((total, entry) => {
            if (!entry || entry.item_id !== itemId) {
                return total;
            }
            if (allowedQuestIds && allowedQuestIds.size) {
                const questId = entry.quest_id !== undefined && entry.quest_id !== null ? String(entry.quest_id) : '';
                if (!questId || !allowedQuestIds.has(questId)) {
                    return total;
                }
            }
            const amount = Number(entry.submitted) || 0;
            return total + (amount > 0 ? amount : 0);
        }, 0);
    };

    const getManualItemProgress = (itemId) => {
        if (!itemId || !state.progress.items) {
            return undefined;
        }
        const value = state.progress.items[itemId];
        return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
    };

    const setItemProgress = (itemId, value) => {
        if (!itemId) {
            return;
        }
        if (!state.progress.items) {
            state.progress.items = {};
        }

        if (value === null || value === undefined || value === '') {
            delete state.progress.items[itemId];
        } else {
            const sanitized = Number(value);
            if (Number.isFinite(sanitized) && sanitized >= 0) {
                state.progress.items[itemId] = sanitized;
            } else {
                delete state.progress.items[itemId];
            }
        }

        schedulePersistProgress(state.progress);
        scheduleServerPersistProgress();
    };

    const getSubmittedForItem = (itemId) => {
        const manual = getManualItemProgress(itemId);
        if (manual !== undefined) {
            return manual;
        }
        return getObjectiveSubmissionsForItem(itemId);
    };

    const isObjectiveCompleted = (quest, objective, originalIndex) => {
        const indexToUse = typeof originalIndex === 'number' ? originalIndex : 0;
        const key = makeObjectiveKey(quest, indexToUse, objective);
        const stored = getObjectiveProgress(key);
        return Boolean(stored && stored.completed);
    };

    const partitionQuestObjectives = (quest) => {
        const objectives = Array.isArray(quest.objectives) ? quest.objectives : [];
        const result = {
            active: [],
            completed: [],
            totalCount: objectives.length
        };

        objectives.forEach((objective, index) => {
            const completed = isObjectiveCompleted(quest, objective, index);
            const annotated = { ...objective, __originalIndex: index, __isCompleted: completed };
            if (completed) {
                result.completed.push(annotated);
            } else {
                result.active.push(annotated);
            }
        });

        return result;
    };

    const questKeyFor = (quest) => {
        if (!quest) {
            return '';
        }
        const key = quest.id || quest.title || '';
        return key ? String(key) : '';
    };

    const generateAliasVariants = (value) => {
        if (value === undefined || value === null) {
            return [];
        }
        const stringValue = String(value)
            .replace(/[“”"‘’]/g, '')
            .replace(/[\u2013\u2014]/g, '-')
            .toLowerCase();
        const normalized = stringValue
            .replace(/[^a-z0-9]+/g, ' ')
            .replace(/\s{2,}/g, ' ')
            .trim();
        if (!normalized) {
            return [];
        }
        const variants = new Set([normalized]);
        const words = normalized.split(' ').filter(Boolean);
        if (words.length > 1) {
            const withoutArticles = words.filter(word => !['the', 'a', 'an'].includes(word));
            if (withoutArticles.length) {
                variants.add(withoutArticles.join(' '));
            }
            const withoutFiller = words.filter(word => !['the', 'a', 'an', 'complete', 'completed', 'finish', 'finished', 'quest', 'quests', 'mission', 'missions', 'chapter', 'chapters', 'task', 'tasks'].includes(word));
            if (withoutFiller.length) {
                variants.add(withoutFiller.join(' '));
            }
        }
        return Array.from(variants).map(entry => entry.trim()).filter(Boolean);
    };

    const collectAliasesForQuest = (quest) => {
        const aliases = new Set();
        const pushVariants = (value) => {
            generateAliasVariants(value).forEach(alias => aliases.add(alias));
        };
        pushVariants(quest.title);
        pushVariants(quest.id);
        if (quest && quest.title && quest.title.includes(':')) {
            quest.title.split(':').forEach(part => pushVariants(part));
        }
        if (quest && quest.title && quest.title.includes('—')) {
            quest.title.split('—').forEach(part => pushVariants(part));
        }
        if (quest && quest.chapter) {
            pushVariants(quest.chapter);
        }
        return Array.from(aliases).filter(Boolean);
    };

    const extractPrerequisiteReferences = (raw) => {
        if (!raw) {
            return [];
        }
        if (Array.isArray(raw)) {
            return raw.map(value => String(value).trim()).filter(Boolean);
        }
        let working = String(raw).trim();
        if (!working) {
            return [];
        }
        working = working.replace(/[\u2013\u2014]/g, ',');
        working = working.replace(/\b(?:then|after|followed by|next|until)\b/gi, ',');
        working = working.replace(/\b(?:and|or)\b/gi, ',');
        working = working.replace(/[&+/;]/g, ',');
        const segments = working.split(',').map(segment => segment.trim()).filter(Boolean);
        if (!segments.length) {
            return [String(raw).trim()];
        }
        return segments;
    };

    const resolvePrerequisiteReferences = (quest) => {
        const references = extractPrerequisiteReferences(quest && quest.prerequisite);
        if (!references.length) {
            return [];
        }
        const questKey = questKeyFor(quest);
        const resolved = [];
        references.forEach((reference) => {
            const variants = generateAliasVariants(reference);
            if (!variants.length) {
                return;
            }
            let matchedId = null;
            for (const variant of variants) {
                const direct = questAliasIndex.get(variant);
                if (direct && direct.length) {
                    matchedId = direct.find(id => id !== questKey) || direct[0];
                    if (matchedId) {
                        break;
                    }
                }
            }
            if (!matchedId) {
                const fallbackVariant = variants.find(variant => variant.length >= 3);
                if (fallbackVariant) {
                    for (const [alias, ids] of questAliasEntries) {
                        if ((alias.length >= 3 && alias.includes(fallbackVariant)) || (fallbackVariant.length >= 3 && fallbackVariant.includes(alias))) {
                            matchedId = ids.find(id => id !== questKey) || null;
                            if (matchedId) {
                                break;
                            }
                        }
                    }
                }
            }
            if (matchedId && !resolved.includes(matchedId)) {
                resolved.push(matchedId);
            }
        });
        return resolved;
    };

    // Determine whether a quest appears to be time-limited (daily/weekly/seasonal).
    // We use several heuristics since the API returns all quests and doesn't
    // indicate "active" status: merchant/title strings may include frequency
    // terms or the quest object may include frequency-like fields.
    const isQuestTimeLimited = (quest) => {
        if (!quest || typeof quest !== 'object') return false;
        const keywords = ['daily', 'weekly', 'seasonal', 'season'];
        const testString = (val) => {
            if (!val && val !== 0) return '';
            try { return String(val).toLowerCase(); } catch (e) { return ''; }
        };
        const merchant = testString(quest.merchant || quest.merchant_original || '');
        const title = testString(quest.title || quest.id || '');
        const id = testString(quest.id || '');
        // check explicit fields that may exist on some sources
        const freq = testString(quest.frequency || quest.repeat || quest.recurrence || quest.schedule || '');
        for (const kw of keywords) {
            if (merchant.includes(kw) || title.includes(kw) || id.includes(kw) || freq.includes(kw)) {
                return true;
            }
        }
        return false;
    };

    // Hard-coded merchants the UI should never show (normalized form).
    // Add merchant names here (lowercase, normalized) to forcibly hide them
    // regardless of whether they have permanent quests.
    const FORCED_HIDDEN_MERCHANTS = new Set([
        'huntress'
    ]);

    const rebuildQuestDependencyIndex = () => {
        questAliasIndex.clear();
        questAliasEntries = [];
        questTitleIndex.clear();

        state.quests.forEach((quest) => {
            const questKey = questKeyFor(quest);
            if (!questKey) {
                quest.__resolvedPrerequisites = [];
                quest.__resolvedPrerequisiteTitles = [];
                return;
            }
            questTitleIndex.set(questKey, quest.title || quest.id || questKey);
            const aliases = collectAliasesForQuest(quest);
            aliases.forEach((alias) => {
                if (!questAliasIndex.has(alias)) {
                    questAliasIndex.set(alias, []);
                }
                const bucket = questAliasIndex.get(alias);
                if (!bucket.includes(questKey)) {
                    bucket.push(questKey);
                }
            });
        });

        questAliasEntries = Array.from(questAliasIndex.entries());

        state.quests.forEach((quest) => {
            const resolved = resolvePrerequisiteReferences(quest);
            quest.__resolvedPrerequisites = resolved;
            quest.__resolvedPrerequisiteTitles = resolved.map(id => questTitleIndex.get(id) || id);
        });
        // Compute a display order based on prerequisites (topological sort). This
        // produces a map from questKey -> numericOrder where prerequisites come
        // before dependents. If cycles exist, remaining nodes are appended in
        // a deterministic order by title.
        try {
            const nodes = new Set();
            state.quests.forEach(q => {
                const k = questKeyFor(q);
                if (k) nodes.add(k);
            });

            const indegree = new Map();
            const adj = new Map();
            nodes.forEach(n => { indegree.set(n, 0); adj.set(n, []); });

            state.quests.forEach(q => {
                const k = questKeyFor(q);
                if (!k) return;
                const deps = Array.isArray(q.__resolvedPrerequisites) ? q.__resolvedPrerequisites : [];
                deps.forEach(dep => {
                    if (!dep || !nodes.has(dep)) return;
                    adj.get(dep).push(k);
                    indegree.set(k, (indegree.get(k) || 0) + 1);
                });
            });

            const queue = [];
            indegree.forEach((d, node) => { if (!d) queue.push(node); });

            const order = [];
            while (queue.length) {
                const node = queue.shift();
                order.push(node);
                const neighbors = adj.get(node) || [];
                neighbors.forEach(nbr => {
                    indegree.set(nbr, (indegree.get(nbr) || 0) - 1);
                    if (indegree.get(nbr) === 0) {
                        queue.push(nbr);
                    }
                });
            }

            // If there are cycles or disconnected nodes not processed, append
            // them in a stable order by title so UI remains deterministic.
            const remaining = Array.from(nodes).filter(n => !order.includes(n));
            remaining.sort((a, b) => {
                const ta = (questTitleIndex.get(a) || a).toLowerCase();
                const tb = (questTitleIndex.get(b) || b).toLowerCase();
                if (ta < tb) return -1; if (ta > tb) return 1; return 0;
            });
            const finalOrder = order.concat(remaining);

            // create a quick lookup map
            state.questDisplayOrder = new Map();
            finalOrder.forEach((qk, idx) => state.questDisplayOrder.set(qk, idx));
        } catch (e) {
            // If anything goes wrong, clear any existing display order to avoid crashes
            state.questDisplayOrder = new Map();
        }
    };

    const computeQuestCompletionIndex = () => {
        const completion = new Map();
        state.quests.forEach((quest) => {
            const partitions = partitionQuestObjectives(quest);
            const totalObjectives = Number(partitions.totalCount) || 0;
            const completedObjectives = partitions.completed.length;
            const activeObjectives = partitions.active.length;
            const allCompleted = totalObjectives > 0
                ? completedObjectives === totalObjectives
                : activeObjectives === 0 && completedObjectives === 0;
            const key = questKeyFor(quest);
            if (key) {
                completion.set(key, allCompleted);
            }
        });
        return completion;
    };

    const areQuestPrerequisitesMet = (quest, completionIndex) => {
        const dependencies = Array.isArray(quest && quest.__resolvedPrerequisites) ? quest.__resolvedPrerequisites : [];
        if (!dependencies.length) {
            return true;
        }
        return dependencies.every((dependencyId) => {
            if (!dependencyId) {
                return true;
            }
            if (!completionIndex.has(dependencyId)) {
                return true;
            }
            return completionIndex.get(dependencyId) === true;
        });
    };

    const recomputeQuestLockState = () => {
        questLockIndex.clear();
        questCompletionIndex = computeQuestCompletionIndex();
        state.quests.forEach((quest) => {
            const questKey = questKeyFor(quest);
            if (!questKey) {
                return;
            }
            const locked = !areQuestPrerequisitesMet(quest, questCompletionIndex);
            questLockIndex.set(questKey, locked);
            quest.__isLocked = locked;
        });
        return questCompletionIndex;
    };

    const isQuestLocked = (questId) => {
        if (!questId) {
            return false;
        }
        return questLockIndex.get(questId) === true;
    };

    const buildMerchantView = (quests, viewMode = 'active') => {
        // Exclude time-limited quests (daily/weekly/seasonal) from all displayed
        // quest lists — even if they exist in the local cache — per user request.
        // We still keep the full data in `state.quests` so item tracking and
        // badge computation continues to account for those quests.
        const originalQuests = Array.isArray(quests) ? quests.slice() : [];
        const questsToDisplay = originalQuests.filter(q => !isQuestTimeLimited(q));
        quests = questsToDisplay;
        const questsForView = [];
        let questsCount = 0;
        let objectiveCount = 0;
        let itemObjectiveCount = 0;
        let totalActive = 0;
        let totalCompleted = 0;
        let hiddenByPrerequisite = 0;
        let lockedVisible = 0;

        recomputeQuestLockState();

        // Iterate only over quests allowed to be displayed (time-limited ones are removed)
        quests.forEach((quest) => {
            const partitions = partitionQuestObjectives(quest);
            const totalObjectives = Number(partitions.totalCount) || 0;
            const completedObjectives = partitions.completed.length;
            const activeObjectives = partitions.active.length;
            const allObjectivesCompleted = totalObjectives > 0
                ? completedObjectives === totalObjectives
                : activeObjectives === 0 && completedObjectives === 0;

            const questKey = questKeyFor(quest);
            const isLocked = questKey ? questLockIndex.get(questKey) === true : false;
            if (isLocked) {
                hiddenByPrerequisite += 1;
                if (state.hideLockedQuests) {
                    return;
                }
            }

            totalActive += partitions.active.length;
            totalCompleted += partitions.completed.length;

            let relevantObjectives;
            if (viewMode === 'completed') {
                if (!allObjectivesCompleted) {
                    return;
                }
                relevantObjectives = partitions.completed;
            } else {
                if (allObjectivesCompleted) {
                    return;
                }
                relevantObjectives = [...partitions.active, ...partitions.completed];
            }

            if (!relevantObjectives.length) {
                return;
            }

            const objectivesForStatistics = viewMode === 'completed' ? partitions.completed : partitions.active;
            questsCount += 1;
            objectiveCount += objectivesForStatistics.length;
            itemObjectiveCount += objectivesForStatistics.filter(obj => obj.type === 'Fetch').length;

            const objectivesWithIndex = relevantObjectives.map(obj => ({ ...obj }));
            if (isLocked) {
                lockedVisible += 1;
            }
            questsForView.push({
                quest,
                objectives: objectivesWithIndex,
                isLocked,
                lockedPrerequisites: Array.isArray(quest.__resolvedPrerequisiteTitles) ? quest.__resolvedPrerequisiteTitles : []
            });
        });

        // Sort questsForView by prerequisite display order if available.
        try {
            const orderMap = state.questDisplayOrder instanceof Map ? state.questDisplayOrder : null;
            if (orderMap) {
                questsForView.sort((a, b) => {
                    const ak = questKeyFor(a.quest) || '';
                    const bk = questKeyFor(b.quest) || '';
                    const ai = orderMap.has(ak) ? orderMap.get(ak) : Number.MAX_SAFE_INTEGER;
                    const bi = orderMap.has(bk) ? orderMap.get(bk) : Number.MAX_SAFE_INTEGER;
                    if (ai !== bi) return ai - bi;
                    // fallback to title
                    const at = (a.quest.title || ak).toLowerCase();
                    const bt = (b.quest.title || bk).toLowerCase();
                    if (at < bt) return -1; if (at > bt) return 1; return 0;
                });
            }
        } catch (e) {
            // ignore sort errors and leave the original order
        }

        return {
            questsForView,
            summary: {
                viewMode,
                questsCount,
                objectiveCount,
                itemObjectiveCount,
                totalFiltered: quests.length,
                totalActive,
                totalCompleted,
                lockedHidden: hiddenByPrerequisite,
                lockedVisible
            }
        };
    };

    // Move quests that were present previously but are missing from the latest server list
    // into the archivedCompletedQuests array if they are completed locally.
    const archiveMissingCompletedQuests = (oldQuests, newQuests) => {
        try {
            if (!Array.isArray(oldQuests) || !Array.isArray(newQuests)) return;
            const newIds = new Set(newQuests.map(q => (q && (q.id || q.title)) ? String(q.id || q.title) : '').filter(Boolean));
            // compute completion index from current state.progress and oldQuests
            const completionIndexBefore = (() => {
                const map = new Map();
                oldQuests.forEach(q => {
                    if (!q) return;
                    const key = q.id || q.title || '';
                    if (!key) return;
                    const partitions = partitionQuestObjectives(q);
                    const totalObjectives = Number(partitions.totalCount) || 0;
                    const completedObjectives = partitions.completed.length;
                    const activeObjectives = partitions.active.length;
                    const allCompleted = totalObjectives > 0
                        ? completedObjectives === totalObjectives
                        : activeObjectives === 0 && completedObjectives === 0;
                    map.set(String(key), !!allCompleted);
                });
                return map;
            })();

            const toArchive = [];
            oldQuests.forEach(q => {
                if (!q) return;
                const key = q.id || q.title || '';
                if (!key) return;
                if (newIds.has(String(key))) return; // still present
                if (!completionIndexBefore.get(String(key))) return; // not completed locally
                // ensure not already archived
                const already = (state.archivedCompletedQuests || []).some(a => (a.id || a.title) && String(a.id || a.title) === String(key));
                if (already) return;
                toArchive.push({
                    id: q.id,
                    title: q.title,
                    merchant: q.merchant,
                    merchant_original: q.merchant_original,
                    archivedAt: new Date().toISOString(),
                    objectives: q.objectives || []
                });
            });

            if (toArchive.length) {
                state.archivedCompletedQuests = (state.archivedCompletedQuests || []).concat(toArchive);
                persistArchivedCompletedQuests(state.archivedCompletedQuests);
            }
        } catch (e) {
            console.warn('Failed to archive missing completed quests', e);
        }
    };

    function toggleLoading(el, isLoading) {
        if (!el) {
            return;
        }
        el.style.display = isLoading ? 'flex' : 'none';
    }

    function renderError(container, message) {
        if (!container) {
            return;
        }
        container.innerHTML = `
            <div class="error-state">
                <span class="material-icons" aria-hidden="true">error_outline</span>
                <h3>Something went wrong</h3>
                <p>${message || 'Please try refreshing the data.'}</p>
            </div>
        `;
    }

    function renderEmpty(container, icon, title, message) {
        if (!container) {
            return;
        }
        container.innerHTML = `
            <div class="empty-state">
                <span class="material-icons" aria-hidden="true">${icon}</span>
                <h3>${title}</h3>
                <p>${message}</p>
            </div>
        `;
    }

    function rarityClass(rarity) {
        if (!rarity) {
            return 'rarity-common';
        }
        return `rarity-${rarity.toLowerCase()}`;
    }

    function createMetaChip(icon, label) {
        const span = document.createElement('span');
        span.innerHTML = `<span class="material-icons" aria-hidden="true">${icon}</span>${label}`;
        return span;
    }

    function createQuestCard(quest, options = {}) {
        const {
            viewMode = 'active',
            objectivesOverride,
            isLocked = false,
            lockedPrerequisites = [],
            index = 0
        } = options;
        const objectivesToRender = objectivesOverride || quest.objectives || [];

        const card = document.createElement('article');
        card.className = 'quest-card';
        card.style.animationDelay = `${index * 50}ms`;

        if (isLocked) {
            card.classList.add('quest-card--locked');
            const lockBanner = document.createElement('div');
            lockBanner.className = 'quest-card-lock';
            const lockIcon = document.createElement('span');
            lockIcon.className = 'material-icons';
            lockIcon.setAttribute('aria-hidden', 'true');
            lockIcon.textContent = 'lock';
            lockBanner.appendChild(lockIcon);
            const lockText = document.createElement('span');
            const requirements = (lockedPrerequisites || []).filter(Boolean);
            if (requirements.length) {
                lockText.textContent = `Requires ${requirements.join(', ')}`;
            } else {
                lockText.textContent = 'Prerequisites incomplete';
            }
            lockBanner.appendChild(lockText);
            card.appendChild(lockBanner);
        }

        const header = document.createElement('header');
        const title = document.createElement('h3');
        title.textContent = quest.title || quest.id;
        header.appendChild(title);

        if (quest.chapter) {
            const subtitle = document.createElement('div');
            subtitle.className = 'quest-meta';
            subtitle.appendChild(createMetaChip('book', quest.chapter));
            if (quest.prerequisite) {
                const prereqTitles = Array.isArray(quest.__resolvedPrerequisiteTitles) && quest.__resolvedPrerequisiteTitles.length
                    ? quest.__resolvedPrerequisiteTitles
                    : [quest.prerequisite];
                subtitle.appendChild(createMetaChip('flag', `Prerequisite: ${prereqTitles.join(', ')}`));
            }
            if (quest.dungeons && quest.dungeons.length) {
                subtitle.appendChild(createMetaChip('map', quest.dungeons.join(', ')));
            }
            header.appendChild(subtitle);
        }

        card.appendChild(header);

        if (quest.text) {
            const description = document.createElement('div');
            description.className = 'quest-description';
            description.textContent = quest.text;
            card.appendChild(description);
        }

        const objectivesContainer = document.createElement('div');
        objectivesContainer.innerHTML = '<h4>Objectives</h4>';
        const objectiveList = document.createElement('ul');
        objectiveList.className = 'objective-list';

        objectivesToRender.forEach((obj, index) => {
            const originalIndex = typeof obj.__originalIndex === 'number' ? obj.__originalIndex : index;
            const objectiveKey = makeObjectiveKey(quest, originalIndex, obj);
            const storedProgress = getObjectiveProgress(objectiveKey) || {};
            const objectiveCompleted = isObjectiveCompleted(quest, obj, originalIndex);

            const item = document.createElement('li');
            item.className = 'objective-item';

            const icon = document.createElement('div');
            icon.className = 'objective-icon';
            const iconName = ({
                Fetch: 'inventory_2',
                Kill: 'gavel',
                Props: 'build',
                Explore: 'travel_explore',
                Survive: 'health_and_safety'
            })[obj.type] || 'task';
            icon.innerHTML = `<span class="material-icons" aria-hidden="true">${iconName}</span>`;

            const content = document.createElement('div');
            content.className = 'objective-content';

            const headerRow = document.createElement('div');
            headerRow.className = 'objective-header';

            const titleDiv = document.createElement('div');
            titleDiv.className = 'objective-title';

            let titleText = '';
            if (obj.type === 'Fetch') {
                const targetName = obj.item && obj.item.name ? obj.item.name : (obj.item_id || 'Unknown Item');
                titleText = `Collect ${obj.count || 0}× ${targetName}`;
            } else if (obj.type === 'Kill') {
                titleText = `Eliminate ${obj.count || 0}× ${obj.monster || 'enemies'}`;
            } else if (obj.type === 'Props') {
                titleText = `Interact with ${obj.count || 0}× ${obj.interact || 'objects'}`;
            } else if (obj.type === 'Explore') {
                titleText = `Explore ${obj.module || 'the objective area'}`;
            } else if (obj.type === 'Survive') {
                titleText = `Survive ${obj.count || 0} waves`;
            } else {
                titleText = `${obj.type || 'Objective'} – ${obj.count || 0}`;
            }

            titleDiv.innerHTML = `<strong>${titleText}</strong>`;
            headerRow.appendChild(titleDiv);

            const toggleLabel = document.createElement('label');
            toggleLabel.className = 'objective-progress-toggle';
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = objectiveCompleted;
            const toggleText = document.createElement('span');
            toggleText.textContent = 'Completed';
            toggleLabel.appendChild(checkbox);
            toggleLabel.appendChild(toggleText);
            headerRow.appendChild(toggleLabel);

            content.appendChild(headerRow);

            if (obj.item && obj.item.rarity) {
                const rarity = document.createElement('span');
                rarity.className = `rarity-badge ${rarityClass(obj.item.rarity)}`;
                rarity.textContent = obj.item.rarity;
                content.appendChild(rarity);
            }

            if (obj.must_escape) {
                const meta = document.createElement('div');
                meta.className = 'objective-meta';
                meta.textContent = 'Must extract after completing objective';
                content.appendChild(meta);
            }

            item.appendChild(icon);
            item.appendChild(content);

            const progressContainer = document.createElement('div');
            progressContainer.className = 'objective-progress';

            const updateCompletionClass = () => {
                item.classList.toggle('completed', checkbox.checked);
            };
            updateCompletionClass();

            const persistObjective = () => {
                setObjectiveProgress(objectiveKey, {
                    quest_id: quest.id,
                    objective_index: originalIndex,
                    type: obj.type,
                    item_id: obj.item_id,
                    submitted: submittedValue,
                    completed: checkbox.checked
                });
            };

            const rerenderMerchantAndItems = (animationClass) => {
                const performRenders = () => {
                    scheduleGlobalRender();
                };

                if (animationClass && item && item.isConnected) {
                    let completed = false;
                    let timeoutId = null;
                    const finalize = () => {
                        if (completed) {
                            return;
                        }
                        completed = true;
                        if (timeoutId !== null) {
                            window.clearTimeout(timeoutId);
                        }
                        item.classList.remove('objective-item--transitioning', animationClass);
                        item.removeEventListener('animationend', finalize);
                        performRenders();
                    };

                    item.classList.add('objective-item--transitioning', animationClass);
                    item.addEventListener('animationend', finalize, { once: true });
                    timeoutId = window.setTimeout(finalize, 260);
                } else {
                    performRenders();
                }
            };

            let fetchInput = null;
            let updateRemainingLabel = null;

            let submittedValue = obj.type === 'Fetch' && obj.count ? clampNumber(storedProgress.submitted ?? 0, 0, obj.count) : 0;

            if (obj.type === 'Fetch' && obj.count) {
                const countWrapper = document.createElement('div');
                countWrapper.className = 'objective-progress-count';

                const countLabel = document.createElement('span');
                countLabel.className = 'objective-progress-label';
                countLabel.textContent = 'Turned in';
                countWrapper.appendChild(countLabel);

                fetchInput = document.createElement('input');
                fetchInput.type = 'number';
                fetchInput.min = '0';
                fetchInput.step = '1';
                fetchInput.max = String(obj.count);
                fetchInput.value = submittedValue;
                countWrapper.appendChild(fetchInput);

                const remainingLabel = document.createElement('span');
                remainingLabel.className = 'objective-remaining';
                updateRemainingLabel = () => {
                    const remaining = Math.max(0, (obj.count || 0) - submittedValue);
                    remainingLabel.textContent = `${remaining} remaining`;
                };
                updateRemainingLabel();
                countWrapper.appendChild(remainingLabel);

                const handleFetchInput = (rawValue) => {
                    if (rawValue === '') {
                        submittedValue = 0;
                        fetchInput.value = '';
                        checkbox.checked = false;
                        delete checkbox.dataset.autoComplete;
                    } else {
                        const clamped = clampNumber(rawValue, 0, obj.count || 0);
                        if (!Number.isFinite(clamped)) {
                            return;
                        }
                        submittedValue = clamped;
                        fetchInput.value = clamped;
                        const shouldComplete = obj.count && clamped >= obj.count;
                        if (shouldComplete) {
                            checkbox.checked = true;
                            checkbox.dataset.autoComplete = 'true';
                        } else if (checkbox.dataset.autoComplete) {
                            checkbox.checked = false;
                            delete checkbox.dataset.autoComplete;
                        }
                    }

                    if (updateRemainingLabel) {
                        updateRemainingLabel();
                    }
                    updateCompletionClass();
                    persistObjective();

                    const animationClass = checkbox.checked && state.merchantViewMode === 'active'
                        ? 'objective-item--completing'
                        : (!checkbox.checked && state.merchantViewMode === 'completed'
                            ? 'objective-item--reopening'
                            : null);

                    rerenderMerchantAndItems(animationClass);
                };

                fetchInput.addEventListener('change', (event) => handleFetchInput(event.target.value));
                fetchInput.addEventListener('input', (event) => handleFetchInput(event.target.value));

                progressContainer.appendChild(countWrapper);
                content.appendChild(progressContainer);
            }

            checkbox.addEventListener('change', () => {
                delete checkbox.dataset.autoComplete;
                if (checkbox.checked && obj.type === 'Fetch' && obj.count && submittedValue < obj.count) {
                    submittedValue = obj.count;
                    if (fetchInput) {
                        fetchInput.value = submittedValue;
                    }
                }
                if (!checkbox.checked && obj.type === 'Fetch' && obj.count && submittedValue >= obj.count) {
                    submittedValue = Math.max(0, obj.count - 1);
                    if (fetchInput) {
                        fetchInput.value = submittedValue > 0 ? submittedValue : '';
                    }
                }
                if (updateRemainingLabel) {
                    updateRemainingLabel();
                }
                updateCompletionClass();
                persistObjective();

                const animationClass = checkbox.checked && state.merchantViewMode === 'active'
                    ? 'objective-item--completing'
                    : (!checkbox.checked && state.merchantViewMode === 'completed'
                        ? 'objective-item--reopening'
                        : null);

                rerenderMerchantAndItems(animationClass);
            });

            item.appendChild(progressContainer);
            objectiveList.appendChild(item);
        });

        if (!objectiveList.children.length) {
            const emptyObjective = document.createElement('li');
            emptyObjective.className = 'objective-item';
            emptyObjective.innerHTML = '<div class="objective-content">No specific objectives for this view.</div>';
            objectiveList.appendChild(emptyObjective);
        }

        objectivesContainer.appendChild(objectiveList);
        card.appendChild(objectivesContainer);

        if (quest.rewards && quest.rewards.length) {
            const rewardSection = document.createElement('div');
            rewardSection.innerHTML = '<h4>Rewards</h4>';
            const chips = document.createElement('div');
            chips.className = 'reward-chips';

            quest.rewards.forEach(reward => {
                const chip = document.createElement('span');
                chip.className = 'reward-chip';
                let iconName = 'card_giftcard';
                if (reward.type === 'Experience') iconName = 'military_tech';
                if (reward.type === 'Affinity') iconName = 'favorite';
                if (reward.type === 'Item') iconName = 'inventory';
                if (reward.type === 'Random') iconName = 'auto_awesome';

                const parts = [];
                parts.push(`<span class="material-icons" aria-hidden="true">${iconName}</span>`);
                if (reward.type === 'Item' && reward.item) {
                    parts.push(`<strong>${reward.count || 1}×</strong> ${reward.item.name}`);
                    if (reward.item.rarity) {
                        parts.push(`<span class="rarity-badge ${rarityClass(reward.item.rarity)}">${reward.item.rarity}</span>`);
                    }
                } else if (reward.type === 'Random') {
                    const rarity = reward.rarity ? `<span class="rarity-badge ${rarityClass(reward.rarity)}">${reward.rarity}</span>` : '';
                    parts.push(`${reward.count || 1}× Random ${reward.item_type || 'Reward'} ${rarity}`);
                } else if (reward.type === 'Affinity') {
                    parts.push(`${reward.count || 0} Affinity${reward.merchant ? ` (${reward.merchant})` : ''}`);
                } else if (reward.type === 'Experience') {
                    parts.push(`${reward.count || 0} Experience`);
                } else {
                    parts.push(`${reward.count || 0} ${reward.type}`);
                }

                chip.innerHTML = parts.join(' ');
                chips.appendChild(chip);
            });

            rewardSection.appendChild(chips);
            card.appendChild(rewardSection);
        }

        return card;
    }

    function updateMerchantStats(summary) {
        if (!elements.merchantStats) {
            return;
        }

        let prepared = summary;
        if (Array.isArray(prepared)) {
            prepared = buildMerchantView(prepared, state.merchantViewMode || 'active').summary;
        }
        if (!prepared) {
            prepared = {
                viewMode: state.merchantViewMode || 'active',
                questsCount: 0,
                objectiveCount: 0,
                itemObjectiveCount: 0,
                totalFiltered: 0,
                totalActive: 0,
                totalCompleted: 0
            };
        }

        const viewMode = prepared.viewMode || state.merchantViewMode || 'active';
        const questsCount = Number(prepared.questsCount) || 0;
        const objectiveCount = Number(prepared.objectiveCount) || 0;
        const itemObjectives = Number(prepared.itemObjectiveCount) || 0;
        const totalFiltered = Number(prepared.totalFiltered) || 0;
        const totalObjectives = Number(prepared.totalActive || 0) + Number(prepared.totalCompleted || 0);
        const lockedHidden = Number(prepared.lockedHidden) || 0;
        const lockedVisible = Number(prepared.lockedVisible) || 0;

        if ((!objectiveCount || !questsCount) && state.hideLockedQuests && lockedHidden > 0) {
            const noun = lockedHidden === 1 ? 'quest' : 'quests';
            elements.merchantStats.innerHTML = `Filtering out <strong>${lockedHidden}</strong> prerequisite-locked ${noun}.`;
            return;
        }

        if (!objectiveCount || !questsCount) {
            if (!totalFiltered) {
                elements.merchantStats.textContent = 'No quests available for this selection.';
            } else if (!totalObjectives) {
                elements.merchantStats.textContent = 'No objectives recorded for these quests yet.';
            } else if (viewMode === 'completed') {
                elements.merchantStats.textContent = 'No completed objectives recorded yet.';
            } else {
                elements.merchantStats.textContent = 'All objectives completed for this selection.';
            }
            return;
        }

        const label = viewMode === 'completed' ? 'completed' : 'active';
        const hiddenNote = state.hideLockedQuests && lockedHidden > 0
            ? ` <span class="quest-stats-note">Hiding ${lockedHidden} locked ${lockedHidden === 1 ? 'quest' : 'quests'}</span>`
            : (lockedVisible > 0
                ? ` <span class="quest-stats-note">${lockedVisible} locked ${lockedVisible === 1 ? 'quest needs' : 'quests need'} prerequisites</span>`
                : '');

        elements.merchantStats.innerHTML = `
            <strong>${questsCount}</strong> quests •
            <strong>${objectiveCount}</strong> ${label} objectives •
            <strong>${itemObjectives}</strong> item turn-ins${hiddenNote ? hiddenNote : ''}
        `;
    }

    function renderMerchantOptions() {
        if (!elements.merchantSelect) {
            return;
        }
        // Disable select until we populate it to avoid flicker while capture data settles
        elements.merchantSelect.disabled = true;
        const previousSelection = state.selectedMerchant;
        const fragment = document.createDocumentFragment();

        state.merchants.forEach(merchant => {
            const option = document.createElement('option');
            option.value = merchant;
            option.textContent = merchant;
            fragment.appendChild(option);
        });

        elements.merchantSelect.innerHTML = '';
        elements.merchantSelect.appendChild(fragment);

        if (state.merchants.length) {
            // enable select when we have merchants
            elements.merchantSelect.disabled = false;
            if (!state.selectedMerchant || !state.merchants.includes(previousSelection)) {
                state.selectedMerchant = state.merchants[0];
            }
            elements.merchantSelect.value = state.selectedMerchant;
        } else {
            // leave disabled and insert a placeholder option so UI indicates no merchants
            const placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = 'No merchants available';
            elements.merchantSelect.appendChild(placeholder);
        }
    }

    function getFilteredQuests() {
        if (!state.selectedMerchant) {
            return state.quests;
        }
        return state.quests.filter(quest => quest.merchant === state.selectedMerchant);
    }

    function renderMerchantView() {
        if (!state.questsLoaded) {
            return;
        }

        const filteredQuests = getFilteredQuests();
        const viewMode = state.merchantViewMode || 'active';
        const { questsForView, summary } = buildMerchantView(filteredQuests, viewMode);

        updateMerchantViewToggle();

        if (!filteredQuests.length) {
            renderEmpty(elements.questList, 'inventory', 'No quests yet', 'Try refreshing or selecting another merchant.');
            updateMerchantStats(summary);
            return;
        }

        if (!questsForView.length) {
            if (state.hideLockedQuests && Number(summary.lockedHidden) > 0) {
                renderEmpty(
                    elements.questList,
                    'lock',
                    'Prerequisites not met',
                    'Complete earlier quests or disable the prerequisite filter to view them.'
                );
                updateMerchantStats(summary);
                return;
            }
            const isCompletedView = viewMode === 'completed';
            renderEmpty(
                elements.questList,
                isCompletedView ? 'sentiment_satisfied' : 'celebration',
                isCompletedView ? 'Nothing completed yet' : 'All caught up!',
                isCompletedView
                    ? 'Complete objectives to see them here.'
                    : 'All objectives are completed for this merchant.'
            );
            updateMerchantStats(summary);
            return;
        }

        const fragment = document.createDocumentFragment();
        questsForView.forEach(({ quest, objectives, isLocked, lockedPrerequisites }, index) => {
            fragment.appendChild(createQuestCard(quest, {
                viewMode,
                objectivesOverride: objectives,
                isLocked,
                lockedPrerequisites,
                index
            }));
        });
        elements.questList.innerHTML = '';
        elements.questList.appendChild(fragment);
        updateMerchantStats(summary);
    }

    const formatCharacterMeta = (entry) => {
        const parts = [];
        if (entry && entry.character_class) {
            parts.push(entry.character_class);
        }
        const level = Number(entry && entry.character_level);
        if (Number.isFinite(level) && level > 0) {
            parts.push(`Level ${level}`);
        }
        if (!parts.length && entry && entry.last_update) {
            parts.push('Recently captured');
        }
        return parts.length ? parts.join(' • ') : 'Captured stash';
    };

    let holdingsPositionFrame = null;
    let holdingsResizeAttached = false;

    const positionItemHoldingsDialog = () => {
        const dialog = elements.itemHoldingsDialog;
        if (!dialog || !elements.itemHoldingsModal || elements.itemHoldingsModal.classList.contains('hidden')) {
            return;
        }

        const viewportWidth = (typeof window !== 'undefined' && window.innerWidth) || (document.documentElement && document.documentElement.clientWidth) || 0;
        const viewportHeight = (typeof window !== 'undefined' && window.innerHeight) || (document.documentElement && document.documentElement.clientHeight) || 0;
        if (!viewportWidth || !viewportHeight) {
            return;
        }

        const margin = 20;
        let anchorRect = state.activeHoldingsAnchor;

        if (anchorRect && anchorRect.element && typeof anchorRect.element.getBoundingClientRect === 'function') {
            const rect = anchorRect.element.getBoundingClientRect();
            if (rect && Number.isFinite(rect.top)) {
                anchorRect = {
                    element: anchorRect.element,
                    top: rect.top,
                    bottom: rect.bottom,
                    left: rect.left,
                    right: rect.right,
                    width: rect.width,
                    height: rect.height
                };
            }
        }

        if (anchorRect && (!Number.isFinite(anchorRect.top) || !Number.isFinite(anchorRect.bottom))) {
            anchorRect = null;
        }

        if (anchorRect && anchorRect.element && typeof document !== 'undefined' && document.body && !document.body.contains(anchorRect.element)) {
            anchorRect = null;
        }

        dialog.style.top = 'auto';
        dialog.style.left = 'auto';
        dialog.classList.remove('positioned');

        const dialogRect = dialog.getBoundingClientRect();
        const dialogWidth = dialogRect.width || dialog.offsetWidth || 0;
        const dialogHeight = dialogRect.height || dialog.offsetHeight || 0;

        // Horizontal: Always center
        let left = (viewportWidth - dialogWidth) / 2;

        // Vertical: Start with center
        let top = (viewportHeight - dialogHeight) / 2;

        if (anchorRect) {
            // Check for vertical overlap
            // Overlap if dialog top < anchor bottom AND dialog bottom > anchor top
            const dialogBottom = top + dialogHeight;
            const overlaps = top < anchorRect.bottom && dialogBottom > anchorRect.top;

            if (overlaps) {
                const itemCenterY = anchorRect.top + (anchorRect.height / 2);
                const isTopHalf = itemCenterY < (viewportHeight / 2);

                if (isTopHalf) {
                    // Item is in top half, push dialog below
                    top = anchorRect.bottom + margin;
                } else {
                    // Item is in bottom half, push dialog above
                    top = anchorRect.top - dialogHeight - margin;
                }
            }
        }

        if (left < margin) {
            left = margin;
        }
        if (left + dialogWidth > viewportWidth - margin) {
            left = Math.max(margin, viewportWidth - dialogWidth - margin);
        }

        if (top < margin) {
            top = margin;
        }
        if (top + dialogHeight > viewportHeight - margin) {
            top = Math.max(margin, viewportHeight - dialogHeight - margin);
        }

        state.activeHoldingsAnchor = anchorRect;

        dialog.style.top = `${Math.round(top)}px`;
        dialog.style.left = `${Math.round(left)}px`;
        dialog.classList.add('positioned');
    };

    function scheduleHoldingsPositionUpdate() {
        if (typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
            positionItemHoldingsDialog();
            return;
        }
        if (holdingsPositionFrame !== null) {
            window.cancelAnimationFrame(holdingsPositionFrame);
        }
        holdingsPositionFrame = window.requestAnimationFrame(() => {
            holdingsPositionFrame = null;
            positionItemHoldingsDialog();
        });
    }

    const attachHoldingsPositionListeners = () => {
        if (holdingsResizeAttached || typeof window === 'undefined') {
            return;
        }
        window.addEventListener('resize', scheduleHoldingsPositionUpdate);
        window.addEventListener('scroll', scheduleHoldingsPositionUpdate, true);
        holdingsResizeAttached = true;
    };

    const detachHoldingsPositionListeners = () => {
        if (!holdingsResizeAttached || typeof window === 'undefined') {
            if (typeof window !== 'undefined' && typeof window.cancelAnimationFrame === 'function' && holdingsPositionFrame !== null) {
                window.cancelAnimationFrame(holdingsPositionFrame);
                holdingsPositionFrame = null;
            }
            return;
        }
        window.removeEventListener('resize', scheduleHoldingsPositionUpdate);
        window.removeEventListener('scroll', scheduleHoldingsPositionUpdate, true);
        holdingsResizeAttached = false;
        if (typeof window !== 'undefined' && typeof window.cancelAnimationFrame === 'function' && holdingsPositionFrame !== null) {
            window.cancelAnimationFrame(holdingsPositionFrame);
            holdingsPositionFrame = null;
        }
    };

    const formatAnchorFromElement = (element) => {
        if (!element || typeof element.getBoundingClientRect !== 'function') {
            return null;
        }
        const rect = element.getBoundingClientRect();
        return {
            element,
            top: rect.top,
            bottom: rect.bottom,
            left: rect.left,
            right: rect.right,
            width: rect.width,
            height: rect.height
        };
    };

    function lockBodyScroll() {
        if (!document || !document.body) {
            return;
        }

        if (!state.bodyScrollLock) {
            const existingOverflow = document.body.style.overflow;
            const existingPaddingRight = document.body.style.paddingRight;

            if (typeof window !== 'undefined') {
                const viewportWidth = document.documentElement ? document.documentElement.clientWidth : 0;
                const fullWidth = window.innerWidth || viewportWidth;
                const scrollbarWidth = fullWidth - viewportWidth;
                if (viewportWidth && scrollbarWidth > 0) {
                    const numericPadding = parseFloat(existingPaddingRight || '0') || 0;
                    document.body.style.paddingRight = `${numericPadding + scrollbarWidth}px`;
                }
            }

            state.bodyScrollLock = {
                overflow: existingOverflow,
                paddingRight: existingPaddingRight
            };
        }

        document.body.classList.add('modal-open');
        document.body.style.overflow = 'hidden';
    }

    function unlockBodyScroll() {
        if (!document || !document.body) {
            return;
        }

        document.body.classList.remove('modal-open');

        if (!state.bodyScrollLock) {
            document.body.style.overflow = '';
            document.body.style.paddingRight = '';
            detachHoldingsPositionListeners();
            return;
        }

        const { overflow, paddingRight } = state.bodyScrollLock;
        document.body.style.overflow = overflow || '';
        document.body.style.paddingRight = paddingRight || '';
        state.bodyScrollLock = null;
        detachHoldingsPositionListeners();
    }

    function hideItemHoldingsModal() {
        state.activeHoldingsItemId = null;
        state.activeHoldingsAnchor = null;
        if (holdingsPositionFrame !== null && typeof window !== 'undefined') {
            window.cancelAnimationFrame(holdingsPositionFrame);
            holdingsPositionFrame = null;
        }
        if (elements.itemHoldingsModal) {
            elements.itemHoldingsModal.classList.add('hidden');
        }
        if (elements.itemHoldingsOverlay) {
            elements.itemHoldingsOverlay.classList.add('hidden');
        }
        if (elements.itemHoldingsBody) {
            elements.itemHoldingsBody.innerHTML = '';
        }
        if (elements.itemHoldingsSummary) {
            elements.itemHoldingsSummary.textContent = '';
        }
        if (elements.itemHoldingsDialog) {
            elements.itemHoldingsDialog.style.top = '';
            elements.itemHoldingsDialog.style.left = '';
            elements.itemHoldingsDialog.classList.remove('positioned');
        }
        unlockBodyScroll();
    }

    const navigateToCharacter = (characterId, stashEntries) => {
        hideItemHoldingsModal();
        if (!characterId) {
            return;
        }
        const encodedCharacter = encodeURIComponent(characterId);

        const stashList = Array.isArray(stashEntries) ? stashEntries.filter(Boolean) : [];

        const normalizeSlotId = (slotValue) => {
            if (typeof slotValue === 'number' && Number.isFinite(slotValue)) {
                return slotValue;
            }
            if (typeof slotValue === 'string' && slotValue.trim() !== '') {
                const parsed = Number.parseInt(slotValue, 10);
                if (Number.isFinite(parsed)) {
                    return parsed;
                }
            }
            return null;
        };

        let primaryStashId = null;
        let primaryHasSlots = false;

        stashList.forEach(entry => {
            if (!entry) {
                return;
            }
            const stashIdRaw = entry.stash_id;
            if (stashIdRaw === undefined || stashIdRaw === null || stashIdRaw === '') {
                return;
            }
            const stashIdStr = String(stashIdRaw);
            const slotForEntry = normalizeSlotId(entry.slot_id);
            if (!primaryStashId) {
                primaryStashId = stashIdStr;
                primaryHasSlots = slotForEntry !== null;
                return;
            }
            if (!primaryHasSlots && slotForEntry !== null) {
                primaryStashId = stashIdStr;
                primaryHasSlots = true;
            }
        });

        const slotCandidates = [];
        if (primaryStashId) {
            stashList.forEach(entry => {
                if (!entry) {
                    return;
                }
                const stashIdRaw = entry.stash_id;
                if (stashIdRaw === undefined || stashIdRaw === null || stashIdRaw === '') {
                    return;
                }
                if (String(stashIdRaw) !== primaryStashId) {
                    return;
                }
                const normalizedSlot = normalizeSlotId(entry.slot_id);
                if (normalizedSlot !== null && normalizedSlot >= 0) {
                    slotCandidates.push(normalizedSlot);
                }
            });
        }

        const uniqueSlotIds = Array.from(new Set(slotCandidates)).sort((a, b) => a - b);

        const queryParams = new URLSearchParams();
        if (primaryStashId) {
            queryParams.set('stashId', primaryStashId);
        }
        if (uniqueSlotIds.length) {
            queryParams.set('slotIds', uniqueSlotIds.join(','));
        }

        const baseUrl = `/character/${encodedCharacter}`;
        const queryString = queryParams.toString();
        const targetUrl = queryString ? `${baseUrl}?${queryString}` : baseUrl;

        const go = () => {
            if (typeof window.navigateWithTransition === 'function') {
                window.navigateWithTransition(targetUrl);
            } else {
                window.location.href = targetUrl;
            }
        };

        if (primaryStashId) {
            fetch(`/api/character/${encodedCharacter}/current-stash/${encodeURIComponent(primaryStashId)}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            })
                .catch(err => console.warn('Failed to pre-set stash for navigation:', err))
                .finally(go);
        } else {
            go();
        }
    };

    const renderItemHoldingsModal = (item, summary, allowedLootStates = null) => {
        if (!elements.itemHoldingsBody || !elements.itemHoldingsSummary) {
            return;
        }
        const itemId = item && item.item_id ? String(item.item_id) : '';
        const itemName = item && (item.name || item.title) ? (item.name || item.title) : itemId;

        if (elements.itemHoldingsTitle && itemName) {
            elements.itemHoldingsTitle.textContent = `${itemName} Holdings`;
        }

        // Default to Looted (2) if no filter provided, to match character view behavior
        const effectiveAllowedLootStates = (allowedLootStates && allowedLootStates.length > 0)
            ? allowedLootStates
            : [2];

        const allowedSet = new Set(effectiveAllowedLootStates.map(Number));

        let totalValue = 0;
        let characters = [];

        if (summary) {
            const rawCharacters = Array.isArray(summary.characters) ? summary.characters : [];

            rawCharacters.forEach(char => {
                let charTotal = 0;
                const stashes = Array.isArray(char.stashes) ? char.stashes : [];
                const filteredStashes = [];

                stashes.forEach(stash => {
                    const count = Number(stash.count) || 0;
                    if (count <= 0) return;

                    const lootState = stash.loot_state !== undefined ? Number(stash.loot_state) : null;
                    if (allowedSet.has(lootState)) {
                        charTotal += count;
                        filteredStashes.push(stash);
                    }
                });

                if (charTotal > 0) {
                    totalValue += charTotal;
                    characters.push({
                        ...char,
                        total: charTotal,
                        stashes: filteredStashes
                    });
                }
            });
        }

        elements.itemHoldingsSummary.innerHTML = `Total owned across captured characters: <strong>${totalValue}</strong>`;
        updateOwnedLabels(itemId);

        if (!characters.length) {
            elements.itemHoldingsBody.innerHTML = '<div class="item-holdings-empty">No captured characters currently hold this item.</div>';
            scheduleHoldingsPositionUpdate();
            return;
        }

        const fragment = document.createDocumentFragment();
        characters.forEach((entry) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'item-holdings-entry';
            button.dataset.characterId = entry.character_id || '';
            button.innerHTML = `
                <div class="entry-meta">
                    <strong>${entry.character_name || 'Unknown Character'}</strong>
                    <span>${formatCharacterMeta(entry)}</span>
                </div>
                <div class="entry-count">${Number(entry.total) || 0}</div>
            `;
            button.addEventListener('click', () => {
                const stashEntries = Array.isArray(entry.stashes) ? entry.stashes : [];
                navigateToCharacter(entry.character_id, stashEntries);
            });
            fragment.appendChild(button);
        });

        elements.itemHoldingsBody.innerHTML = '';
        elements.itemHoldingsBody.appendChild(fragment);
        scheduleHoldingsPositionUpdate();
    };

    const fetchItemHoldings = async (itemId) => {
        const normalizedId = (itemId || '').toString().trim();
        if (!normalizedId) {
            return { total: 0, characters: [] };
        }

        const now = Date.now();
        const cached = state.itemHoldingsCache[normalizedId];
        if (cached && (now - cached.timestamp) < HOLDINGS_CACHE_TTL) {
            updateOwnedLabels(normalizedId);
            return cached.data;
        }

        let response;
        try {
            response = await fetch(`/api/quests/items/holdings?ids=${encodeURIComponent(normalizedId)}`, {
                cache: 'no-store'
            });
        } catch (error) {
            throw new Error('Unable to contact holdings service. Ensure character data is available.');
        }

        let payload;
        try {
            payload = await response.json();
        } catch (error) {
            throw new Error('Received an invalid response when loading holdings.');
        }

        if (!response.ok || payload.success === false) {
            throw new Error((payload && payload.error) || `Failed to load holdings (status ${response.status})`);
        }

        const summary = (payload.items && payload.items[normalizedId]) || { total: 0, characters: [] };
        state.itemHoldingsCache[normalizedId] = {
            data: summary,
            timestamp: now
        };
        updateOwnedLabels(normalizedId);
        return summary;
    };

    const openItemHoldingsModal = (item, triggerElement, allowedLootStates = null) => {
        if (!elements.itemHoldingsModal || !elements.itemHoldingsOverlay) {
            return;
        }

        const itemId = item && item.item_id ? String(item.item_id) : '';
        if (!itemId) {
            if (typeof showNotification === 'function') {
                showNotification('This quest item does not have a valid identifier yet.', 'warning');
            }
            return;
        }

        state.activeHoldingsAnchor = formatAnchorFromElement(triggerElement) || (triggerElement ? { element: triggerElement } : null);
        state.activeHoldingsItemId = itemId;
        elements.itemHoldingsOverlay.classList.remove('hidden');
        elements.itemHoldingsModal.classList.remove('hidden');
        lockBodyScroll();
        attachHoldingsPositionListeners();
        positionItemHoldingsDialog();

        const itemName = item && (item.name || item.title) ? (item.name || item.title) : itemId;
        if (elements.itemHoldingsTitle) {
            elements.itemHoldingsTitle.textContent = `${itemName} Holdings`;
        }
        if (elements.itemHoldingsSummary) {
            elements.itemHoldingsSummary.innerHTML = '<span>Gathering stash holdings...</span>';
        }
        if (elements.itemHoldingsBody) {
            elements.itemHoldingsBody.innerHTML = '<div class="item-holdings-loading"><div class="spinner" aria-hidden="true"></div><span>Loading holdings...</span></div>';
        }
        scheduleHoldingsPositionUpdate();

        fetchItemHoldings(itemId)
            .then((summary) => {
                if (state.activeHoldingsItemId !== itemId) {
                    return;
                }
                renderItemHoldingsModal({ item_id: itemId, name: itemName }, summary, allowedLootStates);
            })
            .catch((error) => {
                console.error('Failed to load item holdings', error);
                if (state.activeHoldingsItemId !== itemId) {
                    return;
                }
                if (elements.itemHoldingsSummary) {
                    elements.itemHoldingsSummary.textContent = '';
                }
                if (elements.itemHoldingsBody) {
                    const message = error && error.message ? error.message : 'Failed to load holdings.';
                    elements.itemHoldingsBody.innerHTML = `<div class="item-holdings-error">${message}</div>`;
                }
                scheduleHoldingsPositionUpdate();
            });
    };

    function renderItemsList() {
        if (!state.itemsLoaded) {
            return;
        }

        ensureItemsLoaderAttached();
        const container = elements.itemsList;
        if (!container) {
            return;
        }
        const loader = elements.itemsLoading;
        if (loader) {
            loader.style.display = 'none';
        }

        const hideLocked = Boolean(state.hideLockedQuests);
        recomputeQuestLockState();

        const searchTerm = state.itemSearch.trim().toLowerCase();
        const filteredBySearch = state.aggregatedItems.filter(item => {
            if (!searchTerm) {
                return true;
            }
            const merchantMatch = (item.merchants || []).some(entry => (entry.name || '').toLowerCase().includes(searchTerm));
            return (item.name || '').toLowerCase().includes(searchTerm) || merchantMatch;
        });

        const resolveQuestKeyFromEntry = (entry) => {
            if (!entry) {
                return '';
            }
            const candidates = [
                entry.id,
                entry.quest_id,
                entry.questId,
                entry.questID,
                entry.quest,
                entry.quest_name,
                entry.questName,
                entry.title,
                entry.name
            ];
            for (const candidate of candidates) {
                if (candidate !== undefined && candidate !== null && candidate !== '') {
                    return String(candidate);
                }
            }
            return '';
        };

        const summarizeItemForView = (item) => {
            const questEntries = Array.isArray(item.quests) ? item.quests : [];
            const visibleQuests = [];
            const hiddenTitles = [];
            const allQuestIds = [];
            let lockedQuestCount = 0;
            let totalFromQuests = 0;
            let visibleTotalFromQuests = 0;

            const allowedLootStates = new Set();
            const pushAllowedLootState = (value) => {
                const numeric = Number(value);
                if (Number.isFinite(numeric)) {
                    allowedLootStates.add(numeric);
                }
            };
            const DEFAULT_LOOT_STATE = 2;

            questEntries.forEach((entry) => {
                const questKey = resolveQuestKeyFromEntry(entry);
                if (questKey) {
                    allQuestIds.push(questKey);
                }
                const locked = questKey ? questLockIndex.get(questKey) === true : false;
                const countValue = Number(entry.count ?? entry.total ?? entry.quantity ?? entry.requirement ?? entry.amount) || 0;
                totalFromQuests += countValue;
                const questTitle = entry.title || entry.quest_title || entry.questTitle || entry.name || entry.id || questKey;

                const lootStateValues = entry.loot_state_values;
                if (Array.isArray(lootStateValues) && lootStateValues.length > 0) {
                    lootStateValues.forEach(value => pushAllowedLootState(value));
                } else {
                    pushAllowedLootState(DEFAULT_LOOT_STATE);
                }

                if (locked) {
                    lockedQuestCount += 1;
                    if (questTitle) {
                        hiddenTitles.push(questTitle);
                    }
                } else {
                    visibleTotalFromQuests += countValue;
                }

                const decoratedEntry = {
                    ...entry,
                    questKey,
                    questTitle,
                    count: countValue,
                    __isLocked: locked
                };

                if (!hideLocked || !locked) {
                    visibleQuests.push(decoratedEntry);
                }
            });

            const baseTotal = Number(item.total_required);
            const fallbackTotal = Number.isFinite(baseTotal) && baseTotal >= 0 ? baseTotal : totalFromQuests;
            let effectiveTotal = fallbackTotal;

            if (hideLocked) {
                if (visibleQuests.length) {
                    effectiveTotal = visibleTotalFromQuests || fallbackTotal;
                } else if (questEntries.length) {
                    effectiveTotal = 0;
                }
            }

            const allowedQuestIds = hideLocked
                ? visibleQuests.map(entry => entry.questKey).filter(Boolean)
                : allQuestIds.filter(Boolean);

            const finalAllowedLootStates = allowedLootStates.size > 0
                ? Array.from(allowedLootStates).sort((a, b) => a - b)
                : null;

            return {
                totalRequired: Math.max(0, Number.isFinite(effectiveTotal) ? effectiveTotal : 0),
                visibleQuests,
                hiddenQuestCount: hideLocked ? lockedQuestCount : 0,
                hiddenQuestTitles: Array.from(new Set(hiddenTitles.filter(Boolean))),
                lockedQuestCount,
                hasQuestAssociations: questEntries.length > 0,
                isFullyLocked: hideLocked && questEntries.length > 0 && visibleQuests.length === 0 && lockedQuestCount > 0,
                allowedQuestIds: allowedQuestIds.length ? allowedQuestIds : null,
                allowedLootStates: finalAllowedLootStates
            };
        };

        let hiddenByPrerequisite = 0;
        const processedItems = [];

        filteredBySearch.forEach((item) => {
            const summary = summarizeItemForView(item);
            if (hideLocked && summary.isFullyLocked) {
                hiddenByPrerequisite += 1;
                return;
            }
            processedItems.push({ item, summary });
        });

        const totalNeeded = processedItems.reduce((total, entry) => total + (entry.summary.totalRequired || 0), 0);
        if (elements.itemsMeta) {
            let metaText = `Showing <strong>${processedItems.length}</strong> of ${state.aggregatedItems.length} items`;
            metaText += ` • Total required: <strong>${totalNeeded}</strong>`;
            if (hideLocked && hiddenByPrerequisite > 0) {
                metaText += ` • Hidden by prerequisites: <strong>${hiddenByPrerequisite}</strong>`;
            }
            elements.itemsMeta.innerHTML = metaText;
        }

        if (!processedItems.length) {
            if (hideLocked && hiddenByPrerequisite > 0) {
                renderEmpty(
                    container,
                    'lock',
                    'Prerequisites not met',
                    'Complete earlier quests or disable the prerequisite filter to view their required items.'
                );
            } else {
                renderEmpty(container, 'checklist_rtl', 'No matching items', 'Try a different search term or refresh the data.');
            }
            ensureItemsLoaderAttached();
            if (loader) {
                loader.style.display = 'none';
            }
            return;
        }

        const itemsWithIndex = processedItems.map(({ item, summary }, index) => ({ item, summary, index }));
        if (state.itemsOwnedFirst) {
            itemsWithIndex.sort((a, b) => {
                const getEffectiveOwned = (entry) => {
                    const itemId = entry.item.item_id || entry.item.itemId || '';
                    let allowedSet = null;
                    if (entry.summary.allowedLootStates && entry.summary.allowedLootStates.length > 0) {
                        allowedSet = new Set(entry.summary.allowedLootStates);
                    } else {
                        allowedSet = new Set([2]);
                    }
                    const total = getCachedHoldingsTotal(itemId, allowedSet);
                    return total !== null ? total : 0;
                };

                const ownedDiff = getEffectiveOwned(b) - getEffectiveOwned(a);
                if (ownedDiff !== 0) {
                    return ownedDiff;
                }
                return a.index - b.index;
            });
        }

        const fragment = document.createDocumentFragment();
        const visibleItemIds = [];

        itemsWithIndex.forEach(({ item, summary }, displayIndex) => {
            const itemIdentifier = item.item_id || item.itemId || '';
            const normalizedItem = { ...item, item_id: itemIdentifier };
            const row = document.createElement('div');
            row.className = 'quest-item';
            row.style.animationDelay = `${displayIndex * 30}ms`;

            const totalRequired = Number(summary.totalRequired) || 0;
            const maxValue = totalRequired > 0 ? totalRequired : Number.MAX_SAFE_INTEGER;
            const allowedQuestIds = summary.allowedQuestIds;

            const main = document.createElement('div');
            main.className = 'item-main';

            const iconWrapper = document.createElement('div');
            iconWrapper.className = 'item-icon';
            if (item.icon) {
                const img = document.createElement('img');
                img.src = item.icon;
                img.alt = `${item.name} icon`;
                iconWrapper.appendChild(img);
            } else {
                iconWrapper.innerHTML = '<span class="material-icons" aria-hidden="true">inventory_2</span>';
            }

            const info = document.createElement('div');
            info.className = 'item-info';
            const name = document.createElement('div');
            name.className = 'item-name';
            name.textContent = item.name || item.item_id;
            info.appendChild(name);

            const meta = document.createElement('div');
            meta.className = 'item-meta';
            if (item.rarity) {
                const rarity = document.createElement('span');
                rarity.className = `rarity-badge ${rarityClass(item.rarity)}`;
                rarity.textContent = item.rarity;
                meta.appendChild(rarity);
            }
            if (item.type) {
                const type = document.createElement('span');
                type.textContent = item.type;
                meta.appendChild(type);
            }
            info.appendChild(meta);

            main.appendChild(iconWrapper);
            main.appendChild(info);

            const required = document.createElement('div');
            required.className = 'item-required';
            const totalLabel = document.createElement('div');
            totalLabel.className = 'item-total';
            totalLabel.innerHTML = `Need: <span>${totalRequired}</span>`;
            required.appendChild(totalLabel);

            const progressBlock = document.createElement('div');
            progressBlock.className = 'item-progress';
            const progressControl = document.createElement('div');
            progressControl.className = 'item-progress-control';

            const progressLabel = document.createElement('span');
            progressLabel.className = 'item-progress-label';
            progressLabel.textContent = 'Submitted';
            progressControl.appendChild(progressLabel);

            const progressInput = document.createElement('input');
            progressInput.type = 'number';
            progressInput.min = '0';
            progressInput.step = '1';
            if (Number.isFinite(maxValue) && maxValue !== Number.MAX_SAFE_INTEGER) {
                progressInput.max = String(maxValue);
            }
            progressControl.appendChild(progressInput);

            const remainingSpan = document.createElement('span');
            remainingSpan.className = 'item-remaining';
            progressControl.appendChild(remainingSpan);

            progressBlock.appendChild(progressControl);

            const progressHint = document.createElement('div');
            progressHint.className = 'item-progress-hint';
            progressBlock.appendChild(progressHint);

            required.appendChild(progressBlock);

            const holdingsBar = document.createElement('div');
            holdingsBar.className = 'item-holdings-bar';

            const ownedLabel = document.createElement('div');
            ownedLabel.className = 'item-owned';
            ownedLabel.textContent = 'Owned: ';
            const ownedValue = document.createElement('span');
            ownedValue.className = 'item-owned-value';

            if (summary.allowedLootStates) {
                ownedValue.dataset.lootFilter = JSON.stringify(summary.allowedLootStates);
            }

            let cachedOwnedTotal = null;
            if (itemIdentifier) {
                ownedValue.dataset.itemId = itemIdentifier;
                let allowedSet = null;
                if (summary.allowedLootStates) {
                    allowedSet = new Set(summary.allowedLootStates);
                }
                cachedOwnedTotal = getCachedHoldingsTotal(itemIdentifier, allowedSet);
            }
            ownedValue.textContent = cachedOwnedTotal !== null ? cachedOwnedTotal : '—';
            ownedLabel.appendChild(ownedValue);
            holdingsBar.appendChild(ownedLabel);

            const holdingsButton = document.createElement('button');
            holdingsButton.type = 'button';
            holdingsButton.className = 'item-holdings-button';
            holdingsButton.title = 'Show holdings across captured characters';
            holdingsButton.innerHTML = '<span class="material-icons" aria-hidden="true">insights</span><span>Holdings</span>';
            if (!itemIdentifier) {
                holdingsButton.disabled = true;
                holdingsButton.title = 'Item identifier unavailable';
            } else {
                holdingsButton.addEventListener('click', (event) => openItemHoldingsModal(normalizedItem, event.currentTarget || holdingsButton, summary.allowedLootStates));
            }
            holdingsBar.appendChild(holdingsButton);

            required.appendChild(holdingsBar);

            if (itemIdentifier) {
                visibleItemIds.push(itemIdentifier);
                if (cachedOwnedTotal !== null) {
                    updateOwnedLabels(itemIdentifier);
                }
            }

            const updateRowClasses = (remainingValue) => {
                remainingSpan.textContent = `Remaining: ${remainingValue}`;
                row.classList.toggle('quest-item-complete', totalRequired > 0 && remainingValue === 0);
            };

            const refreshFromState = () => {
                // We no longer use manual overrides for display in this mode, 
                // but we check if one exists just in case legacy data is present.
                const manual = getManualItemProgress(normalizedItem.item_id);
                const auto = getObjectiveSubmissionsForItem(normalizedItem.item_id, { allowedQuestIds });

                // If we have a manual override, we might want to respect it for display, 
                // but our new logic prefers auto-distribution. 
                // For now, let's show the auto-tracked value which reflects the distributed amount.

                const autoDisplay = totalRequired > 0 ? Math.min(auto, totalRequired) : auto;
                const effective = auto; // Always use auto-tracked value as source of truth

                const submittedValue = Number.isFinite(effective) ? effective : 0;
                const hintParts = [];

                progressInput.value = submittedValue > 0 ? submittedValue : '';
                hintParts.push(`Tracked across objectives: ${autoDisplay}`);

                if (hideLocked && summary.hiddenQuestCount > 0) {
                    hintParts.push(`${summary.hiddenQuestCount} locked quest${summary.hiddenQuestCount === 1 ? '' : 's'} hidden`);
                }
                progressHint.textContent = hintParts.join(' • ');
                const remainingValue = Math.max(0, totalRequired - submittedValue);
                updateRowClasses(remainingValue);
                return submittedValue;
            };

            refreshFromState();

            const handleItemInput = (rawValue) => {
                if (rawValue === '') {
                    distributeItemProgress(normalizedItem.item_id, 0);
                    scheduleGlobalRender(); // Re-render everything to update quest cards
                    return;
                }
                const clamped = clampNumber(rawValue, 0, maxValue);
                if (!Number.isFinite(clamped)) {
                    return;
                }
                distributeItemProgress(normalizedItem.item_id, clamped);
                scheduleGlobalRender(); // Re-render everything to update quest cards
            };

            progressInput.addEventListener('input', (event) => handleItemInput(event.target.value));
            progressInput.addEventListener('change', (event) => handleItemInput(event.target.value));

            const quests = document.createElement('div');
            quests.className = 'item-quests';

            if (item.merchants && item.merchants.length) {
                const merchantTags = document.createElement('div');
                merchantTags.className = 'merchant-tags';
                item.merchants.forEach(entry => {
                    const tag = document.createElement('span');
                    tag.className = 'merchant-tag';
                    tag.innerHTML = `<strong>${entry.name}</strong>`;
                    merchantTags.appendChild(tag);
                });
                quests.appendChild(merchantTags);
            }

            if (summary.visibleQuests && summary.visibleQuests.length) {
                const questTags = document.createElement('div');
                questTags.className = 'quest-tags';
                summary.visibleQuests.forEach(entry => {
                    const tag = document.createElement('span');
                    tag.className = 'quest-tag';
                    const questTitle = entry.questTitle || entry.title || entry.id;
                    const quantityRaw = entry.count ?? entry.total ?? entry.quantity ?? entry.requirement ?? entry.amount;
                    const quantityLabel = quantityRaw !== undefined && quantityRaw !== null && quantityRaw !== ''
                        ? `${quantityRaw}×`
                        : '—';
                    const lootLabels = Array.isArray(entry.loot_state_labels) && entry.loot_state_labels.length
                        ? entry.loot_state_labels
                        : entry.loot_state_label
                            ? [entry.loot_state_label]
                            : (entry.loot_state && typeof entry.loot_state === 'string' && entry.loot_state.trim()
                                ? [entry.loot_state.trim()]
                                : null);
                    const requirementSuffix = lootLabels && lootLabels.length
                        ? `, Loot state: ${lootLabels.join(', ')}`
                        : '';
                    tag.innerHTML = `${entry.merchant ? `<strong>${entry.merchant}</strong> — ` : ''}${questTitle} (<strong>${quantityLabel}</strong>${requirementSuffix})`;
                    questTags.appendChild(tag);
                });
                quests.appendChild(questTags);
            }

            if (hideLocked && summary.hiddenQuestCount > 0) {
                const hiddenHint = document.createElement('div');
                hiddenHint.className = 'item-prerequisite-hint';
                const lockIcon = document.createElement('span');
                lockIcon.className = 'material-icons';
                lockIcon.setAttribute('aria-hidden', 'true');
                lockIcon.textContent = 'lock';
                hiddenHint.appendChild(lockIcon);
                const hintText = document.createElement('span');
                hintText.textContent = summary.hiddenQuestCount === 1
                    ? '1 locked quest hidden by filter'
                    : `${summary.hiddenQuestCount} locked quests hidden by filter`;
                if (summary.hiddenQuestTitles && summary.hiddenQuestTitles.length) {
                    hiddenHint.title = summary.hiddenQuestTitles.join(', ');
                }
                hiddenHint.appendChild(hintText);
                quests.appendChild(hiddenHint);
            }

            row.appendChild(main);
            row.appendChild(required);
            row.appendChild(quests);
            fragment.appendChild(row);
        });

        container.innerHTML = '';
        ensureItemsLoaderAttached();
        if (loader) {
            loader.style.display = 'none';
        }
        container.appendChild(fragment);

        enqueueHoldingsPrefetch(visibleItemIds);
    }

    async function fetchQuests({ force = false, silent = false } = {}) {
        const isFirstLoad = !state.questsLoaded;
        if (isFirstLoad) {
            toggleLoading(elements.questLoading, true);
        } else {
            setRefreshing(elements.questList, true);
        }
        showProgressBar();
        try {
            const response = await fetch(force ? '/api/quests?refresh=1' : '/api/quests');
            const data = await response.json();
            if (!response.ok || data.success === false) {
                throw new Error(data.error || 'Failed to fetch quest data');
            }

            const rawQuests = Array.isArray(data.quests) ? data.quests : [];
            // Archive any completed quests that were present previously but are now missing
            try {
                archiveMissingCompletedQuests(state.quests || [], rawQuests || []);
            } catch (e) {
                // ignore
            }
            // Keep the full quest list for internal tracking (items, archiving, etc.)
            state.quests = rawQuests.map(quest => ({ ...quest }));
            rebuildQuestDependencyIndex();

            // The API returns all possible quests (including rotated dailies/seasonal).
            // For merchant selection we want to hide merchants that only offer
            // time-limited quests (daily/weekly/seasonal) because we can't know
            // whether they're currently active. However, we must keep the full
            // quest list so item submission/badges still work for those quests.
            const allMerchants = Array.isArray(data.merchants) ? data.merchants.slice() : [];
            try {
                // Normalize merchant names for grouping so variants like "Huntress daily"
                // map to the canonical merchant name returned by the server ("Huntress").
                const normalizeMerchant = (m) => {
                    if (!m && m !== 0) return '';
                    let s = String(m).toLowerCase().trim();
                    // strip common frequency words
                    s = s.replace(/\b(daily|weekly|seasonal|season)\b/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
                    return s;
                };

                // Build a normalized merchant -> quest list map
                const merchantMapNorm = new Map();
                rawQuests.forEach(q => {
                    const rawM = q && (q.merchant || q.merchant_original) ? String(q.merchant || q.merchant_original) : '';
                    const key = normalizeMerchant(rawM);
                    if (!key) return;
                    if (!merchantMapNorm.has(key)) merchantMapNorm.set(key, []);
                    merchantMapNorm.get(key).push(q);
                });

                // Filter: only include merchants that have at least one non-time-limited
                // quest
                const frequencyRegex = /\b(daily|weekly|seasonal|season)\b/i;
                const visibleMerchants = allMerchants.filter(m => {
                    if (!m) return false;
                    const key = normalizeMerchant(m);
                    if (FORCED_HIDDEN_MERCHANTS.has(key)) return false;
                    const questsFor = merchantMapNorm.get(key) || [];
                    if (!questsFor.length) return false;
                    // If the literal merchant string contains frequency and no non-time-limited
                    // quests exist in the normalized group, hide it. Otherwise keep.
                    const literalHasFreq = frequencyRegex.test(String(m));
                    if (literalHasFreq) {
                        return questsFor.some(q => !isQuestTimeLimited(q));
                    }
                    return questsFor.some(q => !isQuestTimeLimited(q));
                });

                state.merchants = visibleMerchants;
            } catch (e) {
                // If filtering fails, fall back to the raw merchant list
                state.merchants = data.merchants || [];
            }
            state.questsLoaded = true;
            renderMerchantOptions();
            renderMerchantView();
        } catch (error) {
            console.error(error);
            if (!state.questsLoaded) {
                state.questsLoaded = false;
                renderError(elements.questList, error.message);
            } else if (typeof showNotification === 'function') {
                showNotification('Failed to refresh quests: ' + (error.message || 'Unknown error'), 'error');
            }
        } finally {
            toggleLoading(elements.questLoading, false);
            setRefreshing(elements.questList, false);
            hideProgressBar();
        }
    }

    async function syncProgressFromServer() {
        if (progressSyncInFlight || typeof fetch !== 'function') {
            return;
        }

        progressSyncInFlight = true;
        try {
            const response = await fetch(PROGRESS_SYNC_ENDPOINT, { cache: 'no-store' });
            if (!response.ok) {
                return;
            }
            const data = await response.json();
            if (!data || data.success === false || !data.progress) {
                return;
            }

            const incoming = sanitizeProgressData(data.progress);
            const merged = mergeProgressData(state.progress, incoming);
            const normalized = sanitizeProgressData(merged);
            state.progress = normalized;
            lastServerSyncPayload = JSON.stringify({ progress: normalized });
            schedulePersistProgress(state.progress);

            if (state.questsLoaded) {
                renderMerchantView();
            }
            if (state.itemsLoaded) {
                renderItemsList();
            }
        } catch (error) {
            console.warn('Failed to synchronize quest progress from backend', error);
        } finally {
            progressSyncInFlight = false;
        }
    }

    async function fetchItems({ force = false } = {}) {
        const isFirstLoad = !state.itemsLoaded;
        if (isFirstLoad) {
            ensureItemsLoaderAttached();
            toggleLoading(elements.itemsLoading, true);
        } else {
            setRefreshing(elements.itemsList, true);
        }
        showProgressBar();
        try {
            const response = await fetch(force ? '/api/quests/items?refresh=1' : '/api/quests/items');
            const data = await response.json();
            if (!response.ok || data.success === false) {
                throw new Error(data.error || 'Failed to fetch quest requirements');
            }

            state.aggregatedItems = data.items || [];
            state.itemsLoaded = true;
            renderItemsList();
        } catch (error) {
            console.error(error);
            if (!state.itemsLoaded) {
                state.itemsLoaded = false;
                renderError(elements.itemsList, error.message);
                ensureItemsLoaderAttached();
                if (elements.itemsLoading) {
                    elements.itemsLoading.style.display = 'none';
                }
            } else if (typeof showNotification === 'function') {
                showNotification('Failed to refresh items: ' + (error.message || 'Unknown error'), 'error');
            }
        } finally {
            toggleLoading(elements.itemsLoading, false);
            setRefreshing(elements.itemsList, false);
            hideProgressBar();
        }
    }

    async function refreshAll({ force = false } = {}) {
        const results = await Promise.allSettled([
            fetchQuests({ force, silent: force }),
            fetchItems({ force })
        ]);
        const anyFailed = results.some(r => r.status === 'rejected');
        if (force && typeof showNotification === 'function' && !anyFailed) {
            showNotification('Quest data refreshed', 'success');
        }
    }

    function switchView(view) {
        Object.entries(elements.views).forEach(([name, element]) => {
            if (!element) return;
            element.classList.toggle('hidden', name !== view);
        });
        elements.questTabs.forEach(tab => {
            const isActive = tab.dataset.view === view;
            tab.classList.toggle('active', isActive);
            tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });
    }

    function registerEvents() {
        if (elements.merchantSelect) {
            elements.merchantSelect.addEventListener('change', () => {
                state.selectedMerchant = elements.merchantSelect.value;
                renderMerchantView();
                renderItemsList();
            });
        }

        if (elements.merchantRefresh) {
            elements.merchantRefresh.addEventListener('click', () => runWithButtonLoading(elements.merchantRefresh, () => refreshAll({ force: true })));
        }

        if (elements.itemsRefresh) {
            elements.itemsRefresh.addEventListener('click', () => runWithButtonLoading(elements.itemsRefresh, () => fetchItems({ force: true })));
        }

        if (elements.refreshAll) {
            elements.refreshAll.addEventListener('click', () => runWithButtonLoading(elements.refreshAll, () => refreshAll({ force: true })));
        }

        if (elements.questTabs.length) {
            elements.questTabs.forEach(tab => {
                tab.addEventListener('click', () => switchView(tab.dataset.view));
            });
        }

        if (elements.merchantViewToggle && elements.merchantViewToggle.length) {
            elements.merchantViewToggle.forEach(button => {
                button.addEventListener('click', () => {
                    const mode = button.dataset.mode || 'active';
                    if (state.merchantViewMode === mode) {
                        return;
                    }
                    state.merchantViewMode = mode;
                    updateMerchantViewToggle();
                    renderMerchantView();
                });
            });
        }

        if (elements.itemsSearch) {
            elements.itemsSearch.addEventListener('input', (event) => {
                state.itemSearch = event.target.value;
                if (elements.clearItemSearch) {
                    elements.clearItemSearch.classList.toggle('visible', Boolean(state.itemSearch));
                }
                scheduleGlobalRender();
            });
        }

        if (elements.itemsOwnedFirst) {
            elements.itemsOwnedFirst.addEventListener('change', (event) => {
                state.itemsOwnedFirst = Boolean(event.target.checked);
                updateItemsOwnedToggleUI();
                renderItemsList();
            });
        }

        if (elements.prerequisiteToggle) {
            elements.prerequisiteToggle.addEventListener('change', (event) => {
                state.hideLockedQuests = Boolean(event.target.checked);
                updatePrerequisiteToggleUI();
                renderMerchantView();
                renderItemsList();
            });
        }

        if (elements.clearItemSearch) {
            elements.clearItemSearch.addEventListener('click', () => {
                state.itemSearch = '';
                elements.itemsSearch.value = '';
                elements.clearItemSearch.classList.remove('visible');
                renderItemsList();
            });
        }
    }

    updateMerchantViewToggle();
    registerEvents();
    syncProgressFromServer();

    /* ─── Auto-tracking via packet capture ─── */
    const CAPTURED_ENDPOINT = '/api/quests/captured';
    const AUTO_TRACK_POLL_INTERVAL = 8000; // 8 seconds
    let autoTrackLastUpdate = 0;
    let autoTrackPollTimer = null;
    let autoTrackInFlight = false;

    /**
     * Reconcile captured quest data from packets with DarkerDB quest definitions.
     *
     * The captured data has quest IDs (e.g. "DesignDataQuest:Id_Quest_...")
     * that match the `id` field from the DarkerDB API quests.  For each
     * captured quest we find the matching quest definition, then match
     * captured missions (by contentId = item_id) to objectives and update
     * the progress state.
     */
    function reconcileCapturedProgress(autoProgress) {
        if (!autoProgress || !autoProgress.quests) return false;
        const capturedQuests = autoProgress.quests;
        if (!capturedQuests || typeof capturedQuests !== 'object') return false;

        const questKeys = Object.keys(capturedQuests);
        if (questKeys.length === 0) return false;

        // Strip common game-engine prefixes so packet IDs match DarkerDB short IDs.
        // The Python handler normally does this, but be defensive in case any slip through.
        const GAME_PREFIXES = [
            'DesignDataQuest:Id_Quest_',
            'DesignDataQuestChapter:Id_QuestChapter_',
            'DesignDataMerchant:Id_Merchant_',
            'DesignDataItem:Id_Item_',
            'DesignDataMonster:Id_Monster_',
            'DesignDataObject:Id_Object_',
        ];
        function normalizeGameId(raw) {
            if (!raw) return raw;
            for (const p of GAME_PREFIXES) {
                if (raw.startsWith(p)) return raw.slice(p.length);
            }
            // Generic fallback: "DesignData*:Id_Category_Rest" → "Rest"
            const colonIdx = raw.indexOf(':Id_');
            if (colonIdx >= 0) {
                const after = raw.slice(colonIdx + 4);
                const usIdx = after.indexOf('_');
                if (usIdx >= 0) return after.slice(usIdx + 1);
            }
            return raw;
        }

        // Build lookup from quest ID to DarkerDB quest definition
        const questById = {};
        state.quests.forEach(q => {
            if (q.id) questById[q.id] = q;
        });

        let anyChanged = false;

        console.log('[auto-track] reconciling', questKeys.length, 'captured quests. DarkerDB has', state.quests.length, 'quests, questById keys:', Object.keys(questById).slice(0, 10));

        questKeys.forEach(rawQuestId => {
            const captured = capturedQuests[rawQuestId];
            if (!captured) return;

            const questId = normalizeGameId(rawQuestId);
            const questDef = questById[questId] || questById[rawQuestId];
            if (!questDef) {
                console.warn('[auto-track] No DarkerDB match for quest:', rawQuestId, '→', questId,
                    '| Available IDs sample:', Object.keys(questById).slice(0, 20));
                return;
            }

            const capturedMissions = captured.missions || [];
            const questFlag = captured.quest_flag;
            // quest_flag: 2 = success (ready to turn in), 3 = complete
            const isQuestComplete = questFlag === 2 || questFlag === 3;

            console.log('[auto-track] Matched quest:', questId, '| flag:', questFlag,
                '| isComplete:', isQuestComplete, '| missions:', capturedMissions.length,
                '| objectives:', (questDef.objectives || []).length);

            const objectives = questDef.objectives || [];

            // Strategy: match captured missions to objectives by position first,
            // then by contentId = item_id for Fetch objectives
            capturedMissions.forEach((mission, missionIdx) => {
                const rawContentId = mission.content_id || '';
                const contentId = normalizeGameId(rawContentId);
                const currentValue = mission.current_value || 0;

                // Try to find the matching objective
                let matchedObj = null;
                let matchedIndex = -1;

                // 1. Try positional match
                if (missionIdx < objectives.length) {
                    const candidate = objectives[missionIdx];
                    // If it's a Fetch with same item_id, or any type with current value
                    if (candidate) {
                        if (candidate.type === 'Fetch' && (candidate.item_id === contentId || candidate.item_id === rawContentId)) {
                            matchedObj = candidate;
                            matchedIndex = missionIdx;
                        } else if (contentId && (candidate.item_id === contentId || candidate.item_id === rawContentId)) {
                            matchedObj = candidate;
                            matchedIndex = missionIdx;
                        }
                    }
                }

                // 2. If positional didn't work, scan by contentId
                if (!matchedObj && contentId) {
                    const normContentId = normalizeGameId(contentId);
                    for (let i = 0; i < objectives.length; i++) {
                        const obj = objectives[i];
                        // Try both raw and normalized forms against objective fields
                        const fields = [obj.item_id, obj.monster, obj.interact, obj.module];
                        if (fields.includes(contentId) || fields.includes(normContentId)) {
                            matchedObj = obj;
                            matchedIndex = i;
                            break;
                        }
                    }
                }

                // 3. Fallback: use positional index if there's a count match potential
                if (!matchedObj && missionIdx < objectives.length) {
                    matchedObj = objectives[missionIdx];
                    matchedIndex = missionIdx;
                    console.log('[auto-track]   mission', missionIdx, 'fallback to positional match');
                }

                if (!matchedObj || matchedIndex < 0) {
                    console.warn('[auto-track]   mission', missionIdx, 'NO MATCH (contentId:', contentId, ')');
                    return;
                }

                const key = makeObjectiveKey(questDef, matchedIndex, matchedObj);
                const existing = getObjectiveProgress(key) || {};
                const existingSubmitted = Number(existing.submitted) || 0;
                const newSubmitted = Math.max(existingSubmitted, currentValue);
                const isComplete = isQuestComplete ||
                    (matchedObj.count && newSubmitted >= matchedObj.count);

                console.log('[auto-track]   mission', missionIdx, '→ obj', matchedIndex,
                    '| key:', key, '| value:', currentValue, '→', newSubmitted,
                    '| complete:', isComplete, '| existing:', existingSubmitted);

                // Only update if we have new data
                if (newSubmitted > existingSubmitted || (isComplete && !existing.completed)) {
                    console.log('[auto-track]   UPDATING progress for key:', key);
                    setObjectiveProgress(key, {
                        quest_id: questId,
                        objective_index: matchedIndex,
                        type: matchedObj.type || 'Fetch',
                        item_id: matchedObj.item_id || contentId || undefined,
                        submitted: newSubmitted,
                        completed: isComplete,
                    });
                    anyChanged = true;
                }
            });

            // If the quest is complete but we missed missions, mark all objectives done
            if (isQuestComplete) {
                console.log('[auto-track] Quest', questId, 'is complete, marking all', objectives.length, 'objectives done');
                objectives.forEach((obj, idx) => {
                    const key = makeObjectiveKey(questDef, idx, obj);
                    const existing = getObjectiveProgress(key) || {};
                    if (!existing.completed) {
                        console.log('[auto-track]   Marking completed:', key);
                        setObjectiveProgress(key, {
                            quest_id: questId,
                            objective_index: idx,
                            type: obj.type || 'Objective',
                            item_id: obj.item_id || undefined,
                            submitted: obj.count || existing.submitted || 0,
                            completed: true,
                        });
                        anyChanged = true;
                    }
                });
            }
        });

        return anyChanged;
    }

    async function fetchCapturedQuestData() {
        if (autoTrackInFlight || typeof fetch !== 'function') return;
        autoTrackInFlight = true;
        try {
            const response = await fetch(CAPTURED_ENDPOINT, { cache: 'no-store' });
            if (!response.ok) return;
            const data = await response.json();
            if (!data || data.success === false || !data.available) return;

            const autoProgress = data.auto_progress;
            if (!autoProgress || !autoProgress.last_update) return;

            // Skip if no new data since last check
            if (autoProgress.last_update <= autoTrackLastUpdate) return;
            autoTrackLastUpdate = autoProgress.last_update;

            console.log('[auto-track] New captured data:', JSON.stringify(autoProgress).substring(0, 500));

            if (!state.questsLoaded || state.quests.length === 0) {
                console.warn('[auto-track] Quests not loaded yet, skipping reconciliation');
                return;
            }

            const changed = reconcileCapturedProgress(autoProgress);
            console.log('[auto-track] reconciliation result: changed =', changed);
            if (changed) {
                scheduleGlobalRender();
                // Update the auto-tracking indicator
                updateAutoTrackIndicator(true);
            }
        } catch (error) {
            console.warn('Failed to fetch captured quest data', error);
        } finally {
            autoTrackInFlight = false;
        }
    }

    function startAutoTrackPolling() {
        if (autoTrackPollTimer) return;
        // Initial fetch after quests are loaded
        fetchCapturedQuestData();
        autoTrackPollTimer = window.setInterval(fetchCapturedQuestData, AUTO_TRACK_POLL_INTERVAL);
    }

    function stopAutoTrackPolling() {
        if (autoTrackPollTimer) {
            window.clearInterval(autoTrackPollTimer);
            autoTrackPollTimer = null;
        }
    }

    function updateAutoTrackIndicator(hasData) {
        let indicator = document.getElementById('autoTrackIndicator');
        if (!indicator) {
            // Create the indicator in the header actions area
            const headerActions = document.querySelector('.header-actions');
            if (!headerActions) return;
            indicator = document.createElement('div');
            indicator.id = 'autoTrackIndicator';
            indicator.className = 'auto-track-indicator';
            indicator.title = 'Quest progress is being automatically tracked from game packets';
            indicator.innerHTML = '<span class="material-icons" aria-hidden="true">sensors</span><span class="auto-track-label">Auto-tracking</span>';
            headerActions.insertBefore(indicator, headerActions.firstChild);
        }
        indicator.classList.toggle('active', Boolean(hasData));
    }

    // ── Quest event notification helpers ──
    const QUEST_EVENT_MESSAGES = {
        quest_accepted: { message: 'New quest accepted', type: 'success', icon: 'assignment_turned_in' },
        quest_completed: { message: 'Quest completed!', type: 'success', icon: 'emoji_events' },
        quest_items_submitted: { message: 'Quest items submitted', type: 'info', icon: 'inventory_2' },
        quest_list_update: { message: 'Quest board updated', type: 'info', icon: 'sync' },
        quest_log_update: { message: 'Quest log synced', type: 'info', icon: 'sync' },
        quest_merchant_list: { message: 'Merchant data updated', type: 'info', icon: 'storefront' },
    };

    let _questEventDebounce = null;

    function showQuestEventNotification(eventName, detail) {
        const template = QUEST_EVENT_MESSAGES[eventName];
        if (!template || typeof showNotification !== 'function') return;

        let msg = template.message;
        // Enrich message with detail data when available
        if (detail) {
            if (eventName === 'quest_completed' && detail.reward_count) {
                msg += ` — ${detail.reward_count} reward${detail.reward_count > 1 ? 's' : ''} received`;
            } else if ((eventName === 'quest_list_update' || eventName === 'quest_log_update') && detail.in_progress) {
                msg += ` — ${detail.in_progress} in progress`;
            }
        }

        showNotification(msg, template.type, {
            id: 'quest-event-' + eventName,
            duration: eventName === 'quest_completed' ? 5000 : 3500,
        });
    }

    // Listen for quest packet events from the capture system.
    // Notifications are shown by the Python backend via showNotification()
    // (works on every page). This handler only handles quest-page data refresh.
    window.onQuestPacketEvent = function (eventName, rawDetail) {
        // Debounce the data fetch — packets come in bursts
        if (_questEventDebounce) clearTimeout(_questEventDebounce);
        if (autoTrackPollTimer) {
            window.clearInterval(autoTrackPollTimer);
            autoTrackPollTimer = null;
        }
        _questEventDebounce = window.setTimeout(() => {
            _questEventDebounce = null;
            fetchCapturedQuestData();
            autoTrackPollTimer = window.setInterval(fetchCapturedQuestData, AUTO_TRACK_POLL_INTERVAL);
        }, 500);
    };

    // Start polling once quests are loaded
    const originalRefreshAll = refreshAll;
    refreshAll = async function (opts) {
        await originalRefreshAll(opts);
        if (state.questsLoaded) {
            startAutoTrackPolling();
        }
    };

    window.addEventListener('questDataCleared', () => {
        state.progress = sanitizeProgressData(null);
        lastServerSyncPayload = '';
        try {
            window.localStorage?.removeItem(PROGRESS_STORAGE_KEY);
        } catch (error) {
            console.warn('Failed to clear quest progress storage', error);
        }
        if (state.questsLoaded) {
            renderMerchantView();
        }
        if (state.itemsLoaded) {
            renderItemsList();
        }
        syncProgressFromServer();
    });
    window.addEventListener('beforeunload', () => {
        stopAutoTrackPolling();
        scheduleServerPersistProgress({ immediate: true });
    });
    refreshAll({ force: false });
})();
