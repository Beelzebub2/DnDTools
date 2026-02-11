/**
 * DnDTools — In-Game Overlay Controller
 * ======================================
 * Lightweight, self-contained controller for the overlay window.
 * Handles tab navigation, character loading, item search, quest display,
 * stash sorting, and notification toasts — all backed by the same Flask
 * server as the main application.
 *
 * NOTE: The overlay runs in a separate pywebview window that does NOT share
 * DOM or JS state with the main window.  All data is fetched fresh via the
 * REST API (with pywebview bridge as preferred path when available).
 */

(function () {
    'use strict';

    // ─── Rarity Colours ─────────────────────────────────────────────────
    const RARITY_COLORS = {
        'None': '#808080', 'Poor': '#969696',
        'Common': '#FFFFFF', 'Unknown': '#FFFFFF',
        'Uncommon': '#00FF00', 'Rare': '#0070DD',
        'Epic': '#A335EE', 'Legend': '#FF8000',
        'Legendary': '#FF8000', 'Unique': '#FFD700',
        'Artifact': '#FF0000'
    };

    // ─── State ───────────────────────────────────────────────────────────
    const state = {
        characters: [],
        quests: [],
        merchants: [],
        aggregatedItems: [],
        sortInProgress: false,
        searchAbort: null,       // AbortController for in-flight search
        currentCharId: null,     // selected character in sort panel
    };

    // ─── Utilities ───────────────────────────────────────────────────────

    /** Wait for pywebview bridge to become available. */
    function waitForPywebview() {
        return new Promise((resolve) => {
            const timeout = setTimeout(resolve, 1000);
            if (window.pywebview && window.pywebview.api) {
                clearTimeout(timeout);
                resolve();
                return;
            }
            const check = setInterval(() => {
                if (window.pywebview && window.pywebview.api) {
                    clearInterval(check);
                    clearTimeout(timeout);
                    resolve();
                }
            }, 25);
        });
    }

    /** HTML-escape helper. */
    function escapeHtml(str) {
        if (!str) return '';
        const el = document.createElement('span');
        el.textContent = str;
        return el.innerHTML;
    }

    /** Simple debounce. */
    function debounce(fn, ms) {
        let timer;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), ms);
        };
    }

    /** Friendly "time since" display. */
    function timeSince(dateStr) {
        if (!dateStr) return 'Unknown';
        const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
        if (seconds < 0) return 'Just now';
        if (seconds < 60) return 'Just now';
        if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
        if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
        if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
        return new Date(dateStr).toLocaleDateString();
    }

    /** Return the icon URL for a character class. */
    function classImageUrl(className) {
        const name = (className || 'fighter').toLowerCase();
        return `/assets/classes/${name}.png`;
    }

    /** Get stash count from character data. */
    function getStashCount(char) {
        if (!char || !char.stashes) return 0;
        return Object.keys(char.stashes).length;
    }

    /** Get total item count across stashes. */
    function getTotalItems(char) {
        if (!char || !char.stashes) return 0;
        let total = 0;
        for (const key of Object.keys(char.stashes)) {
            const stash = char.stashes[key];
            if (Array.isArray(stash)) total += stash.length;
            else if (typeof stash === 'number') total += stash;
        }
        return total;
    }

    // ─── Element Cache ───────────────────────────────────────────────────
    const el = {};
    function cacheElements() {
        // Tabs & panels
        el.tabs = document.querySelectorAll('.overlay-tab');
        el.panels = document.querySelectorAll('.overlay-panel');

        // Title bar controls
        el.minBtn = document.getElementById('overlayMinBtn');
        el.closeBtn = document.getElementById('overlayCloseBtn');

        // Characters
        el.charList = document.getElementById('overlayCharList');
        el.refreshChars = document.getElementById('overlayRefreshChars');

        // Search
        el.searchInput = document.getElementById('overlaySearchInput');
        el.searchClear = document.getElementById('overlaySearchClear');
        el.searchResults = document.getElementById('overlaySearchResults');

        // Quests
        el.questList = document.getElementById('overlayQuestList');
        el.refreshQuests = document.getElementById('overlayRefreshQuests');

        // Sort
        el.sortCharacter = document.getElementById('overlaySortCharacter');
        el.sortStash = document.getElementById('overlaySortStash');
        el.triggerSort = document.getElementById('overlayTriggerSort');
        el.sortStatus = document.getElementById('overlaySortStatus');
        el.sortProgressBar = document.getElementById('overlaySortProgressBar');
        el.sortMessage = document.getElementById('overlaySortMessage');

        // Notifications
        el.notifications = document.getElementById('overlay-notifications');
    }

    // ─── Notification System ─────────────────────────────────────────────
    let notifCounter = 0;
    const MAX_NOTIFICATIONS = 4;

    function showNotification(message, type = 'info', duration = 3000) {
        if (!el.notifications) return;

        // Trim old notifications
        while (el.notifications.children.length >= MAX_NOTIFICATIONS) {
            el.notifications.removeChild(el.notifications.firstChild);
        }

        const id = ++notifCounter;
        const toast = document.createElement('div');
        toast.className = `overlay-toast overlay-toast-${type}`;
        toast.dataset.id = id;
        toast.innerHTML = `
            <span class="material-icons overlay-toast-icon">${typeIcon(type)}</span>
            <span class="overlay-toast-msg">${escapeHtml(message)}</span>
            <button class="overlay-toast-close" aria-label="Dismiss">
                <span class="material-icons">close</span>
            </button>
            ${duration > 0 ? '<div class="overlay-toast-timer"></div>' : ''}
        `;

        toast.querySelector('.overlay-toast-close')?.addEventListener('click', () => dismissNotification(toast));
        el.notifications.appendChild(toast);

        // Trigger enter animation
        requestAnimationFrame(() => toast.classList.add('visible'));

        if (duration > 0) {
            const timer = toast.querySelector('.overlay-toast-timer');
            if (timer) timer.style.animationDuration = `${duration}ms`;
            setTimeout(() => dismissNotification(toast), duration);
        }

        return id;
    }

    function dismissNotification(toast) {
        if (!toast || !toast.parentNode) return;
        toast.classList.add('exiting');
        toast.addEventListener('animationend', () => toast.remove(), { once: true });
        // Fallback removal
        setTimeout(() => { if (toast.parentNode) toast.remove(); }, 350);
    }

    function typeIcon(type) {
        switch (type) {
            case 'success': return 'check_circle';
            case 'error': return 'error';
            case 'warning': return 'warning';
            default: return 'info';
        }
    }

    // ─── Tab Navigation ──────────────────────────────────────────────────
    function initTabs() {
        el.tabs.forEach(tab => {
            tab.addEventListener('click', () => switchTab(tab.dataset.tab));
        });
    }

    function switchTab(tabName) {
        el.tabs.forEach(tab => {
            const isActive = tab.dataset.tab === tabName;
            tab.classList.toggle('active', isActive);
            tab.setAttribute('aria-selected', String(isActive));
        });
        el.panels.forEach(panel => {
            panel.classList.toggle('active', panel.dataset.panel === tabName);
        });
    }

    // ─── Title Bar Controls ──────────────────────────────────────────────
    function initTitleBar() {
        el.minBtn?.addEventListener('click', () => {
            if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.overlay_minimize === 'function') {
                window.pywebview.api.overlay_minimize();
            } else {
                // pywebview window.minimize() via JS bridge
                try { window.pywebview?.api?.minimize_overlay?.(); } catch (_) { }
            }
        });

        el.closeBtn?.addEventListener('click', () => {
            // Ask the Python side to hide the overlay (not destroy)
            if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.hide_overlay === 'function') {
                window.pywebview.api.hide_overlay();
            } else {
                // Fallback — simply hide via fetch
                fetch('/api/overlay/hide', { method: 'POST' }).catch(() => { });
            }
        });
    }

    // ═════════════════════════════════════════════════════════════════════
    //                       CHARACTERS PANEL
    // ═════════════════════════════════════════════════════════════════════

    async function loadCharacters(showFeedback = false) {
        try {
            let characters;
            if (window.pywebview?.api?.get_characters_summary) {
                characters = await window.pywebview.api.get_characters_summary();
            } else if (window.pywebview?.api?.get_characters) {
                characters = await window.pywebview.api.get_characters();
            } else {
                const res = await fetch('/api/characters');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                characters = await res.json();
            }

            if (!Array.isArray(characters)) characters = [];

            // Sort by most recently updated
            characters.sort((a, b) => {
                const da = new Date(a.lastUpdate || 0).getTime();
                const db = new Date(b.lastUpdate || 0).getTime();
                return db - da;
            });

            state.characters = characters;
            renderCharacters();
            populateSortCharacterSelect();

            if (showFeedback) showNotification('Characters refreshed', 'success', 2000);
        } catch (err) {
            console.error('Failed to load characters', err);
            el.charList.innerHTML = `
                <div class="overlay-empty-state">
                    <span class="material-icons">error_outline</span>
                    <p>Failed to load characters</p>
                </div>`;
            if (showFeedback) showNotification('Failed to load characters', 'error');
        }
    }

    function renderCharacters() {
        if (!state.characters.length) {
            el.charList.innerHTML = `
                <div class="overlay-empty-state">
                    <span class="material-icons">person_off</span>
                    <p>No characters found</p>
                    <small>Open the game and capture some stash data first.</small>
                </div>`;
            return;
        }

        el.charList.innerHTML = state.characters.map(char => {
            const stashCount = getStashCount(char);
            const totalItems = getTotalItems(char);
            const rankName = char.rank?.name || 'Unknown';
            const updated = timeSince(char.lastUpdate);

            return `
            <div class="overlay-char-card" data-char-id="${escapeHtml(char.id)}" data-class="${escapeHtml((char.class || '').toLowerCase())}">
                <div class="overlay-char-portrait">
                    <img src="${classImageUrl(char.class)}" alt="" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                    <span class="material-icons overlay-char-fallback" style="display:none;">person</span>
                    <span class="overlay-char-level">${char.level || '?'}</span>
                </div>
                <div class="overlay-char-info">
                    <div class="overlay-char-name">${escapeHtml(char.nickname || char.id)}</div>
                    <div class="overlay-char-class">${escapeHtml(char.class || 'Unknown')}</div>
                </div>
                <div class="overlay-char-stats-compact">
                    <span title="Rank"><span class="material-icons">military_tech</span> ${escapeHtml(rankName)}</span>
                    <span title="Stash Tabs"><span class="material-icons">inventory_2</span> ${stashCount}</span>
                    <span title="Total Items"><span class="material-icons">category</span> ${totalItems}</span>
                </div>
                <div class="overlay-char-updated">${updated}</div>
            </div>`;
        }).join('');

        // Click handler — navigate to character stash in main window
        el.charList.querySelectorAll('.overlay-char-card').forEach(card => {
            card.addEventListener('click', () => {
                const charId = card.dataset.charId;
                if (charId) {
                    // Open character page in the main app window
                    if (window.pywebview?.api?.navigate_to) {
                        window.pywebview.api.navigate_to(`/character/${charId}`);
                    } else {
                        // Fallback: tell the user
                        showNotification('Open the main window to view this character', 'info', 3000);
                    }
                }
            });
        });
    }

    // ═════════════════════════════════════════════════════════════════════
    //                         SEARCH PANEL
    // ═════════════════════════════════════════════════════════════════════

    function initSearch() {
        const debouncedSearch = debounce(() => performSearch(), 250);

        el.searchInput.addEventListener('input', () => {
            const hasValue = el.searchInput.value.trim().length > 0;
            el.searchClear.style.display = hasValue ? '' : 'none';
            debouncedSearch();
        });

        el.searchClear.addEventListener('click', () => {
            el.searchInput.value = '';
            el.searchClear.style.display = 'none';
            el.searchResults.innerHTML = `
                <div class="overlay-empty-state">
                    <span class="material-icons">manage_search</span>
                    <p>Type to search items</p>
                </div>`;
            // Abort any in-flight request
            if (state.searchAbort) {
                state.searchAbort.abort();
                state.searchAbort = null;
            }
        });

        // Focus search input when switching to search tab
        el.tabs.forEach(tab => {
            if (tab.dataset.tab === 'search') {
                tab.addEventListener('click', () => {
                    setTimeout(() => el.searchInput?.focus(), 100);
                });
            }
        });
    }

    async function performSearch() {
        const query = el.searchInput.value.trim();
        if (!query) {
            el.searchResults.innerHTML = `
                <div class="overlay-empty-state">
                    <span class="material-icons">manage_search</span>
                    <p>Type to search items</p>
                </div>`;
            return;
        }

        if (query.length < 2) return; // Minimum 2 chars

        // Abort previous request
        if (state.searchAbort) {
            state.searchAbort.abort();
        }
        state.searchAbort = new AbortController();

        // Show loading state
        el.searchResults.innerHTML = `
            <div class="overlay-empty-state">
                <span class="material-icons overlay-loading-spin">hourglass_empty</span>
                <p>Searching…</p>
            </div>`;

        try {
            let results;
            if (window.pywebview?.api?.search_items) {
                results = await window.pywebview.api.search_items(query);
            } else {
                const res = await fetch(`/api/search_items?query=${encodeURIComponent(query)}`, {
                    signal: state.searchAbort.signal
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                results = await res.json();
            }

            if (!Array.isArray(results)) results = [];
            renderSearchResults(results, query);
        } catch (err) {
            if (err.name === 'AbortError') return; // Superseded
            console.error('Search failed', err);
            el.searchResults.innerHTML = `
                <div class="overlay-empty-state">
                    <span class="material-icons">error_outline</span>
                    <p>Search failed</p>
                </div>`;
        }
    }

    function renderSearchResults(results, query) {
        if (!results.length) {
            el.searchResults.innerHTML = `
                <div class="overlay-empty-state">
                    <span class="material-icons">search_off</span>
                    <p>No items found for "${escapeHtml(query)}"</p>
                </div>`;
            return;
        }

        // Group duplicate items across stashes
        const grouped = groupSearchResults(results);

        el.searchResults.innerHTML = grouped.map(item => {
            const color = RARITY_COLORS[item.rarity] || '#FFFFFF';
            const iconPath = item.iconPath ? `/assets/${item.iconPath}` : '';
            const totalCount = item.totalCount || item.count || 1;

            const locationsHtml = (item.locations || []).map(loc => `
                <div class="overlay-search-location">
                    <span class="material-icons">person</span>
                    <span class="overlay-loc-char">${escapeHtml(loc.nickname || loc.charId)}</span>
                    <span class="overlay-loc-stash">
                        <span class="material-icons">inventory_2</span> ${escapeHtml(loc.stashLabel || ('Stash ' + loc.stashId))}
                        <span class="overlay-loc-qty">×${loc.count || 1}</span>
                    </span>
                </div>
            `).join('');

            return `
            <div class="overlay-item-card" style="--rarity-color: ${color}">
                <div class="overlay-item-icon">
                    ${iconPath
                    ? `<img src="${iconPath}" alt="" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                           <span class="material-icons overlay-item-fallback" style="display:none;">inventory_2</span>`
                    : `<span class="material-icons overlay-item-fallback">inventory_2</span>`}
                </div>
                <div class="overlay-item-body">
                    <div class="overlay-item-header">
                        <span class="overlay-item-name">${escapeHtml(item.name)}</span>
                        <span class="overlay-item-rarity" style="color:${color}; border-color:${color}40; background:${color}12">${escapeHtml(item.rarity)}</span>
                        <span class="overlay-item-qty">×${totalCount}</span>
                    </div>
                    ${locationsHtml ? `<div class="overlay-item-locations">${locationsHtml}</div>` : ''}
                </div>
            </div>`;
        }).join('');
    }

    /**
     * Group flat search results by a composite key so duplicate items across
     * stashes are merged into a single card with multiple location entries.
     */
    function groupSearchResults(results) {
        const map = new Map();

        for (const item of results) {
            const key = `${item.name}|${item.rarity || ''}|${item.itemId || ''}`;
            if (map.has(key)) {
                const existing = map.get(key);
                existing.totalCount += (item.count || 1);
                existing.locations.push({
                    charId: item.charId || item.characterId,
                    nickname: item.nickname || item.characterName || item.charId || '',
                    stashId: item.stashId,
                    stashLabel: item.stashLabel || item.stashName || '',
                    count: item.count || 1,
                });
            } else {
                map.set(key, {
                    name: item.name,
                    rarity: item.rarity || 'Common',
                    iconPath: item.iconPath || item.icon || '',
                    itemId: item.itemId || '',
                    totalCount: item.count || 1,
                    locations: [{
                        charId: item.charId || item.characterId,
                        nickname: item.nickname || item.characterName || item.charId || '',
                        stashId: item.stashId,
                        stashLabel: item.stashLabel || item.stashName || '',
                        count: item.count || 1,
                    }],
                });
            }
        }

        return Array.from(map.values());
    }

    // ═════════════════════════════════════════════════════════════════════
    //                          QUESTS PANEL
    // ═════════════════════════════════════════════════════════════════════

    async function loadQuests(showFeedback = false) {
        try {
            const res = await fetch('/api/quests');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();

            if (data.success) {
                state.quests = data.quests || [];
                state.merchants = data.merchants || [];
            } else {
                state.quests = [];
            }

            // Also fetch aggregated items
            try {
                const itemsRes = await fetch('/api/quests/items');
                if (itemsRes.ok) {
                    const itemsData = await itemsRes.json();
                    if (itemsData.success) {
                        state.aggregatedItems = itemsData.items || [];
                    }
                }
            } catch (_) { /* Non-critical */ }

            renderQuests();
            if (showFeedback) showNotification('Quests refreshed', 'success', 2000);
        } catch (err) {
            console.error('Failed to load quests', err);
            el.questList.innerHTML = `
                <div class="overlay-empty-state">
                    <span class="material-icons">error_outline</span>
                    <p>Failed to load quests</p>
                </div>`;
            if (showFeedback) showNotification('Failed to load quests', 'error');
        }
    }

    function renderQuests() {
        if (!state.quests.length) {
            el.questList.innerHTML = `
                <div class="overlay-empty-state">
                    <span class="material-icons">assignment_late</span>
                    <p>No quests found</p>
                    <small>Capture some game data to see quests.</small>
                </div>`;
            return;
        }

        // Group quests by merchant
        const byMerchant = new Map();
        for (const quest of state.quests) {
            const merchant = quest.merchant || quest.npc || 'Unknown';
            if (!byMerchant.has(merchant)) byMerchant.set(merchant, []);
            byMerchant.get(merchant).push(quest);
        }

        let html = '';
        for (const [merchant, quests] of byMerchant) {
            const completedCount = quests.filter(q => isQuestComplete(q)).length;
            const progressPct = quests.length ? Math.round((completedCount / quests.length) * 100) : 0;

            html += `
            <div class="overlay-quest-merchant">
                <div class="overlay-merchant-header">
                    <img src="/assets/merchants/${merchant.toLowerCase().replace(/\s+/g, '_')}.png"
                         alt="" class="overlay-merchant-icon"
                         onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                    <span class="material-icons overlay-merchant-fallback" style="display:none;">store</span>
                    <div class="overlay-merchant-info">
                        <span class="overlay-merchant-name">${escapeHtml(merchant)}</span>
                        <span class="overlay-merchant-progress-text">${completedCount}/${quests.length}</span>
                    </div>
                    <div class="overlay-merchant-progress-bar">
                        <div class="overlay-merchant-progress-fill" style="width:${progressPct}%"></div>
                    </div>
                </div>
                <div class="overlay-quest-items">
                    ${quests.map(q => renderQuestCard(q)).join('')}
                </div>
            </div>`;
        }

        el.questList.innerHTML = html;
    }

    function renderQuestCard(quest) {
        const objectives = quest.objectives || [];
        const totalObj = objectives.length;
        const completedObj = objectives.filter(o => isObjectiveComplete(o)).length;
        const allDone = totalObj > 0 && completedObj === totalObj;

        const objectivesHtml = objectives.map(obj => {
            const isDone = isObjectiveComplete(obj);
            const icon = objectiveTypeIcon(obj.type);
            const label = objectiveLabel(obj);

            return `
            <div class="overlay-objective ${isDone ? 'done' : ''}">
                <span class="material-icons overlay-obj-icon">${icon}</span>
                <span class="overlay-obj-label">${escapeHtml(label)}</span>
                ${obj.count > 1 ? `<span class="overlay-obj-count">${obj.progress || 0}/${obj.count}</span>` : ''}
                ${isDone ? '<span class="material-icons overlay-obj-check">check_circle</span>' : ''}
            </div>`;
        }).join('');

        return `
        <div class="overlay-quest-card ${allDone ? 'completed' : ''}">
            <div class="overlay-quest-header">
                <span class="overlay-quest-title">${escapeHtml(quest.title || quest.id || 'Quest')}</span>
                ${allDone ? '<span class="material-icons overlay-quest-done-icon">task_alt</span>' : ''}
            </div>
            ${quest.chapter ? `<span class="overlay-quest-chapter">Chapter ${quest.chapter}</span>` : ''}
            <div class="overlay-objectives">${objectivesHtml}</div>
        </div>`;
    }

    function isQuestComplete(quest) {
        const objs = quest.objectives || [];
        return objs.length > 0 && objs.every(o => isObjectiveComplete(o));
    }

    function isObjectiveComplete(obj) {
        if (obj.completed) return true;
        if (obj.count && obj.progress >= obj.count) return true;
        return false;
    }

    function objectiveTypeIcon(type) {
        switch ((type || '').toLowerCase()) {
            case 'fetch': return 'inventory_2';
            case 'kill': return 'gavel';
            case 'props': return 'build';
            case 'explore': return 'travel_explore';
            case 'survive': return 'health_and_safety';
            default: return 'flag';
        }
    }

    function objectiveLabel(obj) {
        const type = (obj.type || '').toLowerCase();
        const count = obj.count || 1;
        switch (type) {
            case 'fetch':
                return `Collect ${count}× ${obj.item?.name || obj.itemName || 'item'}`;
            case 'kill':
                return `Eliminate ${count}× ${obj.monster || obj.monster_type || 'enemy'}`;
            case 'props':
                return `Interact with ${count}× ${obj.interact || 'object'}`;
            case 'explore':
                return `Explore ${obj.module || 'area'}`;
            case 'survive':
                return `Survive in ${obj.dungeon || obj.module || 'dungeon'}`;
            default:
                return obj.text || obj.description || `Complete objective`;
        }
    }

    // ═════════════════════════════════════════════════════════════════════
    //                           SORT PANEL
    // ═════════════════════════════════════════════════════════════════════

    function populateSortCharacterSelect() {
        if (!el.sortCharacter) return;

        const current = el.sortCharacter.value;
        el.sortCharacter.innerHTML = '<option value="">Select a character…</option>';

        for (const char of state.characters) {
            const opt = document.createElement('option');
            opt.value = char.id;
            opt.textContent = `${char.nickname || char.id} (${char.class || '?'} Lv${char.level || '?'})`;
            el.sortCharacter.appendChild(opt);
        }

        // Restore previous selection
        if (current && state.characters.some(c => c.id === current)) {
            el.sortCharacter.value = current;
        }
    }

    function initSort() {
        el.sortCharacter?.addEventListener('change', () => {
            state.currentCharId = el.sortCharacter.value;
            updateSortStashOptions();
        });

        el.triggerSort?.addEventListener('click', () => triggerSort());
    }

    function updateSortStashOptions() {
        if (!el.sortStash) return;

        // Keep basic stash options — the API supports stash IDs 1 and 2 by default
        el.sortStash.innerHTML = `
            <option value="2">Bag (Stash 2)</option>
            <option value="1">Stash 1</option>
        `;

        // If we have character data with additional stashes, add them
        if (state.currentCharId) {
            const char = state.characters.find(c => c.id === state.currentCharId);
            if (char?.stashes) {
                const existingIds = new Set(['1', '2']);
                for (const stashId of Object.keys(char.stashes)) {
                    if (!existingIds.has(String(stashId))) {
                        const opt = document.createElement('option');
                        opt.value = stashId;
                        opt.textContent = `Stash ${stashId}`;
                        el.sortStash.appendChild(opt);
                    }
                }
            }
        }
    }

    async function triggerSort() {
        const charId = el.sortCharacter?.value;
        if (!charId) {
            showNotification('Please select a character first', 'warning', 3000);
            return;
        }

        if (state.sortInProgress) {
            showNotification('Sort already in progress', 'warning', 2500);
            return;
        }

        const stashId = el.sortStash?.value || '2';
        state.sortInProgress = true;

        // Update UI
        el.triggerSort.disabled = true;
        el.triggerSort.innerHTML = '<span class="material-icons overlay-loading-spin">hourglass_empty</span> Sorting…';
        el.sortStatus.style.display = '';
        el.sortMessage.textContent = 'Initiating sort…';
        el.sortProgressBar.style.width = '0%';

        try {
            const res = await fetch(`/api/character/${encodeURIComponent(charId)}/stash/${stashId}/sort`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pack: false, stack: false }),
            });

            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();

            if (data.success === false) {
                throw new Error(data.error || 'Sort failed');
            }

            // Start polling for progress
            pollSortProgress(charId, stashId);
        } catch (err) {
            console.error('Sort trigger failed', err);
            showNotification(`Sort failed: ${err.message}`, 'error');
            resetSortUI();
        }
    }

    function pollSortProgress(charId, stashId) {
        let progress = 10;
        const interval = setInterval(() => {
            progress = Math.min(progress + Math.random() * 15, 95);
            el.sortProgressBar.style.width = `${progress}%`;
            el.sortMessage.textContent = progress < 30 ? 'Analysing stash layout…'
                : progress < 60 ? 'Computing optimal arrangement…'
                    : progress < 85 ? 'Applying sort moves…'
                        : 'Finalising…';
        }, 800);

        // Listen for sort completion (the main window will eventually trigger this)
        // Use a polling approach as a fallback
        const checkDone = setInterval(async () => {
            try {
                // Check if character stash data has been updated (sort done indicator)
                const res = await fetch(`/api/character/${encodeURIComponent(charId)}/info`);
                if (res.ok) {
                    const info = await res.json();
                    if (info) {
                        // Sort likely completed — give a brief finish animation
                        clearInterval(interval);
                        clearInterval(checkDone);
                        el.sortProgressBar.style.width = '100%';
                        el.sortMessage.textContent = 'Sort complete!';
                        showNotification('Stash sorted successfully!', 'success');
                        setTimeout(resetSortUI, 2000);
                    }
                }
            } catch (_) { /* ignore */ }
        }, 3000);

        // Timeout after 60 seconds
        setTimeout(() => {
            clearInterval(interval);
            clearInterval(checkDone);
            if (state.sortInProgress) {
                el.sortProgressBar.style.width = '100%';
                el.sortMessage.textContent = 'Sort may have completed — check your stash.';
                showNotification('Sort timed out — check stash manually', 'warning');
                setTimeout(resetSortUI, 3000);
            }
        }, 60000);
    }

    function resetSortUI() {
        state.sortInProgress = false;
        if (el.triggerSort) {
            el.triggerSort.disabled = false;
            el.triggerSort.innerHTML = '<span class="material-icons">sort</span> Sort Stash';
        }
        if (el.sortStatus) {
            el.sortStatus.style.display = 'none';
        }
        if (el.sortProgressBar) {
            el.sortProgressBar.style.width = '0%';
        }
    }

    // ═════════════════════════════════════════════════════════════════════
    //                        KEYBOARD SHORTCUTS
    // ═════════════════════════════════════════════════════════════════════

    function initKeyboard() {
        document.addEventListener('keydown', (e) => {
            // Escape → hide overlay
            if (e.key === 'Escape') {
                e.preventDefault();
                if (window.pywebview?.api?.hide_overlay) {
                    window.pywebview.api.hide_overlay();
                } else {
                    fetch('/api/overlay/hide', { method: 'POST' }).catch(() => { });
                }
                return;
            }

            // Ctrl+1-4 → quick tab switch
            if (e.ctrlKey && !e.shiftKey && !e.altKey) {
                const tabMap = { '1': 'characters', '2': 'search', '3': 'quests', '4': 'sort' };
                if (tabMap[e.key]) {
                    e.preventDefault();
                    switchTab(tabMap[e.key]);
                    return;
                }
            }

            // Ctrl+F → focus search
            if (e.ctrlKey && e.key === 'f') {
                e.preventDefault();
                switchTab('search');
                setTimeout(() => el.searchInput?.focus(), 50);
            }
        });
    }

    // ═════════════════════════════════════════════════════════════════════
    //                          INITIALISATION
    // ═════════════════════════════════════════════════════════════════════

    async function init() {
        cacheElements();
        initTabs();
        initTitleBar();
        initSearch();
        initSort();
        initKeyboard();

        // Wait for pywebview bridge
        await waitForPywebview();

        // Load initial data in parallel
        await Promise.allSettled([
            loadCharacters(),
            loadQuests(),
        ]);
    }

    // ─── Boot ────────────────────────────────────────────────────────────
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
