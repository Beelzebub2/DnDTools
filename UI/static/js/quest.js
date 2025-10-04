/* global showNotification */
(() => {
    const PROGRESS_STORAGE_KEY = 'dndtools.questProgress.v1';
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
        itemHoldingsCache: {},
        activeHoldingsItemId: null,
        activeHoldingsAnchor: null,
        bodyScrollLock: null
    };

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
        itemHoldingsClose: document.getElementById('itemHoldingsClose')
    };

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

    const updateOwnedLabels = (itemId, total) => {
        if (!itemId) {
            return;
        }
        const numericTotal = Number(total);
        const sanitizedTotal = Number.isFinite(numericTotal) ? numericTotal : 0;
        const selector = `.item-owned-value[data-item-id="${cssEscape(itemId)}"]`;
        document.querySelectorAll(selector).forEach(node => {
            node.textContent = sanitizedTotal;
        });
    };

    const holdingsPrefetchQueue = new Set();
    let holdingsPrefetchInFlight = false;

    const getCachedHoldingsTotal = (itemId) => {
        if (!itemId) {
            return null;
        }
        const cached = state.itemHoldingsCache[itemId];
        if (!cached || !cached.data) {
            return null;
        }
        const total = Number(cached.data.total);
        return Number.isFinite(total) ? total : 0;
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
                updateOwnedLabels(normalized, cachedTotal);
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
                        updateOwnedLabels(id, Number(summary.total) || 0);
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

    const getObjectiveSubmissionsForItem = (itemId) => {
        if (!itemId) {
            return 0;
        }
        return Object.values(state.progress.objectives).reduce((total, entry) => {
            if (!entry || entry.item_id !== itemId) {
                return total;
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

    const buildMerchantView = (quests, viewMode = 'active') => {
        const questsForView = [];
        let questsCount = 0;
        let objectiveCount = 0;
        let itemObjectiveCount = 0;
        let totalActive = 0;
        let totalCompleted = 0;

        quests.forEach((quest) => {
            const partitions = partitionQuestObjectives(quest);
            const totalObjectives = Number(partitions.totalCount) || 0;
            const completedObjectives = partitions.completed.length;
            const activeObjectives = partitions.active.length;
            const allObjectivesCompleted = totalObjectives > 0
                ? completedObjectives === totalObjectives
                : activeObjectives === 0 && completedObjectives === 0;

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
            questsForView.push({ quest, objectives: objectivesWithIndex });
        });

        return {
            questsForView,
            summary: {
                viewMode,
                questsCount,
                objectiveCount,
                itemObjectiveCount,
                totalFiltered: quests.length,
                totalActive,
                totalCompleted
            }
        };
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
        const { viewMode = 'active', objectivesOverride } = options;
        const objectivesToRender = objectivesOverride || quest.objectives || [];

        const card = document.createElement('article');
        card.className = 'quest-card';

        const header = document.createElement('header');
        const title = document.createElement('h3');
        title.textContent = quest.title || quest.id;
        header.appendChild(title);

        if (quest.chapter) {
            const subtitle = document.createElement('div');
            subtitle.className = 'quest-meta';
            subtitle.appendChild(createMetaChip('book', quest.chapter));
            if (quest.prerequisite) {
                subtitle.appendChild(createMetaChip('flag', `Prerequisite: ${quest.prerequisite}`));
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

            content.innerHTML = `<strong>${titleText}</strong>`;

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

            const toggleLabel = document.createElement('label');
            toggleLabel.className = 'objective-progress-toggle';
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = objectiveCompleted;
            const toggleText = document.createElement('span');
            toggleText.textContent = 'Completed';
            toggleLabel.appendChild(checkbox);
            toggleLabel.appendChild(toggleText);
            progressContainer.appendChild(toggleLabel);

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
                    renderMerchantView();
                    if (state.itemsLoaded) {
                        renderItemsList();
                    }
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
        elements.merchantStats.innerHTML = `
            <strong>${questsCount}</strong> quests •
            <strong>${objectiveCount}</strong> ${label} objectives •
            <strong>${itemObjectives}</strong> item turn-ins
        `;
    }

    function renderMerchantOptions() {
        if (!elements.merchantSelect) {
            return;
        }
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
            if (!state.selectedMerchant || !state.merchants.includes(previousSelection)) {
                state.selectedMerchant = state.merchants[0];
            }
            elements.merchantSelect.value = state.selectedMerchant;
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
        questsForView.forEach(({ quest, objectives }) => {
            fragment.appendChild(createQuestCard(quest, { viewMode, objectivesOverride: objectives }));
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

        let top;
        let left;

        if (anchorRect) {
            const spaceBelow = viewportHeight - anchorRect.bottom;
            const showBelow = spaceBelow >= dialogHeight + margin || anchorRect.top <= margin;
            if (showBelow) {
                top = anchorRect.bottom + margin;
            } else {
                top = anchorRect.top - dialogHeight - margin;
            }

            if (top < margin) {
                top = margin;
            }

            left = anchorRect.left + (anchorRect.width / 2) - (dialogWidth / 2);
        } else {
            top = (viewportHeight - dialogHeight) / 2;
            left = (viewportWidth - dialogWidth) / 2;
        }

        if (left < margin) {
            left = margin;
        }
        if (left + dialogWidth > viewportWidth - margin) {
            left = Math.max(margin, viewportWidth - dialogWidth - margin);
        }

        if (dialogHeight && top + dialogHeight > viewportHeight - margin) {
            top = Math.max(margin, viewportHeight - dialogHeight - margin);
        }
        if (top < margin) {
            top = margin;
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

    const navigateToCharacter = (characterId, stashEntry) => {
        hideItemHoldingsModal();
        if (!characterId) {
            return;
        }
        const encodedCharacter = encodeURIComponent(characterId);
        const stashId = stashEntry && stashEntry.stash_id ? String(stashEntry.stash_id) : null;

        const go = () => {
            const targetUrl = stashId
                ? `/character/${encodedCharacter}?stashId=${encodeURIComponent(stashId)}`
                : `/character/${encodedCharacter}`;
            if (typeof window.navigateWithTransition === 'function') {
                window.navigateWithTransition(targetUrl);
            } else {
                window.location.href = targetUrl;
            }
        };

        if (stashId) {
            fetch(`/api/character/${encodedCharacter}/current-stash/${encodeURIComponent(stashId)}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            }).finally(go);
        } else {
            go();
        }
    };

    const renderItemHoldingsModal = (item, summary) => {
        if (!elements.itemHoldingsBody || !elements.itemHoldingsSummary) {
            return;
        }
        const itemId = item && item.item_id ? String(item.item_id) : '';
        const itemName = item && (item.name || item.title) ? (item.name || item.title) : itemId;

        if (elements.itemHoldingsTitle && itemName) {
            elements.itemHoldingsTitle.textContent = `${itemName} Holdings`;
        }

        const total = Number(summary && summary.total);
        const totalValue = Number.isFinite(total) ? total : 0;
        elements.itemHoldingsSummary.innerHTML = `Total owned across captured characters: <strong>${totalValue}</strong>`;
        updateOwnedLabels(itemId, totalValue);

        const characters = Array.isArray(summary && summary.characters) ? summary.characters : [];
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
                const primaryStash = Array.isArray(entry.stashes) && entry.stashes.length ? entry.stashes[0] : null;
                navigateToCharacter(entry.character_id, primaryStash);
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
            updateOwnedLabels(normalizedId, cached.data && cached.data.total);
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
        updateOwnedLabels(normalizedId, summary.total || 0);
        return summary;
    };

    const openItemHoldingsModal = (item, triggerElement) => {
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
                renderItemHoldingsModal({ item_id: itemId, name: itemName }, summary);
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

        const searchTerm = state.itemSearch.trim().toLowerCase();
        const filtered = state.aggregatedItems.filter(item => {
            if (!searchTerm) {
                return true;
            }
            const merchantMatch = (item.merchants || []).some(entry => (entry.name || '').toLowerCase().includes(searchTerm));
            return (item.name || '').toLowerCase().includes(searchTerm) || merchantMatch;
        });

        const totalNeeded = filtered.reduce((total, item) => total + (item.total_required || 0), 0);
        if (elements.itemsMeta) {
            elements.itemsMeta.innerHTML = `Showing <strong>${filtered.length}</strong> of ${state.aggregatedItems.length} items • Total required: <strong>${totalNeeded}</strong>`;
        }

        if (!filtered.length) {
            renderEmpty(container, 'checklist_rtl', 'No matching items', 'Try a different search term or refresh the data.');
            ensureItemsLoaderAttached();
            if (loader) {
                loader.style.display = 'none';
            }
            return;
        }

        const itemsWithIndex = filtered.map((item, index) => ({ item, index }));
        if (state.itemsOwnedFirst) {
            itemsWithIndex.sort((a, b) => {
                const ownedDiff = getOwnedTotalForItem(b.item) - getOwnedTotalForItem(a.item);
                if (ownedDiff !== 0) {
                    return ownedDiff;
                }
                return a.index - b.index;
            });
        }

        const fragment = document.createDocumentFragment();
        const visibleItemIds = [];

        itemsWithIndex.forEach(({ item }) => {
            const itemIdentifier = item.item_id || item.itemId || '';
            const normalizedItem = { ...item, item_id: itemIdentifier };
            const row = document.createElement('div');
            row.className = 'quest-item';

            const totalRequired = Number(item.total_required) || 0;
            const maxValue = totalRequired > 0 ? totalRequired : Number.MAX_SAFE_INTEGER;

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
            let cachedOwnedTotal = null;
            if (itemIdentifier) {
                ownedValue.dataset.itemId = itemIdentifier;
                cachedOwnedTotal = getCachedHoldingsTotal(itemIdentifier);
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
                holdingsButton.addEventListener('click', (event) => openItemHoldingsModal(normalizedItem, event.currentTarget || holdingsButton));
            }
            holdingsBar.appendChild(holdingsButton);

            required.appendChild(holdingsBar);

            if (itemIdentifier) {
                visibleItemIds.push(itemIdentifier);
                if (cachedOwnedTotal !== null) {
                    updateOwnedLabels(itemIdentifier, cachedOwnedTotal);
                }
            }

            const updateRowClasses = (remainingValue) => {
                remainingSpan.textContent = `Remaining: ${remainingValue}`;
                row.classList.toggle('quest-item-complete', totalRequired > 0 && remainingValue === 0);
            };

            const refreshFromState = () => {
                const manual = getManualItemProgress(item.item_id);
                const auto = getObjectiveSubmissionsForItem(item.item_id);
                const autoDisplay = totalRequired > 0 ? Math.min(auto, totalRequired) : auto;
                const effective = manual !== undefined ? clampNumber(manual, 0, maxValue) : clampNumber(auto, 0, maxValue);
                const submittedValue = Number.isFinite(effective) ? effective : 0;
                if (manual !== undefined) {
                    progressInput.value = submittedValue;
                    progressHint.textContent = `Manual override • Auto-tracked: ${autoDisplay}`;
                } else {
                    progressInput.value = submittedValue > 0 ? submittedValue : '';
                    progressHint.textContent = `Auto-tracked from objectives: ${autoDisplay}`;
                }
                const remainingValue = Math.max(0, totalRequired - submittedValue);
                updateRowClasses(remainingValue);
                return submittedValue;
            };

            refreshFromState();

            const handleItemInput = (rawValue) => {
                if (rawValue === '') {
                    setItemProgress(item.item_id, '');
                    refreshFromState();
                    return;
                }
                const clamped = clampNumber(rawValue, 0, maxValue);
                if (!Number.isFinite(clamped)) {
                    return;
                }
                setItemProgress(item.item_id, clamped);
                refreshFromState();
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

            if (item.quests && item.quests.length) {
                const questTags = document.createElement('div');
                questTags.className = 'quest-tags';
                item.quests.forEach(entry => {
                    const tag = document.createElement('span');
                    tag.className = 'quest-tag';
                    const questTitle = entry.title || entry.id;
                    tag.innerHTML = `${entry.merchant ? `<strong>${entry.merchant}</strong> — ` : ''}${questTitle} (<strong>${entry.count}×</strong>)`;
                    questTags.appendChild(tag);
                });
                quests.appendChild(questTags);
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
        toggleLoading(elements.questLoading, true);
        try {
            const response = await fetch(force ? '/api/quests?refresh=1' : '/api/quests');
            const data = await response.json();
            if (!response.ok || data.success === false) {
                throw new Error(data.error || 'Failed to fetch quest data');
            }

            state.quests = data.quests || [];
            state.merchants = data.merchants || [];
            state.questsLoaded = true;
            renderMerchantOptions();
            renderMerchantView();
        } catch (error) {
            console.error(error);
            state.questsLoaded = false;
            renderError(elements.questList, error.message);
        } finally {
            toggleLoading(elements.questLoading, false);
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
        ensureItemsLoaderAttached();
        toggleLoading(elements.itemsLoading, true);
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
            state.itemsLoaded = false;
            renderError(elements.itemsList, error.message);
            ensureItemsLoaderAttached();
            if (elements.itemsLoading) {
                elements.itemsLoading.style.display = 'none';
            }
        } finally {
            toggleLoading(elements.itemsLoading, false);
        }
    }

    async function refreshAll({ force = false } = {}) {
        await Promise.all([
            fetchQuests({ force, silent: force }),
            fetchItems({ force })
        ]);
        if (force && typeof showNotification === 'function') {
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
                renderItemsList();
            });
        }

        if (elements.itemsOwnedFirst) {
            elements.itemsOwnedFirst.addEventListener('change', (event) => {
                state.itemsOwnedFirst = Boolean(event.target.checked);
                updateItemsOwnedToggleUI();
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
        scheduleServerPersistProgress({ immediate: true });
    });
    refreshAll({ force: false });
})();
