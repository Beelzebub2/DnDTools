/* ═══════════════════════════════════════════════════════════
   DnDTools — In-Game Overlay  (v3)
   ═══════════════════════════════════════════════════════════
   Fullscreen overlay matching the main app stash rendering.
   Sidebar characters  +  Sort / Search / Quests tabs.
   ═══════════════════════════════════════════════════════════ */
(() => {
    'use strict';

    // ── Constants ────────────────────────────────────────────
    const RARITY_COLORS = {
        'None': '#808080', 'Poor': '#969696', 'Common': '#FFFFFF',
        'Uncommon': '#00FF00', 'Rare': '#0070DD', 'Epic': '#A335EE',
        'Legend': '#FF8000', 'Legendary': '#FF8000', 'Unique': '#FFD700',
        'Artifact': '#FF0000'
    };

    const RARITY_RANK = {
        'none': 0, 'poor': 1, 'common': 2, 'uncommon': 3,
        'rare': 4, 'epic': 5, 'legend': 6, 'legendary': 6,
        'unique': 7, 'artifact': 8
    };

    const STASH_NAMES = {
        2: 'Bag', 3: 'Equipment', 4: 'Storage',
        5: 'Purchased Storage 1', 6: 'Purchased Storage 2',
        7: 'Purchased Storage 3', 8: 'Purchased Storage 4',
        9: 'Purchased Storage 5', 20: 'Shared Stash', 30: 'Shared Stash Seasonal'
    };

    const CELL_SIZE = 45;
    const CELL_GAP = 3;
    const TILE = CELL_SIZE + CELL_GAP; // 48

    const DEFAULT_SORT_ORDER = [
        { field: 'height', direction: 'desc' },
        { field: 'width', direction: 'desc' },
        { field: 'name', direction: 'desc' },
        { field: 'rarity', direction: 'desc' }
    ];

    const CLASS_MAP = {
        'fighter': 'fighter.png', 'ranger': 'ranger.png', 'rogue': 'rogue.png',
        'wizard': 'wizard.png', 'cleric': 'cleric.png', 'warlock': 'warlock.png',
        'barbarian': 'barbarian.png', 'bard': 'bard.png', 'druid': 'druid.png',
        'sorcerer': 'sorcerer.png'
    };

    const MERCHANT_META = {
        'Tavern Master': { icon: 'local_bar', image: 'tavern_master.png', role: 'Tavern Keeper', color: '#d4a44a' },
        'Armourer': { icon: 'shield', image: 'armourer.png', role: 'Armour Specialist', color: '#7e98b0' },
        'Alchemist': { icon: 'science', image: 'alchemist.png', role: 'Potion Brewer', color: '#6fcf7f' },
        'Huntress': { icon: 'gps_fixed', image: 'huntress.png', role: 'Hunt Contracts', color: '#c87070' },
        'Fortune Teller': { icon: 'auto_awesome', image: 'fortune_teller.png', role: 'Seer of Fate', color: '#b48be4' },
        'Goldsmith': { icon: 'diamond', image: 'goldsmith.png', role: 'Gem Crafter', color: '#e4c869' },
        'Miner': { icon: 'hardware', image: null, role: 'Firedeep Rescuer', color: '#c49856' },
        'Woodsman': { icon: 'park', image: 'woodsman.png', role: 'Wilderness Scout', color: '#78a85a' },
        'Surgeon': { icon: 'healing', image: 'surgeon.png', role: 'Field Medic', color: '#d6685a' },
        'Leathersmith': { icon: 'checkroom', image: 'leathersmith.png', role: 'Hide Worker', color: '#a0785a' },
        'Treasurer': { icon: 'account_balance', image: 'treasurer.png', role: 'Guild Banker', color: '#dbb960' },
        'Tailor': { icon: 'content_cut', image: 'tailor.png', role: 'Cloth Artisan', color: '#a090c0' },
        'Weaponsmith': { icon: 'gavel', image: 'weaponsmith.png', role: 'Weapon Forger', color: '#8899aa' },
        'Nicholas': { icon: 'ac_unit', image: 'nicholas.png', role: 'Winter Guest', color: '#88c8e8' },
        'Krampus': { icon: 'whatshot', image: 'krampus.png', role: 'Winter Fiend', color: '#d04040' },
        'Goblin Merchant': { icon: 'savings', image: 'goblin_merchant.png', role: 'Black Market', color: '#6bb85a' },
        'Valentine': { icon: 'favorite', image: 'valentine.png', role: 'Cupid Contracts', color: '#e06080' },
        'The Collector': { icon: 'collections_bookmark', image: 'the_collector.png', role: 'Rare Acquisitor', color: '#c0a060' },
        'Squire': { icon: 'military_tech', image: 'squire.png', role: 'Knight Errant', color: '#90a8c0' },
        'Navigator': { icon: 'explore', image: 'navigator.png', role: 'Pathfinder', color: '#68a8c8' },
        'Dealmaker': { icon: 'handshake', image: 'dealmaker.png', role: 'Deal Broker', color: '#b8a060' },
        'Cockatrice': { icon: 'egg', image: 'cockatrice_merchant.png', role: 'Exotic Trader', color: '#c8a848' },
        'Nightmare Mummy': { icon: 'psychology', image: 'nightmare_mummy.png', role: 'Cursed Dealer', color: '#8870a0' },
        'Skeleton Footman': { icon: 'skull', image: 'skeleton_merchant.png', role: 'Bone Trader', color: '#a0a0a0' },
        'Jack O Lantern': { icon: 'local_fire_department', image: 'jack_o_lantern.png', role: 'Harvest Herald', color: '#e08830' },
    };
    const DEFAULT_MERCHANT_META = { icon: 'store', image: null, role: 'Merchant', color: '#cfa346' };

    const QUEST_STATUS_MAP = {
        1: { label: 'In Progress', icon: 'pending', cls: 'status-progress' },
        2: { label: 'Ready to Turn In', icon: 'check_circle', cls: 'status-ready' },
        3: { label: 'Completed', icon: 'verified', cls: 'status-done' },
        5: { label: 'Not Accepted', icon: 'radio_button_unchecked', cls: 'status-not-accepted' },
    };

    const OBJECTIVE_ICONS = {
        'Fetch': 'inventory_2', 'Kill': 'gavel', 'Props': 'build',
        'Explore': 'travel_explore', 'Survive': 'health_and_safety'
    };

    const PROGRESS_KEY = 'dndtools.questProgress.v1';
    const CAPTURED_FLAGS_KEY = 'dndtools.capturedFlags.v1';
    const FORCED_HIDDEN_MERCHANTS = new Set(['huntress']);

    // ── State ────────────────────────────────────────────────
    let characters = [];
    let selectedCharId = null;
    let selectedStashId = null;
    let stashDataCache = {};   // { charId: { stashId: [items...] } }
    let charDetailsCache = {};
    let currentSortOrder = DEFAULT_SORT_ORDER.map(d => ({ ...d }));
    let isPackMode = false;
    let isStackMode = false;
    let isPreviewMode = false;
    let isSorting = false;
    let abortController = null;

    // Quest state
    let questsLoaded = false;
    let allQuests = [];
    let allMerchants = [];
    let activeMerchantIds = new Set();
    let questProgress = { objectives: {}, items: {} };
    let capturedFlags = {};
    let selectedMerchant = null;
    let questViewMode = 'active'; // 'active' | 'completed'
    let hidePrerequisites = false;

    // Deposit state
    let depositFeasibility = null;

    // ── Helpers ──────────────────────────────────────────────
    const $ = (sel, ctx) => (ctx || document).querySelector(sel);
    const $$ = (sel, ctx) => [...(ctx || document).querySelectorAll(sel)];

    function getStashDims(stashId) {
        const id = parseInt(stashId, 10);
        if (id === 3) return [8, 7];   // Equipment
        if (id === 2) return [10, 5];  // Bag
        return [12, 20];               // Storage / Shared
    }

    function getStashName(stashId) {
        return STASH_NAMES[parseInt(stashId, 10)] || `Stash ${stashId}`;
    }

    function getClassIcon(className) {
        if (!className) return '/assets/classes/fighter.png';
        const key = className.toLowerCase();
        const img = CLASS_MAP[key] || 'fighter.png';
        return `/assets/classes/${img}`;
    }

    function getRarityColor(rarity) {
        return RARITY_COLORS[rarity] || RARITY_COLORS['Common'];
    }

    function getRarityRank(rarity) {
        if (typeof rarity === 'number') return rarity;
        if (!rarity) return 0;
        return RARITY_RANK[rarity.toString().toLowerCase()] || 0;
    }

    function getMerchantMeta(name) {
        if (!name) return DEFAULT_MERCHANT_META;
        if (MERCHANT_META[name]) return MERCHANT_META[name];
        const lower = name.toLowerCase();
        for (const [key, meta] of Object.entries(MERCHANT_META)) {
            const kl = key.toLowerCase();
            if (lower === kl || lower.startsWith(kl) || kl.startsWith(lower)) return meta;
        }
        return DEFAULT_MERCHANT_META;
    }

    function normalizeMerchantForMatch(name) {
        return name ? String(name).replace(/\s+/g, '').toLowerCase() : '';
    }

    function isQuestTimeLimited(quest) {
        if (!quest) return false;
        const kw = ['daily', 'weekly', 'seasonal', 'season'];
        const test = v => v ? String(v).toLowerCase() : '';
        const merchant = test(quest.merchant);
        const title = test(quest.title || quest.id);
        const id = test(quest.id);
        const freq = test(quest.frequency || quest.repeat || quest.recurrence || quest.schedule);
        return kw.some(k => merchant.includes(k) || title.includes(k) || id.includes(k) || freq.includes(k));
    }

    function timeAgo(iso) {
        if (!iso) return '';
        const diff = Date.now() - new Date(iso).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return 'now';
        if (mins < 60) return `${mins}m`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs}h`;
        return `${Math.floor(hrs / 24)}d`;
    }

    function debounce(fn, ms) {
        let t;
        return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
    }

    // ── API helpers ─────────────────────────────────────────
    async function apiFetch(url, opts = {}) {
        const res = await fetch(url, opts);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    }

    async function apiPost(url, body) {
        return apiFetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
    }

    // ── Notifications ───────────────────────────────────────
    function notify(msg, type = 'success', duration = 3000) {
        const area = $('#overlay-notifications');
        if (!area) return;
        const toast = document.createElement('div');
        toast.className = `overlay-toast overlay-toast-${type}`;
        const iconMap = { success: 'check_circle', error: 'error', warning: 'warning' };
        toast.innerHTML = `
            <span class="material-icons overlay-toast-icon">${iconMap[type] || 'info'}</span>
            <span class="overlay-toast-msg">${msg}</span>
            <button class="overlay-toast-close"><span class="material-icons">close</span></button>
            <div class="overlay-toast-timer" style="animation-duration:${duration}ms"></div>
        `;
        const remove = () => { toast.classList.add('exiting'); setTimeout(() => toast.remove(), 200); };
        toast.querySelector('.overlay-toast-close').onclick = remove;
        setTimeout(remove, duration);
        area.appendChild(toast);
    }

    // ══════════════════════════════════════════════════════════
    //  CHARACTER LIST
    // ══════════════════════════════════════════════════════════
    async function loadCharacters() {
        try {
            characters = await apiFetch('/api/characters');
            renderCharacterList();
        } catch (e) {
            console.error('Failed to load characters:', e);
            $('#overlayCharList').innerHTML = '<div class="overlay-empty-state small"><span class="material-icons">error</span><p>Failed to load</p></div>';
        }
    }

    function renderCharacterList() {
        const list = $('#overlayCharList');
        if (!list) return;
        if (!characters.length) {
            list.innerHTML = '<div class="overlay-empty-state small"><span class="material-icons">person_off</span><p>No characters</p></div>';
            return;
        }
        const frag = document.createDocumentFragment();
        characters.forEach(c => {
            const card = document.createElement('div');
            card.className = 'overlay-char-card' + (c.id === selectedCharId ? ' active' : '');
            card.dataset.charId = c.id;

            const stashIds = c.stashes ? Object.keys(c.stashes) : [];
            const classLower = (c.class || '').toLowerCase();
            const iconSrc = getClassIcon(c.class);

            card.innerHTML = `
                <div class="overlay-char-portrait">
                    <img src="${iconSrc}" alt="${c.class || ''}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
                    <span class="overlay-char-fallback material-icons" style="display:none">person</span>
                    ${c.level ? `<span class="overlay-char-level">${c.level}</span>` : ''}
                </div>
                <div class="overlay-char-meta">
                    <div class="overlay-char-name">${c.nickname || c.streamingModeName || 'Unknown'}</div>
                    <div class="overlay-char-sub">${c.class || 'Unknown'}${stashIds.length ? ` · ${stashIds.length} stashes` : ''}</div>
                </div>
                <span class="overlay-char-updated">${timeAgo(c.lastUpdate)}</span>
            `;
            card.addEventListener('click', () => selectCharacter(c.id));
            frag.appendChild(card);
        });
        list.innerHTML = '';
        list.appendChild(frag);
    }

    // ══════════════════════════════════════════════════════════
    //  CHARACTER SELECTION & STASH INIT
    // ══════════════════════════════════════════════════════════
    async function selectCharacter(charId) {
        if (charId === selectedCharId) return;
        selectedCharId = charId;

        // Update active class
        $$('.overlay-char-card').forEach(el => {
            el.classList.toggle('active', el.dataset.charId === charId);
        });

        // Switch to sort tab
        switchTab('sort');

        // Show loading
        const area = $('#overlayStashArea');
        area.innerHTML = '<div class="overlay-empty-state"><div class="overlay-spinner-large"></div><p>Loading stash data…</p></div>';
        $('#overlayTriggerSort').disabled = true;
        $('#overlayDepositBtn').disabled = true;

        try {
            // Find character stash IDs
            const char = characters.find(c => c.id === charId);
            const stashIds = char && char.stashes ? Object.keys(char.stashes) : ['2', '3'];
            const stashParam = stashIds.join(',');

            const data = await apiFetch(`/api/character/${charId}/init?stashIds=${stashParam}`);

            charDetailsCache[charId] = data.details || {};

            // Store stash data
            if (data.stashes) {
                stashDataCache[charId] = {};
                for (const [sid, stashInfo] of Object.entries(data.stashes)) {
                    if (stashInfo && typeof stashInfo === 'object' && stashInfo.stashData) {
                        stashDataCache[charId][sid] = (stashInfo.stashData[sid] || []).map(item => normalizeItem(item, sid));
                    } else if (Array.isArray(stashInfo)) {
                        stashDataCache[charId][sid] = stashInfo.map(item => normalizeItem(item, sid));
                    } else {
                        stashDataCache[charId][sid] = [];
                    }
                }
            }

            // Apply settings
            if (typeof data.packMode !== 'undefined') {
                isPackMode = !!data.packMode;
                updatePackUI();
            }
            if (typeof data.stackMode !== 'undefined') {
                isStackMode = !!data.stackMode;
                updateStackUI();
            }
            if (data.sortOrder) {
                applySortOrder(data.sortOrder);
            }

            // Build stash tabs
            renderStashTabs(stashIds);

            // Select first stash or character combined view
            const defaultStash = stashIds.includes('2') || stashIds.includes('3') ? 'character' : stashIds[0];
            selectStash(defaultStash || stashIds[0]);
        } catch (e) {
            console.error('Failed to load character:', e);
            area.innerHTML = '<div class="overlay-empty-state"><span class="material-icons">error</span><p>Failed to load character data</p></div>';
        }
    }

    function normalizeItem(item, stashId) {
        if (!item) return null;
        const [gridWidth, gridHeight] = getStashDims(stashId);
        const slotId = typeof item.slotId === 'number' ? item.slotId : parseInt(item.slotId, 10);
        if (isNaN(slotId) || slotId < 0 || slotId >= (gridWidth * gridHeight)) return null;

        const x = slotId % gridWidth;
        const y = Math.floor(slotId / gridWidth);
        const width = Math.min(item.width || 1, gridWidth - x);
        const height = Math.min(item.height || 1, gridHeight - y);
        const maxStack = Math.max(1, Number(item.maxStackSize ?? item.max_stack_size ?? 1));

        return {
            ...item,
            slotId, width, height,
            rarity: item.rarity || 'Common',
            itemCount: item.itemCount || 1,
            maxStackSize: maxStack,
        };
    }

    // ══════════════════════════════════════════════════════════
    //  STASH TABS
    // ══════════════════════════════════════════════════════════
    function renderStashTabs(stashIds) {
        const container = $('#overlayStashTabs');
        if (!container) return;

        container.innerHTML = '';

        // Always add Character (combined) tab if both bag and equipment exist
        const hasBag = stashIds.includes('2');
        const hasEquip = stashIds.includes('3');
        if (hasBag || hasEquip) {
            const charTab = document.createElement('button');
            charTab.className = 'overlay-stash-tab';
            charTab.dataset.stashId = 'character';
            charTab.textContent = 'Character';
            charTab.addEventListener('click', () => selectStash('character'));
            container.appendChild(charTab);
        }

        // Add other stash tabs (exclude bag/equipment from separate tabs since they're in Character)
        stashIds.forEach(sid => {
            if (sid === '2' || sid === '3') return;
            const tab = document.createElement('button');
            tab.className = 'overlay-stash-tab';
            tab.dataset.stashId = sid;
            tab.textContent = getStashName(sid);
            tab.addEventListener('click', () => selectStash(sid));
            container.appendChild(tab);
        });
    }

    function selectStash(stashId) {
        selectedStashId = stashId;

        // Update tab active states
        $$('.overlay-stash-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.stashId === stashId);
        });

        // Enable/disable sort button
        const canSort = stashId && stashId !== 'character' && stashId !== '3';
        $('#overlayTriggerSort').disabled = !canSort && stashId !== 'character';
        // For Character combined view, sort targets Bag(2)
        if (stashId === 'character') {
            $('#overlayTriggerSort').disabled = false;
        }

        // Enable deposit for non-bag, non-equipment stashes and for character view
        const canDeposit = stashId && stashId !== '2' && stashId !== '3';
        $('#overlayDepositBtn').disabled = !canDeposit && stashId !== 'character';
        if (stashId === 'character') {
            $('#overlayDepositBtn').disabled = true;
        }

        renderCurrentStash();
        checkDepositFeasibility();
    }

    // ══════════════════════════════════════════════════════════
    //  STASH GRID RENDERING — CSS Grid matching main app
    // ══════════════════════════════════════════════════════════
    function renderCurrentStash() {
        const area = $('#overlayStashArea');
        if (!area || !selectedCharId) return;

        if (selectedStashId === 'character') {
            renderCombinedCharacterView(area);
            return;
        }

        const items = getStashItems(selectedStashId);
        if (!items) {
            area.innerHTML = '<div class="overlay-empty-state"><span class="material-icons">inventory_2</span><p>No data for this stash</p></div>';
            return;
        }

        // If preview mode, compute sorted layout
        const itemsToRender = isPreviewMode ? computePreviewLayout(selectedStashId, items) : items;

        area.innerHTML = '';
        const wrap = document.createElement('div');
        wrap.className = 'overlay-stash-grid-wrap';
        const label = document.createElement('div');
        label.className = 'overlay-stash-grid-label';
        label.textContent = getStashName(selectedStashId);
        wrap.appendChild(label);
        wrap.appendChild(buildStashGrid(selectedStashId, itemsToRender));
        area.appendChild(wrap);
    }

    function renderCombinedCharacterView(area) {
        area.innerHTML = '';
        const wrap = document.createElement('div');
        wrap.className = 'overlay-combined-grid';

        // Equipment Grid (8×7)
        const equipItems = getStashItems('3') || [];
        if (equipItems.length) {
            const section = document.createElement('div');
            const label = document.createElement('div');
            label.className = 'overlay-section-title';
            label.textContent = 'Equipment';
            section.appendChild(label);
            section.appendChild(buildStashGrid('3', equipItems));
            wrap.appendChild(section);
        }

        // Bag Grid (10×5)
        const bagItems = getStashItems('2') || [];
        const bagItemsToRender = isPreviewMode ? computePreviewLayout('2', bagItems) : bagItems;
        {
            const section = document.createElement('div');
            const label = document.createElement('div');
            label.className = 'overlay-section-title';
            label.textContent = 'Bag';
            section.appendChild(label);
            section.appendChild(buildStashGrid('2', bagItemsToRender));
            wrap.appendChild(section);
        }

        area.appendChild(wrap);
    }

    function getStashItems(stashId) {
        if (!selectedCharId || !stashDataCache[selectedCharId]) return null;
        return stashDataCache[selectedCharId][stashId] || null;
    }

    function buildStashGrid(stashId, items) {
        const [gridWidth, gridHeight] = getStashDims(stashId);

        const grid = document.createElement('div');
        grid.className = 'overlay-stash-grid';

        // Set CSS Grid template matching main app: repeat(N, 45px)
        grid.style.gridTemplateColumns = `repeat(${gridWidth}, ${CELL_SIZE}px)`;
        grid.style.gridTemplateRows = `repeat(${gridHeight}, ${CELL_SIZE}px)`;
        grid.style.gridAutoRows = '0px';
        grid.style.gridAutoColumns = '0px';

        // Compute exact dimensions (matching main app character.js)
        const hGaps = Math.max(gridWidth - 1, 0) * CELL_GAP;
        const vGaps = Math.max(gridHeight - 1, 0) * CELL_GAP;
        const borderAllowance = 4;
        const paddingAllowance = 4;
        const totalW = (gridWidth * CELL_SIZE) + hGaps + borderAllowance + paddingAllowance;
        const totalH = (gridHeight * CELL_SIZE) + vGaps + borderAllowance + paddingAllowance;
        grid.style.width = `${totalW}px`;
        grid.style.height = `${totalH}px`;
        grid.style.maxWidth = `${totalW}px`;
        grid.style.maxHeight = `${totalH}px`;
        grid.style.overflow = 'hidden';

        if (Array.isArray(items) && items.length) {
            items.forEach(item => {
                if (!item) return;
                const slotId = item.displaySlotId != null ? item.displaySlotId : item.slotId;
                const w = Math.max(1, Math.min(Number(item.width) || 1, gridWidth));
                const h = Math.max(1, Math.min(Number(item.height) || 1, gridHeight));
                const x = slotId % gridWidth;
                const y = Math.floor(slotId / gridWidth);

                // Bounds check
                if (x < 0 || x >= gridWidth || y < 0 || y >= gridHeight ||
                    (x + w) > gridWidth || (y + h) > gridHeight) return;

                const el = document.createElement('div');
                el.className = 'overlay-stash-item';
                if (item.displaySlotId != null) el.classList.add('preview-moved');

                // CSS Grid placement — exactly like main app
                el.style.gridColumn = `${x + 1} / span ${w}`;
                el.style.gridRow = `${y + 1} / span ${h}`;

                // Rarity styling — exactly like main app
                const color = getRarityColor(item.rarity);
                el.style.borderColor = color;
                el.style.boxShadow = `inset 0 0 0 1px rgba(0,0,0,0.3), 0 0 0 1px ${color}30, inset 0 0 5px ${color}40`;
                el.style.backgroundColor = `${color}15`;
                el.style.border = `1px solid ${color}`;

                // Item image — uses imagePath just like main app
                if (item.imagePath) {
                    const img = document.createElement('img');
                    img.src = item.imagePath;
                    img.alt = item.name || 'Item';
                    img.className = 'item-image';
                    img.loading = 'lazy';
                    img.draggable = false;
                    el.appendChild(img);
                } else {
                    const txt = document.createElement('span');
                    txt.className = 'item-text-content';
                    txt.textContent = item.name || 'Unknown';
                    el.appendChild(txt);
                }

                // Stack count badge
                if (item.itemCount > 1) {
                    const badge = document.createElement('div');
                    badge.className = 'item-count-badge';
                    badge.textContent = item.itemCount;
                    el.appendChild(badge);
                }

                // Tooltip title
                el.title = `${item.name || 'Unknown'} [${item.rarity || 'Common'}]` +
                    (item.itemCount > 1 ? ` x${item.itemCount}` : '');

                grid.appendChild(el);
            });
        }

        return grid;
    }

    // ══════════════════════════════════════════════════════════
    //  PREVIEW LAYOUT — local sort preview matching main app
    // ══════════════════════════════════════════════════════════
    function computePreviewLayout(stashId, items) {
        if (!Array.isArray(items) || !items.length) return [];
        if (stashId === 'character' || stashId === '3' || stashId === 3) return items;

        const [gridWidth, gridHeight] = getStashDims(stashId);
        if (!gridWidth || !gridHeight) return items;

        // Stack items if stack mode is on
        const stacked = isStackMode ? buildStackedItems(items) : items.map(i => ({ ...i }));
        const working = stacked.length ? stacked : items;

        // Prepare and sort
        const prepared = working.map((item, index) => ({
            ...item,
            width: Math.max(1, Math.min(Number(item.width) || 1, gridWidth)),
            height: Math.max(1, Math.min(Number(item.height) || 1, gridHeight)),
            _originalIndex: index,
            _rarityRank: getRarityRank(item.rarity),
            _normalizedName: (item.name || '').toString().toLowerCase(),
        }));

        prepared.sort((a, b) => compareSortPreview(a, b, currentSortOrder));
        prepared.forEach((item, index) => { item._previewOrder = index; });

        // Place items deterministically
        const placed = placeItems(prepared, gridWidth, gridHeight, isPackMode);
        if (!placed) return items; // Fallback to original

        return placed.map(p => {
            const clone = { ...p.item };
            clone.displaySlotId = (p.y * gridWidth) + p.x;
            delete clone._originalIndex;
            delete clone._rarityRank;
            delete clone._normalizedName;
            delete clone._previewOrder;
            return clone;
        });
    }

    function buildStackedItems(items) {
        if (!Array.isArray(items) || !items.length) return [];
        const groups = new Map();
        const order = [];

        items.forEach((item, index) => {
            const maxStack = Math.max(1, Number(item.maxStackSize ?? item.max_stack_size ?? 1));
            const count = Math.max(1, Number(item.itemCount ?? 1));

            if (maxStack <= 1) {
                order.push({ type: 'single', item: { ...item } });
                return;
            }
            const key = `${item.itemId || item.item_id || item.name || index}|${item.rarity || ''}`;
            if (!groups.has(key)) {
                groups.set(key, { template: { ...item }, total: 0, maxStack, orderIndex: index });
                order.push({ type: 'group', key });
            }
            groups.get(key).total += count;
        });

        const result = [];
        order.forEach(entry => {
            if (entry.type === 'single') { result.push(entry.item); return; }
            const g = groups.get(entry.key);
            if (!g) return;
            let rem = g.total;
            while (rem > 0) {
                const clone = { ...g.template };
                clone.itemCount = Math.min(g.maxStack, rem);
                result.push(clone);
                rem -= clone.itemCount;
            }
        });
        return result;
    }

    function compareSortPreview(a, b, sortOrder) {
        const dirs = Array.isArray(sortOrder) && sortOrder.length ? sortOrder : DEFAULT_SORT_ORDER;
        for (const d of dirs) {
            const field = (d.field || '').toLowerCase();
            const dir = (d.direction || 'desc').toLowerCase();
            switch (field) {
                case 'height': case 'width': {
                    const av = Number(a[field] ?? 0), bv = Number(b[field] ?? 0);
                    if (av !== bv) return dir === 'asc' ? av - bv : bv - av;
                    break;
                }
                case 'name': {
                    const an = a._normalizedName || '', bn = b._normalizedName || '';
                    if (an !== bn) return dir === 'asc' ? an.localeCompare(bn) : bn.localeCompare(an);
                    break;
                }
                case 'rarity': {
                    const ar = a._rarityRank ?? getRarityRank(a.rarity);
                    const br = b._rarityRank ?? getRarityRank(b.rarity);
                    if (ar !== br) return dir === 'asc' ? ar - br : br - ar;
                    break;
                }
                default: {
                    const av = Number(a[field] ?? 0), bv = Number(b[field] ?? 0);
                    if (av !== bv) return dir === 'asc' ? av - bv : bv - av;
                }
            }
        }
        return (a._originalIndex ?? 0) - (b._originalIndex ?? 0);
    }

    function placeItems(items, gw, gh, preferDense) {
        const occ = Array.from({ length: gh }, () => Array(gw).fill(false));
        const results = [];

        // Sort by user order, then size
        const sorted = [...items].sort((a, b) => {
            const oa = typeof a._previewOrder === 'number' ? a._previewOrder : Infinity;
            const ob = typeof b._previewOrder === 'number' ? b._previewOrder : Infinity;
            if (oa !== ob) return oa - ob;
            const areaDiff = (b.width * b.height) - (a.width * a.height);
            if (areaDiff) return areaDiff;
            return (b._rarityRank || 0) - (a._rarityRank || 0);
        });

        for (const item of sorted) {
            const slot = findSlot(item, occ, gw, gh, preferDense);
            if (!slot) return null;
            occupy(occ, slot.x, slot.y, item.width, item.height);
            results.push({ item, x: slot.x, y: slot.y });
        }
        return results;
    }

    function findSlot(item, occ, gw, gh, dense) {
        const limX = gw - item.width, limY = gh - item.height;
        let best = null, bestScore = null;
        for (let y = 0; y <= limY; y++) {
            for (let x = 0; x <= limX; x++) {
                if (!fits(occ, x, y, item.width, item.height)) continue;
                const adj = adjacency(occ, x, y, item.width, item.height, gw, gh);
                const bias = dense ? -adj : adj;
                const score = { x, y, bias };
                if (!bestScore || cmpPlace(score, bestScore) < 0) {
                    bestScore = score;
                    best = { x, y };
                }
            }
        }
        return best;
    }

    function fits(occ, ox, oy, w, h) {
        for (let dy = 0; dy < h; dy++)
            for (let dx = 0; dx < w; dx++)
                if (occ[oy + dy][ox + dx]) return false;
        return true;
    }

    function occupy(occ, ox, oy, w, h) {
        for (let dy = 0; dy < h; dy++)
            for (let dx = 0; dx < w; dx++)
                occ[oy + dy][ox + dx] = true;
    }

    function adjacency(occ, ox, oy, w, h, gw, gh) {
        let score = 0;
        for (let dy = 0; dy < h; dy++)
            for (let dx = 0; dx < w; dx++) {
                const x = ox + dx, y = oy + dy;
                if (x > 0 && occ[y][x - 1]) score++;
                if (x < gw - 1 && occ[y][x + 1]) score++;
                if (y > 0 && occ[y - 1][x]) score++;
                if (y < gh - 1 && occ[y + 1][x]) score++;
            }
        return score;
    }

    function cmpPlace(a, b) {
        if (a.y !== b.y) return a.y - b.y;
        if (a.x !== b.x) return a.x - b.x;
        return a.bias - b.bias;
    }

    // ══════════════════════════════════════════════════════════
    //  SORT ORDERING
    // ══════════════════════════════════════════════════════════
    function applySortOrder(order) {
        if (!Array.isArray(order) || !order.length) return;
        currentSortOrder = order.map(d => ({
            field: (d.field || d.key || '').toString().trim().toLowerCase(),
            direction: (['asc', 'desc'].includes((d.direction || d.dir || '').toString().trim().toLowerCase()))
                ? (d.direction || d.dir).toString().trim().toLowerCase() : 'desc'
        })).filter(d => d.field);
        renderOrderingList();
    }

    function renderOrderingList() {
        const list = $('#overlayOrderingList');
        if (!list) return;
        list.innerHTML = '';
        currentSortOrder.forEach((d, idx) => {
            const row = document.createElement('div');
            row.className = 'overlay-ordering-item';
            row.draggable = true;
            row.dataset.index = idx;
            row.innerHTML = `
                <div class="overlay-ordering-arrows">
                    <button data-dir="up" ${idx === 0 ? 'disabled' : ''}><span class="material-icons">arrow_drop_up</span></button>
                    <button data-dir="down" ${idx === currentSortOrder.length - 1 ? 'disabled' : ''}><span class="material-icons">arrow_drop_down</span></button>
                </div>
                <span class="overlay-ordering-field">${d.field}</span>
                <button class="overlay-ordering-dir">${d.direction.toUpperCase()}</button>
            `;
            // Toggle direction
            row.querySelector('.overlay-ordering-dir').addEventListener('click', () => {
                d.direction = d.direction === 'asc' ? 'desc' : 'asc';
                persistSortOrder();
                renderOrderingList();
                if (isPreviewMode) renderCurrentStash();
            });
            // Arrow buttons
            row.querySelectorAll('.overlay-ordering-arrows button').forEach(btn => {
                btn.addEventListener('click', () => {
                    const dir = btn.dataset.dir;
                    if (dir === 'up' && idx > 0) {
                        [currentSortOrder[idx - 1], currentSortOrder[idx]] = [currentSortOrder[idx], currentSortOrder[idx - 1]];
                    } else if (dir === 'down' && idx < currentSortOrder.length - 1) {
                        [currentSortOrder[idx], currentSortOrder[idx + 1]] = [currentSortOrder[idx + 1], currentSortOrder[idx]];
                    }
                    persistSortOrder();
                    renderOrderingList();
                    if (isPreviewMode) renderCurrentStash();
                });
            });
            // Drag reorder
            row.addEventListener('dragstart', e => { e.dataTransfer.setData('text/plain', idx); row.classList.add('dragging'); });
            row.addEventListener('dragend', () => row.classList.remove('dragging'));
            row.addEventListener('dragover', e => e.preventDefault());
            row.addEventListener('drop', e => {
                e.preventDefault();
                const from = parseInt(e.dataTransfer.getData('text/plain'), 10);
                if (from !== idx) {
                    const [moved] = currentSortOrder.splice(from, 1);
                    currentSortOrder.splice(idx, 0, moved);
                    persistSortOrder();
                    renderOrderingList();
                    if (isPreviewMode) renderCurrentStash();
                }
            });
            list.appendChild(row);
        });
    }

    function persistSortOrder() {
        apiPost('/api/sort_order', { order: currentSortOrder }).catch(e => console.warn('Failed to persist sort order:', e));
    }

    // ══════════════════════════════════════════════════════════
    //  SORT EXECUTION
    // ══════════════════════════════════════════════════════════
    async function triggerSort() {
        if (isSorting || !selectedCharId) return;

        // If combined view, sort the Bag (stash 2)
        const stashIdToSort = selectedStashId === 'character' ? '2' : selectedStashId;
        if (!stashIdToSort || stashIdToSort === '3') return;

        if (abortController) abortController.abort();
        abortController = new AbortController();

        setSortingState(true);

        try {
            const result = await apiPost(`/api/character/${selectedCharId}/stash/${stashIdToSort}/sort`, {
                pack: isPackMode,
                stack: isStackMode
            });

            if (result.success) {
                notify('Sort completed! Refreshing…', 'success');
                // Turn off preview
                if (isPreviewMode) {
                    isPreviewMode = false;
                    updatePreviewUI();
                }
                // Reload stash data
                await reloadStashData();
            } else {
                const msg = result.error || 'Sort failed';
                if (msg.toLowerCase().includes('cancel')) {
                    notify('Sort cancelled', 'warning');
                } else {
                    notify(msg, 'error');
                }
            }
        } catch (e) {
            if (e.name === 'AbortError') {
                notify('Sort cancelled', 'warning');
            } else {
                console.error('Sort error:', e);
                notify('Network error while sorting', 'error');
            }
        } finally {
            setSortingState(false);
        }
    }

    function setSortingState(sorting) {
        isSorting = sorting;
        const overlay = $('#overlaySortOverlay');
        if (overlay) overlay.style.display = sorting ? 'flex' : 'none';
        const msg = $('#overlaySortMessage');
        if (msg) msg.textContent = sorting ? 'Sorting…' : '';
        $('#overlayTriggerSort').disabled = sorting;
    }

    async function reloadStashData() {
        if (!selectedCharId) return;
        try {
            const char = characters.find(c => c.id === selectedCharId);
            const stashIds = char && char.stashes ? Object.keys(char.stashes) : ['2', '3'];
            const data = await apiFetch(`/api/character/${selectedCharId}/stashes?stashIds=${stashIds.join(',')}`);

            stashDataCache[selectedCharId] = {};
            for (const [sid, stashInfo] of Object.entries(data)) {
                if (stashInfo && typeof stashInfo === 'object' && stashInfo.stashData) {
                    stashDataCache[selectedCharId][sid] = (stashInfo.stashData[sid] || []).map(item => normalizeItem(item, sid));
                } else if (Array.isArray(stashInfo)) {
                    stashDataCache[selectedCharId][sid] = stashInfo.map(item => normalizeItem(item, sid));
                } else {
                    stashDataCache[selectedCharId][sid] = [];
                }
            }
            renderCurrentStash();
        } catch (e) {
            console.error('Failed to reload stash data:', e);
        }
    }

    // ══════════════════════════════════════════════════════════
    //  DEPOSIT
    // ══════════════════════════════════════════════════════════
    async function checkDepositFeasibility() {
        depositFeasibility = null;
        const badge = $('#overlayDepositBadge');
        const info = $('#overlayDepositInfo');
        const execBtn = $('#overlayDepositExecute');
        if (badge) { badge.className = 'overlay-deposit-badge'; badge.textContent = ''; }
        if (execBtn) execBtn.disabled = true;

        const stashId = selectedStashId === 'character' ? null : selectedStashId;
        if (!stashId || stashId === '2' || stashId === '3' || !selectedCharId) return;

        if (info) info.textContent = 'Checking feasibility…';

        try {
            const result = await apiPost(`/api/character/${selectedCharId}/stash/transfer/check`, {
                sourceStashId: '2',
                targetStashId: stashId,
                pack: isPackMode,
                stack: isStackMode,
            });

            depositFeasibility = result;

            if (result.success === false && result.error) {
                if (badge) { badge.className = 'overlay-deposit-badge blocked'; badge.textContent = '✗'; }
                if (info) info.textContent = result.error;
                return;
            }

            const { feasible, placeable, total, target_free_cells, target_total_cells, message } = result;
            if (feasible) {
                if (badge) { badge.className = 'overlay-deposit-badge feasible'; badge.textContent = '✓'; }
                if (execBtn) execBtn.disabled = false;
            } else if (placeable > 0) {
                if (badge) { badge.className = 'overlay-deposit-badge partial'; badge.textContent = `${placeable}/${total}`; }
            } else {
                if (badge) { badge.className = 'overlay-deposit-badge blocked'; badge.textContent = '✗'; }
            }
            if (info) info.textContent = message || `${placeable}/${total} items can be deposited. ${target_free_cells}/${target_total_cells} cells free.`;
        } catch (e) {
            console.error('Deposit check failed:', e);
            if (info) info.textContent = 'Failed to check feasibility';
        }
    }

    async function executeDeposit() {
        if (!depositFeasibility || !selectedCharId) return;
        const stashId = selectedStashId === 'character' ? null : selectedStashId;
        if (!stashId) return;

        const execBtn = $('#overlayDepositExecute');
        if (execBtn) { execBtn.disabled = true; execBtn.textContent = 'Depositing…'; }
        setSortingState(true);

        try {
            const result = await apiPost(`/api/character/${selectedCharId}/stash/transfer/execute`, {
                sourceStashId: '2',
                targetStashId: stashId,
                pack: isPackMode,
                stack: isStackMode,
            });

            if (result.success) {
                notify('Deposit completed!', 'success');
                closeAllDropdowns();
                await reloadStashData();
            } else {
                notify(result.error || 'Deposit failed', 'error');
            }
        } catch (e) {
            console.error('Deposit failed:', e);
            notify('Network error during deposit', 'error');
        } finally {
            setSortingState(false);
            if (execBtn) { execBtn.disabled = false; execBtn.innerHTML = '<span class="material-icons">check</span> Execute Deposit'; }
        }
    }

    // ══════════════════════════════════════════════════════════
    //  SEARCH
    // ══════════════════════════════════════════════════════════
    const doSearch = debounce(async (query) => {
        const results = $('#overlaySearchResults');
        const clearBtn = $('#overlaySearchClear');
        if (!query || query.length < 2) {
            results.innerHTML = '<div class="overlay-empty-state"><span class="material-icons">manage_search</span><p>Type to search items across all characters</p></div>';
            if (clearBtn) clearBtn.style.display = 'none';
            return;
        }
        if (clearBtn) clearBtn.style.display = '';

        results.innerHTML = '<div class="overlay-empty-state"><span class="material-icons overlay-loading-spin">refresh</span><p>Searching…</p></div>';

        try {
            const data = await apiFetch(`/api/search_items?query=${encodeURIComponent(query)}`);
            if (!data || !data.length) {
                results.innerHTML = '<div class="overlay-empty-state"><span class="material-icons">search_off</span><p>No items found</p></div>';
                return;
            }
            renderSearchResults(data, results);
        } catch (e) {
            console.error('Search error:', e);
            results.innerHTML = '<div class="overlay-empty-state"><span class="material-icons">error</span><p>Search failed</p></div>';
        }
    }, 300);

    function renderSearchResults(items, container) {
        container.innerHTML = '';
        // Group by item name + rarity
        const groups = new Map();
        items.forEach(item => {
            const key = `${item.name}|${item.rarity}`;
            if (!groups.has(key)) {
                groups.set(key, { ...item, locations: [] });
            }
            groups.get(key).locations.push(item);
        });

        for (const [, group] of groups) {
            const card = document.createElement('div');
            card.className = 'overlay-item-card';
            const color = getRarityColor(group.rarity);
            card.style.setProperty('--rarity-color', color);

            const totalQty = group.locations.reduce((s, l) => s + (l.itemCount || 1), 0);

            card.innerHTML = `
                <div class="overlay-item-icon">
                    ${group.imagePath
                    ? `<img src="${group.imagePath}" alt="${group.name}" onerror="this.parentElement.innerHTML='<span class=\\'material-icons overlay-item-fallback\\'>category</span>'">`
                    : '<span class="material-icons overlay-item-fallback">category</span>'}
                </div>
                <div class="overlay-item-body">
                    <div class="overlay-item-header">
                        <span class="overlay-item-name">${group.name || 'Unknown'}</span>
                        <span class="overlay-item-rarity" style="background:${color}22;color:${color}">${group.rarity || 'Common'}</span>
                        ${totalQty > 1 ? `<span class="overlay-item-qty">×${totalQty}</span>` : ''}
                    </div>
                    <div class="overlay-item-locations">
                        ${group.locations.map(loc =>
                        `<div class="overlay-search-location">
                                <span class="material-icons">inventory_2</span>
                                <span class="overlay-loc-char">${loc.characterNickname || loc.characterClass || '??'}</span>
                                <span class="overlay-loc-stash">${getStashName(loc.stashId)}</span>
                                ${(loc.itemCount || 1) > 1 ? `<span class="overlay-loc-qty">×${loc.itemCount}</span>` : ''}
                            </div>`
                    ).join('')}
                    </div>
                </div>
            `;
            container.appendChild(card);
        }
    }

    // ══════════════════════════════════════════════════════════
    //  QUESTS — matches main app quest.js functionality
    // ══════════════════════════════════════════════════════════
    async function loadQuests() {
        try {
            const data = await apiFetch('/api/quests');
            if (!data.success && data.success !== undefined) {
                console.warn('Quest load returned non-success:', data);
            }
            allQuests = data.quests || [];
            allMerchants = data.merchants || [];
            questsLoaded = true;

            // Load progress from localStorage
            loadQuestProgress();
            // Load captured flags from localStorage
            loadCapturedFlags();
            // Load active merchants from server
            await loadActiveMerchants();
            // Resolve prerequisites
            resolvePrerequisites();

            renderMerchantGallery();
        } catch (e) {
            console.error('Failed to load quests:', e);
            const gallery = $('#overlayMerchantGallery');
            if (gallery) gallery.innerHTML = '<div class="overlay-empty-state"><span class="material-icons">error</span><p>Failed to load quests</p></div>';
        }
    }

    function loadQuestProgress() {
        try {
            const raw = localStorage.getItem(PROGRESS_KEY);
            if (raw) {
                const parsed = JSON.parse(raw);
                questProgress = {
                    objectives: (parsed && parsed.objectives) || {},
                    items: (parsed && parsed.items) || {}
                };
            }
        } catch { /* ignore */ }
    }

    function loadCapturedFlags() {
        try {
            const raw = localStorage.getItem(CAPTURED_FLAGS_KEY);
            if (raw) {
                const parsed = JSON.parse(raw);
                capturedFlags = (parsed && parsed.flags) || {};
                // Apply to quests
                allQuests.forEach(q => {
                    if (q.id && typeof capturedFlags[q.id] === 'number') {
                        q.__capturedFlag = capturedFlags[q.id];
                    }
                });
            }
        } catch { /* ignore */ }
    }

    async function loadActiveMerchants() {
        try {
            const data = await apiFetch('/api/quests/active-merchants');
            if (data && Array.isArray(data.active_merchants)) {
                data.active_merchants.forEach(id => activeMerchantIds.add(id));
            }
        } catch { /* ignore */ }
    }

    function resolvePrerequisites() {
        // Build title/id index
        const titleIndex = new Map();
        const idIndex = new Map();
        allQuests.forEach(q => {
            const key = questKey(q);
            if (key) idIndex.set(key, q);
            if (q.title) titleIndex.set(q.title.toLowerCase(), q);
        });

        allQuests.forEach(quest => {
            if (!quest.prerequisite) {
                quest.__resolvedPrerequisites = [];
                quest.__resolvedPrerequisiteTitles = [];
                return;
            }
            const prereqIds = [];
            const prereqTitles = [];
            const rawPrereqs = Array.isArray(quest.prerequisite) ? quest.prerequisite : [quest.prerequisite];

            rawPrereqs.forEach(prereq => {
                const prereqStr = String(prereq);
                // Try to find by id first
                if (idIndex.has(prereqStr)) {
                    prereqIds.push(prereqStr);
                    const found = idIndex.get(prereqStr);
                    prereqTitles.push(found.title || prereqStr);
                } else {
                    // Try title match
                    const lower = prereqStr.toLowerCase();
                    if (titleIndex.has(lower)) {
                        const found = titleIndex.get(lower);
                        prereqIds.push(questKey(found));
                        prereqTitles.push(found.title || prereqStr);
                    } else {
                        prereqIds.push(prereqStr);
                        prereqTitles.push(prereqStr);
                    }
                }
            });

            quest.__resolvedPrerequisites = prereqIds;
            quest.__resolvedPrerequisiteTitles = prereqTitles;
        });
    }

    function questKey(quest) {
        return quest ? (quest.id || quest.title || '') : '';
    }

    function makeObjectiveKey(quest, index, obj) {
        const qid = quest.id || quest.title || `quest-${index}`;
        const parts = [qid, obj.type || 'Objective', index];
        if (obj.item_id) parts.push(obj.item_id);
        else if (obj.monster) parts.push(obj.monster);
        else if (obj.monster_type) parts.push(obj.monster_type);
        else if (obj.module) parts.push(obj.module);
        else if (obj.interact) parts.push(obj.interact);
        return parts.join('::');
    }

    function isObjectiveCompleted(quest, obj, index) {
        const key = makeObjectiveKey(quest, index, obj);
        const stored = questProgress.objectives[key];
        return Boolean(stored && stored.completed);
    }

    function getObjectiveSubmitted(quest, obj, index) {
        const key = makeObjectiveKey(quest, index, obj);
        const stored = questProgress.objectives[key];
        return stored ? (Number(stored.submitted) || 0) : 0;
    }

    function computeQuestCompletion() {
        const index = new Map();
        allQuests.forEach(quest => {
            const objs = quest.objectives || [];
            const total = objs.length;
            const completed = objs.filter((o, i) => isObjectiveCompleted(quest, o, i)).length;
            const allDone = total > 0 ? completed === total : true;
            const key = questKey(quest);
            if (key) index.set(key, allDone);
        });
        return index;
    }

    function recomputeQuestLockState() {
        const completion = computeQuestCompletion();
        allQuests.forEach(quest => {
            const deps = quest.__resolvedPrerequisites || [];
            const locked = deps.length > 0 && !deps.every(dep => {
                if (!dep) return true;
                if (!completion.has(dep)) return true;
                return completion.get(dep) === true;
            });
            quest.__isLocked = locked;
        });
        return completion;
    }

    function getVisibleMerchants() {
        if (activeMerchantIds.size === 0) return allMerchants;
        const normSet = new Set();
        activeMerchantIds.forEach(id => normSet.add(normalizeMerchantForMatch(id)));
        return allMerchants.filter(m => normSet.has(normalizeMerchantForMatch(m)));
    }

    // ── Merchant Gallery ────────────────────────────────────
    function renderMerchantGallery() {
        const gallery = $('#overlayMerchantGallery');
        if (!gallery || !questsLoaded) return;

        const visible = getVisibleMerchants().filter(m =>
            !FORCED_HIDDEN_MERCHANTS.has(normalizeMerchantForMatch(m))
        );

        recomputeQuestLockState();

        // Build per-merchant stats
        const stats = {};
        visible.forEach(m => { stats[m] = { total: 0, active: 0, completed: 0 }; });
        const hasCaptured = activeMerchantIds.size > 0;

        allQuests.forEach(quest => {
            const m = quest.merchant;
            if (!stats[m]) return;
            if (quest.unreleased) return;
            if (!hasCaptured && isQuestTimeLimited(quest)) return;

            const objs = quest.objectives || [];
            const total = objs.length;
            const completed = objs.filter((o, i) => isObjectiveCompleted(quest, o, i)).length;
            const allDone = total > 0 ? completed === total : true;

            stats[m].total++;
            if (allDone) stats[m].completed++;
            else stats[m].active++;
        });

        gallery.innerHTML = '';
        if (!visible.length) {
            gallery.innerHTML = '<div class="overlay-empty-state"><span class="material-icons">storefront</span><p>No merchants available</p></div>';
            return;
        }

        visible.forEach(merchant => {
            const meta = getMerchantMeta(merchant);
            const s = stats[merchant] || { total: 0, active: 0, completed: 0 };
            const allDone = s.total > 0 && s.completed === s.total;
            const pct = s.total > 0 ? Math.round((s.completed / s.total) * 100) : 0;

            const card = document.createElement('button');
            card.type = 'button';
            card.className = 'overlay-merchant-card' + (allDone ? ' all-done' : '');
            card.addEventListener('click', () => navigateToMerchant(merchant));

            card.innerHTML = `
                <div class="overlay-merchant-card-top">
                    <div class="overlay-merchant-portrait">
                        ${meta.image
                    ? `<img src="/assets/merchants/${meta.image}" alt="${merchant}" onerror="this.parentElement.innerHTML='<span class=\\'overlay-merchant-fallback material-icons\\'>${meta.icon}</span>'">`
                    : `<span class="overlay-merchant-fallback material-icons">${meta.icon}</span>`}
                    </div>
                    <div class="overlay-merchant-meta">
                        <div class="overlay-merchant-name">${merchant}</div>
                        <div class="overlay-merchant-count">${s.active} active · ${s.completed}/${s.total} done</div>
                    </div>
                </div>
                <div class="overlay-merchant-card-bar">
                    <div class="overlay-merchant-card-bar-fill" style="width:${pct}%"></div>
                </div>
            `;
            gallery.appendChild(card);
        });
    }

    // ── Merchant Quest List ─────────────────────────────────
    function navigateToMerchant(merchant) {
        selectedMerchant = merchant;
        $('#overlayMerchantGallery').style.display = 'none';
        $('#overlayMerchantQuests').style.display = '';
        $('#overlayQuestBack').style.display = '';
        $('#overlayQuestTitle').textContent = merchant;

        renderMerchantQuests();
    }

    function navigateBackToMerchants() {
        selectedMerchant = null;
        $('#overlayMerchantGallery').style.display = '';
        $('#overlayMerchantQuests').style.display = 'none';
        $('#overlayQuestBack').style.display = 'none';
        $('#overlayQuestTitle').textContent = 'Merchants';
        renderMerchantGallery();
    }

    function renderMerchantQuests() {
        const container = $('#overlayMerchantQuests');
        if (!container || !selectedMerchant) return;

        const hasCaptured = activeMerchantIds.size > 0;
        const merchantIsActive = hasCaptured; // If we have any capture data

        let quests = allQuests.filter(q => q.merchant === selectedMerchant);

        // Filter time-limited if no active capture data
        if (!merchantIsActive) {
            quests = quests.filter(q => !isQuestTimeLimited(q));
        }

        // Filter unreleased
        quests = quests.filter(q => !q.unreleased);

        recomputeQuestLockState();

        // Partition into active vs completed
        const active = [];
        const completed = [];

        quests.forEach(quest => {
            const objs = quest.objectives || [];
            const total = objs.length;
            const completedCount = objs.filter((o, i) => isObjectiveCompleted(quest, o, i)).length;
            const allDone = total > 0 ? completedCount === total : true;

            if (allDone) completed.push(quest);
            else active.push(quest);
        });

        // Filter by view mode
        const toShow = questViewMode === 'completed' ? completed : active;

        // Header
        const meta = getMerchantMeta(selectedMerchant);
        container.innerHTML = '';

        const header = document.createElement('div');
        header.className = 'overlay-merchant-quests-header';
        header.innerHTML = `
            ${meta.image ? `<img src="/assets/merchants/${meta.image}" alt="">` : ''}
            <span class="overlay-merchant-quests-name">${selectedMerchant}</span>
            <span class="overlay-merchant-quests-stats">${active.length} active · ${completed.length} done</span>
        `;
        container.appendChild(header);

        // View tabs
        const tabs = document.createElement('div');
        tabs.className = 'overlay-quest-view-tabs';
        tabs.style.margin = '6px 0';
        ['active', 'completed'].forEach(mode => {
            const btn = document.createElement('button');
            btn.className = 'overlay-quest-view-tab' + (questViewMode === mode ? ' active' : '');
            btn.textContent = mode === 'active' ? `Active (${active.length})` : `Completed (${completed.length})`;
            btn.addEventListener('click', () => { questViewMode = mode; renderMerchantQuests(); });
            tabs.appendChild(btn);
        });
        container.appendChild(tabs);

        if (!toShow.length) {
            const empty = document.createElement('div');
            empty.className = 'overlay-empty-state small';
            empty.innerHTML = `<span class="material-icons">${questViewMode === 'completed' ? 'check_circle' : 'assignment'}</span><p>No ${questViewMode} quests</p>`;
            container.appendChild(empty);
            return;
        }

        // Render quest cards
        toShow.forEach(quest => {
            const objs = quest.objectives || [];
            const isLocked = quest.__isLocked;

            // Skip locked if filter enabled
            if (isLocked && hidePrerequisites) return;

            const card = document.createElement('div');
            card.className = 'overlay-quest-card';
            if (isLocked) card.classList.add('locked');

            const totalObjs = objs.length;
            const completedObjs = objs.filter((o, i) => isObjectiveCompleted(quest, o, i)).length;
            const allDone = totalObjs > 0 ? completedObjs === totalObjs : true;
            if (allDone) card.classList.add('completed');

            // Header
            const hdr = document.createElement('div');
            hdr.className = 'overlay-quest-header';
            hdr.innerHTML = `<span class="overlay-quest-title">${quest.title || quest.id}</span>`;

            // Chapter badge
            if (quest.chapter) {
                const ch = document.createElement('span');
                ch.className = 'overlay-quest-chapter';
                ch.textContent = quest.chapter;
                hdr.appendChild(ch);
            }

            // Captured flag status badge
            if (typeof quest.__capturedFlag === 'number') {
                const status = QUEST_STATUS_MAP[quest.__capturedFlag];
                if (status) {
                    const badge = document.createElement('span');
                    badge.className = `overlay-quest-status ${status.cls}`;
                    badge.innerHTML = `<span class="material-icons">${status.icon}</span>${status.label}`;
                    hdr.appendChild(badge);
                }
            }

            // Lock indicator
            if (isLocked) {
                const lock = document.createElement('div');
                lock.className = 'overlay-quest-lock';
                const prereqTitles = quest.__resolvedPrerequisiteTitles || [];
                lock.innerHTML = `<span class="material-icons">lock</span>Requires: ${prereqTitles.join(', ') || 'prerequisites'}`;
                hdr.appendChild(lock);
            }

            card.appendChild(hdr);

            // Meta info
            const metaDiv = document.createElement('div');
            metaDiv.className = 'overlay-quest-meta';
            if (quest.prerequisite) {
                const prereqTitles = quest.__resolvedPrerequisiteTitles || [quest.prerequisite];
                metaDiv.innerHTML += `<span><span class="material-icons">flag</span>${prereqTitles.join(', ')}</span>`;
            }
            if (quest.dungeons && quest.dungeons.length) {
                metaDiv.innerHTML += `<span><span class="material-icons">map</span>${quest.dungeons.join(', ')}</span>`;
            }
            if (metaDiv.innerHTML) card.appendChild(metaDiv);

            // Objectives
            if (objs.length) {
                const objDiv = document.createElement('div');
                objDiv.className = 'overlay-objectives';

                objs.forEach((obj, i) => {
                    const done = isObjectiveCompleted(quest, obj, i);
                    const submitted = getObjectiveSubmitted(quest, obj, i);
                    const count = obj.count || 0;
                    const iconName = OBJECTIVE_ICONS[obj.type] || 'task';

                    const row = document.createElement('div');
                    row.className = 'overlay-objective' + (done ? ' done' : '');

                    let label = '';
                    if (obj.type === 'Fetch') {
                        const name = (obj.item && obj.item.name) || obj.item_id || 'Unknown';
                        label = `Collect ${count}× ${name}`;
                    } else if (obj.type === 'Kill') {
                        label = `Eliminate ${count}× ${obj.monster || obj.monster_type || 'enemies'}`;
                    } else if (obj.type === 'Props') {
                        label = `Interact with ${count}× ${obj.interact || 'objects'}`;
                    } else if (obj.type === 'Explore') {
                        label = `Explore ${obj.module || 'the area'}`;
                    } else if (obj.type === 'Survive') {
                        const dungeons = quest.dungeons || [];
                        const survIdx = objs.slice(0, i + 1).filter(o => o.type === 'Survive').length - 1;
                        const dungeon = dungeons[survIdx] || dungeons[i];
                        label = dungeon ? `Survive in ${dungeon}` : 'Survive and extract';
                    } else {
                        label = obj.type || 'Objective';
                    }

                    row.innerHTML = `
                        <span class="material-icons overlay-obj-icon">${iconName}</span>
                        <span class="overlay-obj-label">${label}</span>
                        ${count > 0 ? `<span class="overlay-obj-count">${submitted}/${count}</span>` : ''}
                        ${done ? '<span class="material-icons overlay-obj-check">check_circle</span>' : ''}
                    `;

                    // Rarity badge for fetch items
                    if (obj.type === 'Fetch' && obj.item && obj.item.rarity) {
                        const rc = getRarityColor(obj.item.rarity);
                        const rarityBadge = document.createElement('span');
                        rarityBadge.className = 'overlay-obj-rarity';
                        rarityBadge.style.background = `${rc}22`;
                        rarityBadge.style.color = rc;
                        rarityBadge.textContent = obj.item.rarity;
                        row.insertBefore(rarityBadge, row.querySelector('.overlay-obj-count'));
                    }

                    objDiv.appendChild(row);
                });

                card.appendChild(objDiv);
            }

            // Rewards
            if (quest.rewards && quest.rewards.length) {
                const rewDiv = document.createElement('div');
                rewDiv.className = 'overlay-quest-rewards';
                quest.rewards.forEach(rew => {
                    const chip = document.createElement('span');
                    chip.className = 'overlay-reward-chip';
                    if (typeof rew === 'string') {
                        chip.textContent = rew;
                    } else {
                        chip.innerHTML = `<span class="material-icons">${rew.type === 'gold' ? 'paid' : 'card_giftcard'}</span>${rew.name || rew.type || 'Reward'}${rew.amount ? ` ×${rew.amount}` : ''}`;
                    }
                    rewDiv.appendChild(chip);
                });
                card.appendChild(rewDiv);
            }

            container.appendChild(card);
        });
    }

    // ══════════════════════════════════════════════════════════
    //  UI TOGGLES & HELPERS
    // ══════════════════════════════════════════════════════════
    function updatePackUI() {
        const el = $('#overlayPackToggle');
        if (el) el.checked = isPackMode;
    }

    function updateStackUI() {
        const el = $('#overlayStackToggle');
        if (el) el.checked = isStackMode;
    }

    function updatePreviewUI() {
        const btn = $('#overlayPreviewBtn');
        if (btn) btn.classList.toggle('active', isPreviewMode);
    }

    function switchTab(tabName) {
        $$('.overlay-tab').forEach(t => {
            const active = t.dataset.tab === tabName;
            t.classList.toggle('active', active);
            t.setAttribute('aria-selected', active);
        });
        $$('.overlay-panel').forEach(p => {
            p.classList.toggle('active', p.dataset.panel === tabName);
        });
    }

    function closeAllDropdowns() {
        $$('.overlay-dropdown').forEach(d => d.classList.remove('open'));
    }

    // ══════════════════════════════════════════════════════════
    //  EVENT BINDING
    // ══════════════════════════════════════════════════════════
    function init() {
        // Close button
        const closeBtn = $('#overlayCloseBtn');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                fetch('/api/overlay/hide', { method: 'POST' }).catch(() => { });
            });
        }

        // Tab navigation
        $$('.overlay-tab').forEach(tab => {
            tab.addEventListener('click', () => switchTab(tab.dataset.tab));
        });

        // Refresh characters
        const refreshBtn = $('#overlayRefreshChars');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => {
                refreshBtn.querySelector('.material-icons').classList.add('overlay-loading-spin');
                loadCharacters().finally(() => {
                    refreshBtn.querySelector('.material-icons').classList.remove('overlay-loading-spin');
                });
            });
        }

        // Sort order dropdown
        const orderingDropdown = $('#overlayOrderingDropdown');
        const orderingBtn = $('#overlayOrderingBtn');
        if (orderingBtn && orderingDropdown) {
            orderingBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                orderingDropdown.classList.toggle('open');
            });
        }

        // Reset ordering
        const resetBtn = $('#overlayResetOrdering');
        if (resetBtn) {
            resetBtn.addEventListener('click', () => {
                currentSortOrder = DEFAULT_SORT_ORDER.map(d => ({ ...d }));
                persistSortOrder();
                renderOrderingList();
                if (isPreviewMode) renderCurrentStash();
            });
        }

        // Pack toggle
        const packToggle = $('#overlayPackToggle');
        if (packToggle) {
            packToggle.addEventListener('change', () => {
                isPackMode = packToggle.checked;
                apiPost('/api/pack_mode', { enabled: isPackMode }).catch(() => { });
                if (isPreviewMode) renderCurrentStash();
            });
        }

        // Stack toggle
        const stackToggle = $('#overlayStackToggle');
        if (stackToggle) {
            stackToggle.addEventListener('change', () => {
                isStackMode = stackToggle.checked;
                apiPost('/api/stack_mode', { enabled: isStackMode }).catch(() => { });
                if (isPreviewMode) renderCurrentStash();
            });
        }

        // Preview toggle
        const previewBtn = $('#overlayPreviewBtn');
        if (previewBtn) {
            previewBtn.addEventListener('click', () => {
                isPreviewMode = !isPreviewMode;
                updatePreviewUI();
                renderCurrentStash();
            });
        }

        // Sort trigger
        const sortBtn = $('#overlayTriggerSort');
        if (sortBtn) {
            sortBtn.addEventListener('click', triggerSort);
        }

        // Deposit dropdown
        const depositDropdown = $('#overlayDepositDropdown');
        const depositBtn = $('#overlayDepositBtn');
        if (depositBtn && depositDropdown) {
            depositBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                depositDropdown.classList.toggle('open');
            });
        }
        const depositExec = $('#overlayDepositExecute');
        if (depositExec) {
            depositExec.addEventListener('click', executeDeposit);
        }

        // Search
        const searchInput = $('#overlaySearchInput');
        if (searchInput) {
            searchInput.addEventListener('input', () => doSearch(searchInput.value.trim()));
        }
        const searchClear = $('#overlaySearchClear');
        if (searchClear) {
            searchClear.addEventListener('click', () => {
                searchInput.value = '';
                doSearch('');
            });
        }

        // Quest back button
        const questBack = $('#overlayQuestBack');
        if (questBack) {
            questBack.addEventListener('click', navigateBackToMerchants);
        }

        // Quest prerequisite filter
        const prereqFilter = $('#overlayPrereqFilter');
        if (prereqFilter) {
            prereqFilter.addEventListener('change', () => {
                hidePrerequisites = prereqFilter.checked;
                if (selectedMerchant) renderMerchantQuests();
            });
        }

        // Quest refresh
        const questRefresh = $('#overlayRefreshQuests');
        if (questRefresh) {
            questRefresh.addEventListener('click', () => {
                questRefresh.querySelector('.material-icons').classList.add('overlay-loading-spin');
                loadQuests().finally(() => {
                    questRefresh.querySelector('.material-icons').classList.remove('overlay-loading-spin');
                });
            });
        }

        // Global click to close dropdowns
        document.addEventListener('click', (e) => {
            $$('.overlay-dropdown.open').forEach(d => {
                if (!d.contains(e.target)) d.classList.remove('open');
            });
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                // Close dropdown first, then hide overlay
                const openDropdown = $('.overlay-dropdown.open');
                if (openDropdown) {
                    openDropdown.classList.remove('open');
                } else {
                    fetch('/api/overlay/hide', { method: 'POST' }).catch(() => { });
                }
            }
        });

        // Initial render
        renderOrderingList();
        loadCharacters();
        loadQuests();
    }

    // Boot
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
