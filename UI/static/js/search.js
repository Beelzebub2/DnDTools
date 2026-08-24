(function () {
'use strict';

// The AJAX router re-evaluates this file whenever Search is revisited. Keep
// lexical declarations scoped to this execution so route re-entry cannot fail
// with "Identifier has already been declared".
let searchTimeout = null;
let globalTooltip = null;
let tooltipHideTimeout = null;

const rarityColors = {
    'None': '#808080',      // Gray
    'Poor': '#969696',      // Light Gray
    'Common': '#FFFFFF',    // White
    'Unknown': '#FFFFFF',   // Fallback
    'Uncommon': '#00FF00',  // Green
    'Rare': '#0070DD',      // Blue
    'Epic': '#A335EE',      // Purple
    'Legend': '#FF8000',    // Orange
    'Legendary': '#FF8000', // Orange (alternate name)
    'Unique': '#FFD700',    // Gold
    'Artifact': '#FF0000'   // Red
};

const rarityRanks = {
    'none': 0,
    'poor': 1,
    'common': 2,
    'unknown': 2,
    'uncommon': 3,
    'rare': 4,
    'epic': 5,
    'legend': 6,
    'legendary': 6,
    'unique': 7,
    'mythic': 8,
    'artifact': 9
};

// Global tooltip functions - same as character page
function getOrCreateGlobalTooltip() {
    if (!globalTooltip) {
        globalTooltip = document.createElement('div');
        globalTooltip.className = 'item-tooltip';
        document.body.appendChild(globalTooltip);
    }
    return globalTooltip;
}

function showGlobalTooltip(html, x, y) {
    const tooltip = getOrCreateGlobalTooltip();
    tooltip.innerHTML = html;
    tooltip.style.display = 'block';
    tooltip.classList.add('visible');

    // Position
    const tooltipWidth = tooltip.offsetWidth || 250;
    const tooltipHeight = tooltip.offsetHeight || 150;
    let left = x + 15;
    let top = y + 15;

    if (left + tooltipWidth > window.innerWidth) {
        left = x - tooltipWidth - 15;
    }
    if (top + tooltipHeight > window.innerHeight) {
        top = y - tooltipHeight - 15;
    }

    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
}

function hideGlobalTooltip(delay = 100) {
    if (tooltipHideTimeout) clearTimeout(tooltipHideTimeout);
    tooltipHideTimeout = setTimeout(() => {
        if (globalTooltip) {
            globalTooltip.classList.remove('visible');
            setTimeout(() => {
                if (globalTooltip) globalTooltip.style.display = 'none';
            }, 200);
        }
    }, delay);
}

function formatPrimaryProps(ppArray) {
    if (!Array.isArray(ppArray) || !ppArray.length) {
        return '';
    }
    return ppArray.map(([name, value]) => `<div>${name ?? ''} ${value ?? ''}</div>`).join('');
}

function formatSecondaryProps(spArray) {
    if (!Array.isArray(spArray) || !spArray.length) {
        return '';
    }
    return spArray.map(([name, value]) => {
        const numericValue = Number(value);
        const sign = Number.isFinite(numericValue) && numericValue >= 0 ? '+' : '';
        const displayValue = Number.isFinite(numericValue) ? numericValue : value ?? '';
        return `<div>${sign}${displayValue} ${name ?? ''}</div>`;
    }).join('');
}

// Helper function to generate a unique key for an item
function sanitizeCount(value, fallback = 1) {
    const parsed = Number.parseInt(value, 10);
    if (Number.isFinite(parsed) && parsed >= 0) {
        return parsed;
    }
    return fallback;
}

function normalizeItem(item) {
    const safeItem = item || {};
    const name = typeof safeItem.name === 'string' && safeItem.name.trim() ? safeItem.name : 'Unknown Item';
    const rarity = typeof safeItem.rarity === 'string' && safeItem.rarity.trim() ? safeItem.rarity : 'Unknown';
    const pp = Array.isArray(safeItem.pp) ? safeItem.pp : [];
    const sp = Array.isArray(safeItem.sp) ? safeItem.sp : [];
    const iconPath = safeItem.iconPath || null;
    return { name, rarity, pp, sp, iconPath };
}

function normalizeSlotId(slotId) {
    if (typeof slotId === 'number' && Number.isFinite(slotId)) {
        return slotId;
    }
    if (typeof slotId === 'string' && slotId.trim() !== '') {
        const parsed = Number.parseInt(slotId, 10);
        if (Number.isFinite(parsed)) {
            return parsed;
        }
    }
    return null;
}

function getItemKey(item) {
    const normalized = normalizeItem(item);
    return `${normalized.name}|${normalized.rarity}|${JSON.stringify(normalized.pp)}|${JSON.stringify(normalized.sp)}`;
}

function mergeStashLocation(targetList, addition) {
    if (!Array.isArray(targetList) || !addition || !addition.stashId) {
        return;
    }

    const stashId = String(addition.stashId);
    const additionCount = sanitizeCount(addition.count ?? 0, 0);
    const additionSlotIds = Array.isArray(addition.slotIds) ? addition.slotIds : [];

    let targetEntry = targetList.find(entry => String(entry.stashId) === stashId);
    if (!targetEntry) {
        const uniqueSlots = new Set();
        additionSlotIds.forEach(slot => {
            const normalizedSlot = normalizeSlotId(slot);
            if (normalizedSlot !== null) {
                uniqueSlots.add(normalizedSlot);
            }
        });

        targetList.push({
            stashId,
            count: additionCount,
            slotIds: Array.from(uniqueSlots).sort((a, b) => a - b)
        });
        return;
    }

    targetEntry.count = sanitizeCount((targetEntry.count || 0) + additionCount, 0);
    const slotSet = new Set(Array.isArray(targetEntry.slotIds) ? targetEntry.slotIds : []);
    additionSlotIds.forEach(slot => {
        const normalizedSlot = normalizeSlotId(slot);
        if (normalizedSlot !== null) {
            slotSet.add(normalizedSlot);
        }
    });
    targetEntry.slotIds = Array.from(slotSet).sort((a, b) => a - b);
}

function escapeHtml(str) {
    if (str === undefined || str === null) {
        return '';
    }
    return String(str)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

// Helper function to check if a stash is shared
function isSharedStash(stashId) {
    const normalized = String(stashId ?? '').trim();
    return normalized === '20' || normalized === '30';
}

// Get a friendly name for a stash type
function getStashName(stashId) {
    const normalized = String(stashId ?? '').trim();
    const stashTypes = {
        '20': 'Shared Seasonal',
        '30': 'Shared Stash'
    };
    return stashTypes[normalized] || `Stash ${normalized || '?'}`;
}

// Helper function to group identical items
function groupItems(results) {
    const groupedItems = new Map();
    const sharedStashItems = new Map();
    const characterItems = new Map();

    results.forEach(rawResult => {
        if (!rawResult) {
            return;
        }

        const stashId = String(rawResult.stash_id ?? '').trim();
        if (!stashId) {
            return;
        }

        const normalizedItem = normalizeItem(rawResult.item);
        const itemKey = getItemKey(normalizedItem);
        const normalizedCount = Math.max(sanitizeCount(rawResult.itemCount, 1), 1);
        const slotId = normalizeSlotId(rawResult.slotId);
        const characterId = rawResult.id != null ? String(rawResult.id) : null;
        const nickname = rawResult.nickname || 'Unknown';
        const characterClass = rawResult.class || 'Unknown';
        const level = rawResult.level ?? '?';
        const shared = isSharedStash(stashId);

        const stashLocation = {
            stashId,
            count: normalizedCount,
            slotIds: slotId !== null ? [slotId] : []
        };

        const locationEntry = {
            nickname,
            class: characterClass,
            level,
            id: characterId,
            sharedStash: shared,
            stashLabel: getStashTypeDisplay(stashId),
            stashLocations: [stashLocation]
        };

        if (shared) {
            const existingShared = sharedStashItems.get(itemKey);
            if (!existingShared) {
                sharedStashItems.set(itemKey, {
                    item: normalizedItem,
                    itemCount: normalizedCount,
                    stashId,
                    stashType: getStashName(stashId),
                    isShared: true,
                    locations: [locationEntry]
                });
            } else {
                existingShared.itemCount += normalizedCount;
                const existingLocation = existingShared.locations.find(loc => loc.id === locationEntry.id);
                if (existingLocation) {
                    mergeStashLocation(existingLocation.stashLocations, stashLocation);
                } else {
                    existingShared.locations.push(locationEntry);
                }
            }
            return;
        }

        const characterKey = characterId ?? `__${nickname.toLowerCase()}__${stashId}`;
        if (!characterItems.has(characterKey)) {
            characterItems.set(characterKey, new Map());
        }

        const charItemMap = characterItems.get(characterKey);
        let charEntry = charItemMap.get(itemKey);
        if (!charEntry) {
            charEntry = {
                item: normalizedItem,
                itemCount: 0,
                locationsMap: new Map()
            };
            charItemMap.set(itemKey, charEntry);
        }

        charEntry.itemCount += normalizedCount;
        const locationKey = characterId ?? nickname;
        let locationData = charEntry.locationsMap.get(locationKey);
        if (!locationData) {
            locationData = {
                nickname,
                class: characterClass,
                level,
                id: characterId,
                sharedStash: false,
                stashLabel: getStashTypeDisplay(stashId),
                stashLocations: []
            };
            charEntry.locationsMap.set(locationKey, locationData);
        }

        mergeStashLocation(locationData.stashLocations, stashLocation);
    });

    characterItems.forEach(itemMap => {
        itemMap.forEach((itemEntry, itemKey) => {
            const aggregatedEntry = groupedItems.get(itemKey);
            const locations = Array.from(itemEntry.locationsMap.values());
            if (!aggregatedEntry) {
                groupedItems.set(itemKey, {
                    item: itemEntry.item,
                    itemCount: itemEntry.itemCount,
                    locations
                });
            } else {
                aggregatedEntry.itemCount += itemEntry.itemCount;
                aggregatedEntry.locations.push(...locations);
            }
        });
    });

    return [
        ...Array.from(groupedItems.values()),
        ...Array.from(sharedStashItems.values())
    ];
}

// Helper function to get stash type name
function getStashTypeDisplay(stashId) {
    const stashTypes = {
        2: 'Bag',
        3: 'Equipment',
        4: 'Storage',
        5: 'Purchased Storage 1',
        6: 'Purchased Storage 2',
        7: 'Purchased Storage 3',
        8: 'Purchased Storage 4',
        9: 'Purchased Storage 5',
        20: 'Shared Seasonal',
        30: 'Shared Stash'
    };
    return stashTypes[stashId] || `Stash ${stashId}`;
}

(function () {
    function searchPageInit() {
        const searchInput = document.getElementById('searchInput');
        const searchResults = document.getElementById('searchResults');
        const clearSearch = document.getElementById('clearSearch');
        const searchMeta = document.getElementById('searchMeta');
        const resultsCount = document.getElementById('resultsCount');
        const filterRarity = document.getElementById('filterRarity');

        // A route can change while its script is still loading. Do not bind a
        // detached Search controller to whatever page replaced it.
        if (!searchInput || !searchResults || !clearSearch || !searchMeta || !resultsCount) {
            return;
        }

        let disposed = false;
        let requestVersion = 0;
        let searchAbortController = null;
        let preloadAbortController = null;

        const cancelPendingSearch = () => {
            requestVersion += 1;
            if (searchTimeout !== null) {
                window.clearTimeout(searchTimeout);
                searchTimeout = null;
            }
            if (searchAbortController) {
                searchAbortController.abort();
                searchAbortController = null;
            }
        };

        // Show initial loading if data hasn't been loaded yet
        let isInitialLoad = true;

        // Pre-load character data on page load to avoid delays during search
        const preloadData = async () => {
            try {
                if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_characters === 'function') {
                    await window.pywebview.api.get_characters();
                } else {
                    preloadAbortController = new AbortController();
                    const response = await fetch('/api/characters', { signal: preloadAbortController.signal });
                    await response.json();
                }
                if (!disposed) {
                    isInitialLoad = false;
                }
            } catch (error) {
                if (!disposed && error.name !== 'AbortError') {
                    console.warn('Failed to preload character data:', error);
                    isInitialLoad = false;
                }
            } finally {
                preloadAbortController = null;
            }
        };

        // Start preloading in the background
        preloadData();

        // ── Filter helpers ─────────────────────────────────────────────

        /** Keep free text and exact-match filters separate across both APIs. */
        function getSearchCriteria() {
            return {
                query: searchInput.value.trim(),
                rarity: filterRarity ? filterRarity.value.trim() : ''
            };
        }

        /** Update the visual state of the rarity dropdown. */
        function updateFilterUI() {
            if (filterRarity) {
                filterRarity.classList.toggle('filter-active', !!filterRarity.value);
            }
        }

        /** Trigger a search using the current text and structured filters. */
        function triggerFilteredSearch() {
            updateFilterUI();
            const criteria = getSearchCriteria();
            if (criteria.query || criteria.rarity) {
                performSearch(criteria.query, criteria.rarity);
            } else {
                cancelPendingSearch();
                showEmptyState();
                updateResultsCount(0, '');
            }
        }

        // ── Clear search ────────────────────────────────────────────────
        const handleClearSearch = () => {
            cancelPendingSearch();
            searchInput.value = '';
            if (filterRarity) filterRarity.value = '';
            clearSearch.style.display = 'none';
            searchMeta.textContent = '';
            resultsCount.textContent = '';
            updateFilterUI();
            showEmptyState();
            searchInput.focus();
        };
        clearSearch.addEventListener('click', handleClearSearch);

        // Show/hide clear button based on input
        const handleSearchInput = (e) => {
            const value = e.target.value.trim();
            clearSearch.style.display = value ? 'flex' : 'none';

            if (!value && !(filterRarity && filterRarity.value)) {
                searchMeta.textContent = '';
                resultsCount.textContent = '';
            }
        };
        searchInput.addEventListener('input', handleSearchInput);

        // ── Filter event handler ────────────────────────────────────────
        const handleRarityChange = () => triggerFilteredSearch();
        if (filterRarity) {
            filterRarity.addEventListener('change', handleRarityChange);
        }

        const showEmptyState = () => {
            searchResults.innerHTML = `
            <div class="empty-search-state">
                <span class="material-icons">search</span>
                <h3>Ready to search</h3>
                <p>Enter search terms above to find items across all your character stashes</p>
            </div>
        `;
        };

        const showLoadingState = () => {
            const loadingMessage = isInitialLoad ?
                'Loading character data for the first time...' :
                'Searching your character stashes...';

            searchResults.innerHTML = `
            <div class="loading">
                <span class="material-icons">hourglass_empty</span>
                ${loadingMessage}
            </div>
        `;
        };

        const updateResultsCount = (count, query) => {
            if (count === 0) {
                resultsCount.textContent = 'No results';
                searchMeta.textContent = query ? `No items found for "${query}"` : '';
            } else {
                resultsCount.textContent = `${count} ${count === 1 ? 'result' : 'results'}`;
                searchMeta.textContent = query ? `Found items matching "${query}"` : '';
            }
        };

        const displayResults = (results, query = '') => {
            const container = document.getElementById('searchResults');
            if (!container || disposed) {
                return;
            }
            container.innerHTML = '';

            const groupedResults = groupItems(results);
            // Character files are loaded newest-first and searched in worker
            // threads, neither of which is a useful display order. Keep the
            // grouped result list stable and predictable for every search.
            groupedResults.sort((left, right) => {
                const leftItem = normalizeItem(left.item);
                const rightItem = normalizeItem(right.item);
                const nameResult = leftItem.name.localeCompare(rightItem.name, undefined, {
                    sensitivity: 'base',
                    numeric: true
                });
                if (nameResult !== 0) {
                    return nameResult;
                }
                const leftRarity = rarityRanks[leftItem.rarity.toLowerCase()] ?? -1;
                const rightRarity = rarityRanks[rightItem.rarity.toLowerCase()] ?? -1;
                const rarityResult = rightRarity - leftRarity;
                if (rarityResult !== 0) {
                    return rarityResult;
                }
                return sanitizeCount(right.itemCount, 0) - sanitizeCount(left.itemCount, 0);
            });
            updateResultsCount(groupedResults.length, query);

            if (groupedResults.length === 0) {
                container.innerHTML = `
                <div class="empty-search-state">
                    <span class="material-icons">search_off</span>
                    <h3>No items found</h3>
                    <p>Try different search terms, adjust your filters, or check if you have captured character data</p>
                </div>
            `;
                return;
            }

            groupedResults.forEach(result => {
                const itemElement = document.createElement('div');
                itemElement.className = 'result-item';

                const normalizedItem = normalizeItem(result.item);
                const rarityColor = rarityColors[normalizedItem.rarity] || rarityColors['Unknown'] || '#ffffff';
                const rarityStyle = `
                background: linear-gradient(135deg, ${rarityColor}20, ${rarityColor}10);
                border: 1px solid ${rarityColor}40;
                color: ${rarityColor};
            `;
                const totalCount = Math.max(sanitizeCount(result.itemCount, 1), 1);

                const locationsHtml = (Array.isArray(result.locations) ? result.locations : []).map(loc => {
                    const stashLocations = Array.isArray(loc.stashLocations) ? loc.stashLocations : [];
                    let primaryStashId = null;
                    let primarySlotIds = [];

                    const stashesHtml = stashLocations.map(stashEntry => {
                        const stashIdStr = String(stashEntry.stashId ?? '').trim();
                        if (!primaryStashId && stashIdStr) {
                            primaryStashId = stashIdStr;
                            primarySlotIds = Array.isArray(stashEntry.slotIds) ? stashEntry.slotIds : [];
                        }
                        const quantity = sanitizeCount(stashEntry.count, 0);
                        const slotIds = Array.isArray(stashEntry.slotIds) ? stashEntry.slotIds : [];
                        const slotIdsDisplay = slotIds.length ? slotIds.join(', ') : '';
                        const slotDataset = slotIds.length ? ` data-slot-ids="${slotIds.join(',')}"` : '';
                        const slotLabel = slotIds.length ? `<span class="stash-slot">(Slot${slotIds.length > 1 ? 's' : ''}: ${escapeHtml(slotIdsDisplay)})</span>` : '';
                        const stashLabel = escapeHtml(getStashTypeDisplay(stashIdStr));
                        return `
                        <div class="stash-entry" data-char-id="${loc.id || ''}" data-stash-id="${stashIdStr}"${slotDataset}>
                            <div class="stash-location">
                                <span class="material-icons">inventory_2</span>
                                ${stashLabel}
                                <span class="stash-quantity"> - Quantity: ${quantity}</span>
                                ${slotLabel}
                            </div>
                        </div>
                    `;
                    }).join('');

                    const nameParts = [];
                    if (loc.nickname) {
                        nameParts.push(escapeHtml(loc.nickname));
                    }
                    if (loc.class) {
                        const levelPart = loc.level !== undefined && loc.level !== null && loc.level !== '' ? ` LvL ${escapeHtml(loc.level)}` : '';
                        nameParts.push(`(${escapeHtml(loc.class)}${levelPart})`);
                    }
                    let headerLabel = nameParts.join(' ').trim();
                    if (!headerLabel) {
                        if (loc.sharedStash) {
                            headerLabel = escapeHtml(result.stashType || loc.stashLabel || getStashName(primaryStashId));
                        } else {
                            headerLabel = escapeHtml(loc.stashLabel || getStashTypeDisplay(primaryStashId) || 'Unknown Location');
                        }
                    } else if (loc.sharedStash && result.stashType) {
                        headerLabel = `${escapeHtml(result.stashType)} • ${headerLabel}`;
                    }

                    const locationAttrs = [];
                    if (loc.id) {
                        locationAttrs.push(`data-char-id="${loc.id}"`);
                    }
                    if (primaryStashId) {
                        locationAttrs.push(`data-stash-id="${primaryStashId}"`);
                    }
                    if (primarySlotIds.length) {
                        locationAttrs.push(`data-slot-ids="${primarySlotIds.join(',')}"`);
                    }
                    const locationAttrString = locationAttrs.length ? ` ${locationAttrs.join(' ')}` : '';
                    const sharedClass = loc.sharedStash ? ' shared-location' : '';
                    const stashContent = stashesHtml || `
                    <div class="stash-entry disabled">
                        <div class="stash-location">
                            <span class="material-icons">info</span>
                            No stash placement details available
                        </div>
                    </div>
                `;
                    return `
                    <div class="location-info${sharedClass}"${locationAttrString}>
                        <div class="character-name">${headerLabel}</div>
                        <div class="stash-container">
                            ${stashContent}
                        </div>
                    </div>
                `;
                }).join('');

                const iconPath = normalizedItem.iconPath ? `/assets/${normalizedItem.iconPath.replaceAll('\\', '/')}` : null;

                itemElement.innerHTML = `
                <div class="item-icon-container">
                    ${iconPath ?
                        `<img src="${iconPath}" alt="${escapeHtml(normalizedItem.name)}" class="item-icon" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                         <span class="material-icons item-icon-fallback" style="display: none;">inventory_2</span>` :
                        `<span class="material-icons item-icon-fallback">inventory_2</span>`
                    }
                </div>
                <div class="item-content">
                    <div class="item-details">
                        <div class="item-name">${escapeHtml(normalizedItem.name)}</div>
                        <div class="item-meta">
                            <div class="item-rarity" style="${rarityStyle}">${escapeHtml(normalizedItem.rarity)}</div>
                            <div class="item-count">x${totalCount}</div>
                        </div>
                    </div>
                    <div class="locations-container">
                        <div class="locations-title">Found in:</div>
                        ${locationsHtml || '<div class="stash-container"><div class="stash-entry disabled"><div class="stash-location"><span class="material-icons">info</span>No stash placement details available</div></div></div>'}
                    </div>
                </div>
            `;

                itemElement.addEventListener('mouseenter', (e) => {
                    if (tooltipHideTimeout) clearTimeout(tooltipHideTimeout);

                    const rarityHex = rarityColors[normalizedItem.rarity] || rarityColors['Unknown'] || rarityColors['Common'];
                    const html = `
                    <div class="tooltip-header" style="background-color: ${rarityHex}44;">
                        <div class="tooltip-name">${escapeHtml(normalizedItem.name) || 'Unknown'}</div>
                        <div class="tooltip-rarity">${escapeHtml(normalizedItem.rarity) || 'Common'}</div>
                    </div>
                    <div class="tooltip-body">
                        <div class="tooltip-section primary-props">${formatPrimaryProps(normalizedItem.pp)}</div>
                        <div class="tooltip-section secondary-props">${formatSecondaryProps(normalizedItem.sp)}</div>
                    </div>
                    <div class="tooltip-body">
                        <div class="tooltip-section primary-props">
                            <div>Total Count: ${totalCount}</div>
                        </div>
                    </div>
                `;
                    showGlobalTooltip(html, e.clientX, e.clientY);
                });

                itemElement.addEventListener('mousemove', (e) => {
                    if (globalTooltip && globalTooltip.style.display === 'block') {
                        showGlobalTooltip(globalTooltip.innerHTML, e.clientX, e.clientY);
                    }
                });

                itemElement.addEventListener('mouseleave', () => {
                    hideGlobalTooltip();
                });

                const attachNavigationHandler = (element) => {
                    if (!element) {
                        return;
                    }
                    element.addEventListener('click', async (e) => {
                        e.stopPropagation();
                        const charId = element.dataset.charId;
                        const stashId = element.dataset.stashId;
                        if (!charId || !stashId) {
                            return;
                        }
                        const slotIds = element.dataset.slotIds;
                        const slotParam = slotIds ? `&slotIds=${encodeURIComponent(slotIds)}` : '';
                        try {
                            await fetch(`/api/character/${encodeURIComponent(charId)}/current-stash/${encodeURIComponent(stashId)}`, {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json'
                                }
                            });
                        } catch (error) {
                            console.error('Error navigating to character page:', error);
                        } finally {
                            window.location.href = `/character/${encodeURIComponent(charId)}?stashId=${encodeURIComponent(stashId)}${slotParam}`;
                        }
                    });
                };

                itemElement.querySelectorAll('.location-info').forEach(location => {
                    const stashEntries = location.querySelectorAll('.stash-entry');
                    if (stashEntries.length > 0) {
                        stashEntries.forEach(entry => attachNavigationHandler(entry));
                    } else {
                        attachNavigationHandler(location);
                    }
                });

                const popup = itemElement.querySelector('.item-popup');

                itemElement.addEventListener('mouseenter', () => {
                    if (popup) popup.style.display = 'block';
                });

                itemElement.addEventListener('mousemove', (e) => {
                    if (!popup) return;

                    const offsetX = 15;
                    const offsetY = 15;
                    const rect = itemElement.getBoundingClientRect();
                    const viewportWidth = window.innerWidth;
                    const viewportHeight = window.innerHeight;
                    const tooltipWidth = popup.offsetWidth || 200;
                    const tooltipHeight = popup.offsetHeight || 150;

                    let left = e.clientX + offsetX;
                    let top = e.clientY + offsetY;

                    if (left + tooltipWidth > viewportWidth) {
                        left = e.clientX - tooltipWidth - offsetX;
                    }
                    if (top + tooltipHeight > viewportHeight) {
                        top = e.clientY - tooltipHeight - offsetY;
                    }

                    left = Math.max(0, left);
                    top = Math.max(0, top);

                    popup.style.left = `${left - rect.left}px`;
                    popup.style.top = `${top - rect.top}px`;
                });

                itemElement.addEventListener('mouseleave', () => {
                    if (popup) popup.style.display = 'none';
                });

                container.appendChild(itemElement);
            });
        };

        const performSearch = async (query, rarity = '') => {
            const trimmedQuery = String(query || '').trim();
            const selectedRarity = String(rarity || '').trim();
            const resultLabel = [trimmedQuery, selectedRarity].filter(Boolean).join(' · ');

            if (!trimmedQuery && !selectedRarity) {
                cancelPendingSearch();
                showEmptyState();
                updateResultsCount(0, '');
                return;
            }

            const thisRequest = ++requestVersion;
            if (searchAbortController) {
                searchAbortController.abort();
                searchAbortController = null;
            }
            showLoadingState();
            console.log('Performing search for:', { query: trimmedQuery, rarity: selectedRarity });

            try {
                let details;
                if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.search_items === 'function') {
                    console.log('Using pywebview API for search');
                    details = await window.pywebview.api.search_items(trimmedQuery, selectedRarity);
                } else {
                    console.log('Using fetch API for search');
                    searchAbortController = new AbortController();
                    const params = new URLSearchParams();
                    if (trimmedQuery) params.set('query', trimmedQuery);
                    if (selectedRarity) params.set('rarity', selectedRarity);
                    const res = await fetch(`/api/search_items?${params.toString()}`, {
                        signal: searchAbortController.signal
                    });
                    if (!res.ok) {
                        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
                    }
                    details = await res.json();
                }

                // pywebview calls cannot be aborted, so also guard every
                // completion with a generation token. A slow old query must
                // never overwrite the results from a newer query or page.
                if (disposed || thisRequest !== requestVersion) {
                    return;
                }
                if (!Array.isArray(details)) {
                    throw new Error('Search returned an invalid response');
                }

                // Mark initial load as complete after first successful search
                if (isInitialLoad) {
                    isInitialLoad = false;
                }

                console.log('Search results:', details.length, 'items found');
                displayResults(details, resultLabel);
            } catch (error) {
                if (disposed || thisRequest !== requestVersion || error.name === 'AbortError') {
                    return;
                }
                console.error('Search error:', error);
                searchResults.innerHTML = `
                <div class="empty-search-state">
                    <span class="material-icons">error_outline</span>
                    <h3>Search Error</h3>
                    <p>There was an error searching your items: ${escapeHtml(error.message)}</p>
                    <p>Please try again or check the console for more details.</p>
                </div>
            `;
                updateResultsCount(0, resultLabel);
            } finally {
                if (thisRequest === requestVersion) {
                    searchAbortController = null;
                }
            }
        };    // Debounced search with improved timing and structured filters
        const debouncedSearch = () => {
            if (searchTimeout !== null) {
                window.clearTimeout(searchTimeout);
            }
            searchTimeout = window.setTimeout(() => {
                searchTimeout = null;
                if (!disposed) {
                    triggerFilteredSearch();
                }
            }, 200);
        };
        searchInput.addEventListener('input', debouncedSearch);

        // Initial filter UI state
        updateFilterUI();

        // Initial state
        showEmptyState();

        // Focus search input
        searchInput.focus();

        // Register cleanup for AJAX router
        window.__pageCleanup = window.__pageCleanup || [];
        window.__pageCleanup.push(function () {
            disposed = true;
            cancelPendingSearch();
            if (preloadAbortController) {
                preloadAbortController.abort();
                preloadAbortController = null;
            }
            if (tooltipHideTimeout !== null) {
                window.clearTimeout(tooltipHideTimeout);
                tooltipHideTimeout = null;
            }
            searchInput.removeEventListener('input', debouncedSearch);
            searchInput.removeEventListener('input', handleSearchInput);
            clearSearch.removeEventListener('click', handleClearSearch);
            if (filterRarity) {
                filterRarity.removeEventListener('change', handleRarityChange);
            }
            if (globalTooltip && globalTooltip.parentNode) {
                globalTooltip.parentNode.removeChild(globalTooltip);
                globalTooltip = null;
            }
        });
    }

    if (document.readyState !== 'loading') {
        searchPageInit();
    } else {
        document.addEventListener('DOMContentLoaded', searchPageInit, { once: true });
    }
})();
})();
