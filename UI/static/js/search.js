let searchTimeout;
let globalTooltip;
let tooltipHideTimeout;

const rarityColors = {
    'None': '#808080',      // Gray
    'Poor': '#969696',      // Light Gray
    'Common': '#FFFFFF',    // White
    'Uncommon': '#00FF00',  // Green
    'Rare': '#0070DD',      // Blue
    'Epic': '#A335EE',      // Purple
    'Legend': '#FF8000',    // Orange
    'Legendary': '#FF8000', // Orange (alternate name)
    'Unique': '#FFD700',    // Gold
    'Artifact': '#FF0000'   // Red
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
    return ppArray.map(([name, value]) => `<div>${name} ${value}</div>`).join('');
}

function formatSecondaryProps(spArray) {
    return spArray.map(([name, value]) => {
        const sign = value >= 0 ? '+' : '';
        return `<div>${sign}${value} ${name}</div>`;
    }).join('');
}

// Helper function to generate a unique key for an item
function getItemKey(item) {
    return `${item.name}-${item.rarity}-${JSON.stringify(item.pp)}-${JSON.stringify(item.sp)}`;
}

// Helper function to check if a stash is shared
function isSharedStash(stashId) {
    return stashId === "20" || stashId === "30";
}

// Get a friendly name for a stash type
function getStashName(stashId) {
    const stashTypes = {
        "20": 'Shared Stash',
        "30": 'Seasonal Stash'
    };
    return stashTypes[stashId] || `Stash ${stashId}`;
}

// Helper function to group identical items
function groupItems(results) {
    const groupedItems = new Map();
    const sharedStashItems = new Map();
    const characterItems = new Map(); // Map to group items by character first

    // First pass: Process all items, but handle shared stashes separately
    results.forEach(result => {
        const key = getItemKey(result.item);
        const isShared = isSharedStash(result.stash_id);
        const charKey = result.id; // Character ID

        if (isShared) {
            // For shared stash items, track them separately first
            if (!sharedStashItems.has(key)) {
                sharedStashItems.set(key, {
                    stashType: getStashName(result.stash_id),
                    stashId: result.stash_id,
                    item: result.item,
                    itemCount: result.itemCount,
                    locations: [{
                        nickname: result.nickname,
                        class: result.class,
                        level: result.level,
                        slotId: result.slotId,
                        id: result.id,
                        stash_id: result.stash_id
                    }]
                });
            } else {
                // Don't count duplicate shared stash items from multiple characters
                // Just ensure we have the location info
                const existingItem = sharedStashItems.get(key);
                if (!existingItem.locations.some(loc => loc.id === result.id)) {
                    existingItem.locations.push({
                        nickname: result.nickname,
                        class: result.class,
                        level: result.level,
                        slotId: result.slotId,
                        id: result.id,
                        stash_id: result.stash_id
                    });
                }
            }
        } else {
            // Group by character first
            if (!characterItems.has(charKey)) {
                characterItems.set(charKey, new Map());
            }

            const charItemMap = characterItems.get(charKey);

            if (!charItemMap.has(key)) {
                charItemMap.set(key, {
                    ...result,
                    stashLocations: [{
                        stashId: result.stash_id,
                        slotId: result.slotId,
                        count: result.itemCount
                    }]
                });
            } else {
                const existingItem = charItemMap.get(key);

                // Check if this stash already exists
                const existingStash = existingItem.stashLocations.find(
                    s => s.stashId === result.stash_id
                );

                if (existingStash) {
                    existingStash.count += result.itemCount;
                } else {
                    existingItem.stashLocations.push({
                        stashId: result.stash_id,
                        slotId: result.slotId,
                        count: result.itemCount
                    });
                }
                existingItem.itemCount += result.itemCount;
            }
        }
    });

    // Second pass: Flatten character items into the main map
    characterItems.forEach((itemMap, charId) => {
        itemMap.forEach((charItem, itemKey) => {
            if (!groupedItems.has(itemKey)) {
                groupedItems.set(itemKey, {
                    ...charItem,
                    locations: [{
                        nickname: charItem.nickname,
                        class: charItem.class,
                        level: charItem.level,
                        id: charItem.id,
                        stashLocations: charItem.stashLocations
                    }]
                });
            } else {
                const existingItem = groupedItems.get(itemKey);
                existingItem.itemCount += charItem.itemCount;
                existingItem.locations.push({
                    nickname: charItem.nickname,
                    class: charItem.class,
                    level: charItem.level,
                    id: charItem.id,
                    stashLocations: charItem.stashLocations
                });
            }
        });
    });

    // Convert to array and combine with shared stash items
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
        20: 'Shared Stash',
        30: 'Shared Stash Seasonal'
    };
    return stashTypes[stashId] || `Stash ${stashId}`;
}

// Helper function to create a direct stash link
function createStashLink(charId, stashId, slotId) {
    return `<span class="stash-link" data-stash-id="${stashId}" data-slot-id="${slotId}">
        ${getStashTypeDisplay(stashId)} (Slot: ${slotId})
    </span>`;
}

const debounce = (func, wait) => {
    return (...args) => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => func(...args), wait);
    };
};

window.addEventListener('load', () => {
    const searchInput = document.getElementById('searchInput');
    const searchResults = document.getElementById('searchResults');
    const clearSearch = document.getElementById('clearSearch');
    const searchMeta = document.getElementById('searchMeta');
    const resultsCount = document.getElementById('resultsCount');

    // Clear search functionality
    clearSearch.addEventListener('click', () => {
        searchInput.value = '';
        clearSearch.style.display = 'none';
        searchMeta.textContent = '';
        resultsCount.textContent = '';
        showEmptyState();
        searchInput.focus();
    });

    // Show/hide clear button based on input
    searchInput.addEventListener('input', (e) => {
        const value = e.target.value.trim();
        clearSearch.style.display = value ? 'flex' : 'none';

        if (!value) {
            searchMeta.textContent = '';
            resultsCount.textContent = '';
        }
    });

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
        searchResults.innerHTML = `
            <div class="loading">
                <span class="material-icons">hourglass_empty</span>
                Searching your character stashes...
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
        container.innerHTML = '';

        const groupedResults = groupItems(results);
        updateResultsCount(groupedResults.length, query);

        if (groupedResults.length === 0) {
            container.innerHTML = `
                <div class="empty-search-state">
                    <span class="material-icons">search_off</span>
                    <h3>No items found</h3>
                    <p>Try different search terms or check if you have captured character data</p>
                </div>
            `;
            return;
        }

        groupedResults.forEach(result => {
            const item = document.createElement('div');
            item.className = 'result-item';
            const rarityColor = rarityColors[result.item.rarity] || '#ffffff';

            // Set rarity styling
            const rarityStyle = `
                background: linear-gradient(135deg, ${rarityColor}20, ${rarityColor}10);
                border: 1px solid ${rarityColor}40;
                color: ${rarityColor};
            `;

            // Create location info HTML based on whether it's a shared stash or not
            let locationsHtml = '';
            if (result.stashType) {
                // This is a shared stash item
                locationsHtml = `
                    <div class="location-info" data-stash-id="${result.stashId}">
                        <div class="character-name">${result.stashType}</div>
                        <div class="stash-location">
                            <span class="material-icons">inventory_2</span>
                            Quantity: ${result.itemCount}
                        </div>
                    </div>
                `;
            } else {
                // Regular character stash items
                locationsHtml = result.locations.map(loc => {
                    // For each character, create a section with all stash locations
                    const stashesHtml = loc.stashLocations.map(stash =>
                        `<div class="stash-entry" data-char-id="${loc.id}" data-stash-id="${stash.stashId}">
                            <div class="stash-location">
                                <span class="material-icons">inventory_2</span>
                                ${getStashTypeDisplay(stash.stashId)} - Quantity: ${stash.count}
                            </div>
                        </div>`
                    ).join('');

                    return `
                        <div class="location-info">
                            <div class="character-name">${loc.nickname} (${loc.class} LvL ${loc.level})</div>
                            <div class="stash-container">
                                ${stashesHtml}
                            </div>
                        </div>
                    `;
                }).join('');
            } item.innerHTML = `
                <div class="locations-container">
                    <div class="locations-title">Found in:</div>
                    ${locationsHtml}
                </div>
                <div class="character-info">
                    <div class="item-name">${result.item.name}</div>
                    <div class="item-rarity" style="${rarityStyle}">${result.item.rarity}</div>
                    <div class="item-count">${result.itemCount}</div>
                </div>
            `;

            // Add tooltip functionality using global tooltip system
            item.addEventListener('mouseenter', (e) => {
                if (tooltipHideTimeout) clearTimeout(tooltipHideTimeout);

                const rarityColor = rarityColors[result.item.rarity] || rarityColors['Common'];
                const html = `
                    <div class="tooltip-header" style="background-color: ${rarityColor}44;">
                        <div class="tooltip-name">${result.item.name || 'Unknown'}</div>
                        <div class="tooltip-rarity">${result.item.rarity || 'Common'}</div>
                    </div>
                    <div class="tooltip-body">
                        <div class="tooltip-section primary-props">${formatPrimaryProps(result.item.pp)}</div>
                        <div class="tooltip-section secondary-props">${formatSecondaryProps(result.item.sp)}</div>
                    </div>                    <div class="tooltip-body">
                        <div class="tooltip-section primary-props">
                            <div>Total Count: ${result.itemCount}</div>
                        </div>
                    </div>
                `;
                showGlobalTooltip(html, e.clientX, e.clientY);
            });

            item.addEventListener('mousemove', (e) => {
                if (globalTooltip && globalTooltip.style.display === 'block') {
                    showGlobalTooltip(globalTooltip.innerHTML, e.clientX, e.clientY);
                }
            });

            item.addEventListener('mouseleave', () => {
                hideGlobalTooltip();
            });

            // Add click handler for location info sections
            item.querySelectorAll('.location-info').forEach(location => {
                const stashEntries = location.querySelectorAll('.stash-entry');

                // If this is a shared stash or has stash entries, make them clickable
                if (stashEntries.length > 0) {
                    stashEntries.forEach(stashEntry => {
                        stashEntry.addEventListener('click', async (e) => {
                            e.stopPropagation();
                            const charId = stashEntry.dataset.charId;
                            const stashId = stashEntry.dataset.stashId;

                            try {
                                // Set current stash before navigation
                                await fetch(`/api/character/${charId}/current-stash/${stashId}`, {
                                    method: 'POST',
                                    headers: {
                                        'Content-Type': 'application/json'
                                    }
                                });
                                // Navigate with stashId as URL parameter
                                window.location.href = `/character/${charId}?stashId=${stashId}`;
                            } catch (error) {
                                console.error("Error navigating to character page:", error);
                                // If there's an error, still try to navigate with the stash parameter
                                window.location.href = `/character/${charId}?stashId=${stashId}`;
                            }
                        });
                    });
                } else {
                    location.addEventListener('click', async (e) => {
                        e.stopPropagation();
                        const charId = location.dataset.charId;
                        const stashId = location.dataset.stashId;

                        try {
                            // Set current stash before navigation
                            await fetch(`/api/character/${charId}/current-stash/${stashId}`, {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json'
                                }
                            });
                            // Navigate with stashId as URL parameter
                            window.location.href = `/character/${charId}?stashId=${stashId}`;
                        } catch (error) {
                            console.error("Error navigating to character page:", error);
                            // If there's an error, still try to navigate with the stash parameter
                            window.location.href = `/character/${charId}?stashId=${stashId}`;
                        }
                    });
                }
            });

            // Add event listeners for mouse interactions for item popup
            const popup = item.querySelector('.item-popup');

            item.addEventListener('mouseenter', (e) => {
                if (popup) popup.style.display = 'block';
            });

            item.addEventListener('mousemove', (e) => {
                if (!popup) return;

                const offsetX = 15;
                const offsetY = 15;
                const rect = item.getBoundingClientRect();
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

            item.addEventListener('mouseleave', () => {
                if (popup) popup.style.display = 'none';
            });

            container.appendChild(item);
        });
    };

    const performSearch = async (query) => {
        const trimmedQuery = query.trim();

        if (!trimmedQuery) {
            showEmptyState();
            updateResultsCount(0, '');
            return;
        }

        showLoadingState();

        try {
            let details;
            if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.search_items === 'function') {
                details = await window.pywebview.api.search_items(trimmedQuery);
            } else {
                const res = await fetch(`/api/search_items?query=${encodeURIComponent(trimmedQuery)}`);
                details = await res.json();
            }

            displayResults(details, trimmedQuery);
        } catch (error) {
            searchResults.innerHTML = `
                <div class="empty-search-state">
                    <span class="material-icons">error_outline</span>
                    <h3>Search Error</h3>
                    <p>There was an error searching your items. Please try again.</p>
                </div>
            `;
            updateResultsCount(0, trimmedQuery);
            console.error('Search error:', error);
        }
    };    // Debounced search with improved timing
    const debouncedSearch = debounce((e) => performSearch(e.target.value), 200); // Reduced from 300ms
    searchInput.addEventListener('input', debouncedSearch);

    // Initial state
    showEmptyState();

    // Focus search input
    searchInput.focus();
});
