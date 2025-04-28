let searchTimeout;

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

    // First pass: Process all items, but handle shared stashes separately
    results.forEach(result => {
        const key = getItemKey(result.item);
        const isShared = isSharedStash(result.stash_id);

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
            // Regular character-specific stash item
            if (!groupedItems.has(key)) {
                groupedItems.set(key, {
                    ...result,
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
                const existingItem = groupedItems.get(key);
                existingItem.itemCount += result.itemCount;
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

    const displayResults = (results) => {
        const container = document.getElementById('searchResults');
        container.innerHTML = '';

        if (results.length === 0) {
            container.innerHTML = '<div class="loading">No items found</div>';
            return;
        }

        const groupedResults = groupItems(results);

        groupedResults.forEach(result => {
            const item = document.createElement('div');
            item.className = 'result-item';
            const rarityColor = rarityColors[result.item.rarity] || '#ffffff';

            // Create location info HTML based on whether it's a shared stash or not
            let locationsHtml = '';
            if (result.stashType) {
                // This is a shared stash item
                locationsHtml = result.locations.map(loc =>
                    `<div class="location-info" data-char-id="${loc.id}" data-stash-id="${loc.stash_id}">
                        ${createStashLink(loc.id, loc.stash_id, loc.slotId)}
                    </div>`
                ).join('');
            } else {
                // Regular character stash items
                locationsHtml = result.locations.map(loc =>
                    `<div class="location-info" data-char-id="${loc.id}" data-stash-id="${loc.stash_id}">
                        <div class="character-name">${loc.nickname} (${loc.class} LvL ${loc.level})</div>
                        <div class="stash-location">
                            ${getStashTypeDisplay(loc.stash_id)} (Slot: ${loc.slotId})
                        </div>
                    </div>`
                ).join('');
            }

            item.innerHTML = `
                <div class="locations-container">
                    <div class="locations-title">Found in:</div>
                    ${locationsHtml}
                </div>
                <div class="character-info">
                    <div class="item-name">${result.item.name}</div>
                    <div class="item-rarity">${result.item.rarity}</div>
                    <div class="item-count">
                        ${result.itemCount}
                    </div>
                </div>
                <div class="item-popup" style="display: none; z-index: 100;">
                    <div class="item-header" style="background-color: ${rarityColor}; color: #000;">${result.item.name}</div>
                    <div class="item-properties">
                        <div class="primary-props">${formatPrimaryProps(result.item.pp)}</div>
                        <div class="secondary-props">${formatSecondaryProps(result.item.sp)}</div>
                    </div>
                    <div class="item-meta">
                        <div>Rarity: ${result.item.rarity}</div>
                        <div>Total Count: ${result.itemCount}</div>
                    </div>
                </div>
            `;

            // Add click handler for location info sections
            item.querySelectorAll('.location-info').forEach(location => {
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
        searchResults.innerHTML = '<div class="loading">Searching...</div>';

        try {
            let details;
            if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.search_items === 'function') {
                details = await window.pywebview.api.search_items(query);
            } else {
                const res = await fetch(`/api/search_items?query=${encodeURIComponent(query)}`);
                details = await res.json();
            }

            displayResults(details);
        } catch (error) {
            searchResults.innerHTML = '<div class="loading">Error searching items. Please try again.</div>';
            console.error(error);
        }
    };

    searchInput.addEventListener('input', debounce((e) => performSearch(e.target.value), 500));
    performSearch('');
});
