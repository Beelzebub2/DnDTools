function formatDate(dateString) {
    if (!dateString) return 'Unknown';
    try {
        const date = new Date(dateString);
        if (isNaN(date.getTime())) return dateString;
        return date.toLocaleDateString();
    } catch (e) {
        return dateString;
    }
}

function handleApiError(error, element) {
    console.error('API Error:', error);
    element.innerHTML = `
        <div class="error-state">
            <span class="material-icons">error_outline</span>
            <h3>Error Loading Data</h3>
            <p>${error.toString()}</p>
        </div>`;
}

const charId = window.location.pathname.split('/').pop();
let abortController = null;
let currentStashId = null;  // Track current stash ID

// Rarity colors - same as in search.js for consistency
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

// Format functions - same as in search.js for consistency
function formatPrimaryProps(ppArray) {
    if (!ppArray || !Array.isArray(ppArray)) return '';
    return ppArray.map(([name, value]) => `<div>${name} ${value}</div>`).join('');
}

function formatSecondaryProps(spArray) {
    if (!spArray || !Array.isArray(spArray)) return '';
    return spArray.map(([name, value]) => {
        const sign = value >= 0 ? '+' : '';
        return `<div>${sign}${value} ${name}</div>`;
    }).join('');
}

const updateCharacterInfo = async (characterId) => {
    const charInfo = document.getElementById('characterInfo');
    const charHeader = document.getElementById('characterHeader');
    try {
        let details;
        // Try pywebview API if available and get_character_details is a function
        if (
            window.pywebview &&
            window.pywebview.api &&
            typeof window.pywebview.api.get_character_details === 'function'
        ) {
            details = await window.pywebview.api.get_character_details(characterId);
        } else {
            // Fallback to REST API
            const res = await fetch(`/api/character/${characterId}/details`);
            details = await res.json();
        }
        charHeader.textContent = details.nickname;
        charInfo.innerHTML = `
            <div class="char-info-grid">
                <div class="char-info-item">
                    <h1 class="character-name">${details.nickname}</h1>
                    <div class="character-subtitle">Level ${details.level} ${details.class}</div>
                </div>
                <div class="char-info-item">
                    <div class="info-label">Total Items</div>
                    <div class="info-value">${details.totalItems}</div>
                </div>
                <div class="char-info-item">
                    <div class="info-label">Stash Count</div>
                    <div class="info-value">${details.stashCount}</div>
                </div>
                <div class="char-info-item">
                    <div class="info-label">Last Updated</div>
                    <div class="info-value">${formatDate(details.lastUpdate)}</div>
                </div>
            </div>
        `;
    } catch (error) {
        handleApiError(error, charInfo);
    }
};

const getStashName = (stashId) => {
    // Map stash IDs to their proper names based on StashType enum
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
};

// Get stash dimensions based on stash type
const getStashDimensions = (stashId) => {
    const stashIdInt = parseInt(stashId, 10);

    // Equipment has a special layout
    if (stashIdInt === 3) {
        return [10, 10]; // Wider format for equipment layout
    }

    // BAG is smaller
    if (stashIdInt === 2) {
        return [10, 5];
    }

    // Standard stash dimensions for storage, shared stash, etc
    return [12, 20];
};

// Process stash data from API into a useful format for grid display
const processStashData = async (stashData, stashId) => {
    // Check if we're working with the new enhanced API response format
    if (stashData && typeof stashData === 'object' && stashData.stashData) {
        // New format: we have detailed item data directly from the API
        return stashData.stashData[stashId] || [];
    }

    // If we got a string (old format - image URL), fetch detailed data
    if (typeof stashData === 'string') {
        try {
            // Extract character ID from the image URL if possible
            let charId = '';
            if (stashData.includes('/output/stash_preview_')) {
                const match = stashData.match(/stash_preview_(\d+)_/);
                if (match && match[1]) {
                    charId = match[1];
                }
            }

            // Fetch the character details from the API
            const res = await fetch(`/api/character/${charId || window.location.pathname.split('/').pop()}/details`);
            const details = await res.json();

            // Look up the stash content from the details
            if (details.stashes && details.stashes[stashId]) {
                // Convert stash content to grid items
                return details.stashes[stashId].map(item => {
                    const rarity = item.rarity || 'Common';
                    return {
                        name: item.name,
                        slotId: item.slotId,
                        itemId: item.itemId,
                        itemCount: item.itemCount || 1,
                        rarity: rarity,
                        pp: item.pp || [],
                        sp: item.sp || [],
                        width: item.width || 1,
                        height: item.height || 1
                    };
                });
            }

            console.warn('No stash data found for', stashId, 'in character details');
            return [];
        } catch (error) {
            console.error('Error fetching stash details:', error);
            return [];
        }
    }

    // Fallback for unknown format
    console.warn('Unknown stash data format', stashData);
    return [];
};

// Render the interactive grid for a stash
const renderInteractiveGrid = (stashId, items) => {
    const gridContainer = document.getElementById('interactiveStashGrid');
    if (!gridContainer) return;

    // Clear existing content
    gridContainer.innerHTML = '';

    // Get grid dimensions based on stash type
    const [gridWidth, gridHeight] = getStashDimensions(stashId);
    const isEquipment = parseInt(stashId, 10) === 3;

    // Create grid container
    const grid = document.createElement('div');
    grid.className = 'interactive-stash-grid';
    grid.style.gridTemplateColumns = `repeat(${gridWidth}, 45px)`;
    grid.style.gridTemplateRows = `repeat(${gridHeight}, 45px)`;

    // Add empty cells for the grid background
    if (!isEquipment) {
        for (let y = 0; y < gridHeight; y++) {
            for (let x = 0; x < gridWidth; x++) {
                const cell = document.createElement('div');
                cell.className = 'stash-grid-cell';
                cell.style.gridColumn = `${x + 1}`;
                cell.style.gridRow = `${y + 1}`;
                grid.appendChild(cell);
            }
        }
    } else {
        // Special handling for equipment screen with predefined slots
        const equipmentSlotPositions = {
            0: { x: 0, y: 0, w: 2, h: 4 },  // Weapon slot 1 (left)
            1: { x: 8, y: 0, w: 2, h: 4 },  // Weapon slot 2 (right)
            2: { x: 3, y: 0, w: 2, h: 2 },  // Helmet slot
            7: { x: 5, y: 0, w: 2, h: 2 },  // Necklace/amulet slot
            3: { x: 3, y: 2, w: 4, h: 3 },  // Body armor
            8: { x: 2, y: 3, w: 1, h: 1 },  // Left ring
            9: { x: 7, y: 3, w: 1, h: 1 },  // Right ring
            6: { x: 2, y: 5, w: 2, h: 2 },  // Gloves (left side)
            4: { x: 3, y: 5, w: 4, h: 3 },  // Pants/leggings (center)
            10: { x: 6, y: 5, w: 2, h: 2 }, // Cape/cloak (right side)
            5: { x: 4, y: 8, w: 2, h: 2 },  // Boots (center bottom)
            20: { x: 0, y: 5, w: 1, h: 1 }, // And so on for consumable slots
            21: { x: 1, y: 5, w: 1, h: 1 },
            22: { x: 0, y: 6, w: 1, h: 1 },
            23: { x: 1, y: 6, w: 1, h: 1 },
            24: { x: 0, y: 7, w: 1, h: 1 },
            25: { x: 1, y: 7, w: 1, h: 1 },
            26: { x: 8, y: 5, w: 1, h: 1 },
            27: { x: 9, y: 5, w: 1, h: 1 },
            28: { x: 8, y: 6, w: 1, h: 1 },
            29: { x: 9, y: 6, w: 1, h: 1 },
            30: { x: 8, y: 7, w: 1, h: 1 },
            31: { x: 9, y: 7, w: 1, h: 1 }
        };

        // Create slot backgrounds for equipment layout
        for (const [slotId, pos] of Object.entries(equipmentSlotPositions)) {
            const slotCell = document.createElement('div');
            slotCell.className = 'equipment-slot';
            slotCell.style.gridColumn = `${pos.x + 1} / span ${pos.w}`;
            slotCell.style.gridRow = `${pos.y + 1} / span ${pos.h}`;
            slotCell.dataset.slotId = slotId;
            grid.appendChild(slotCell);
        }
    }

    // Add items to the grid
    if (items && items.length) {
        items.forEach(item => {
            if (!item) return;

            let x, y, w, h;

            if (isEquipment) {
                // For equipment, use predefined positions
                const equipmentSlotPositions = {
                    0: { x: 0, y: 0, w: 2, h: 4 },  // Weapon slot 1 (left)
                    1: { x: 8, y: 0, w: 2, h: 4 },  // Weapon slot 2 (right)
                    2: { x: 3, y: 0, w: 2, h: 2 },  // Helmet slot
                    7: { x: 5, y: 0, w: 2, h: 2 },  // Necklace/amulet slot
                    3: { x: 3, y: 2, w: 4, h: 3 },  // Body armor
                    8: { x: 2, y: 3, w: 1, h: 1 },  // Left ring
                    9: { x: 7, y: 3, w: 1, h: 1 },  // Right ring
                    6: { x: 2, y: 5, w: 2, h: 2 },  // Gloves (left side)
                    4: { x: 3, y: 5, w: 4, h: 3 },  // Pants/leggings (center)
                    10: { x: 6, y: 5, w: 2, h: 2 }, // Cape/cloak (right side)
                    5: { x: 4, y: 8, w: 2, h: 2 },  // Boots (center bottom)
                    20: { x: 0, y: 5, w: 1, h: 1 }, // And so on for consumable slots
                    21: { x: 1, y: 5, w: 1, h: 1 },
                    22: { x: 0, y: 6, w: 1, h: 1 },
                    23: { x: 1, y: 6, w: 1, h: 1 },
                    24: { x: 0, y: 7, w: 1, h: 1 },
                    25: { x: 1, y: 7, w: 1, h: 1 },
                    26: { x: 8, y: 5, w: 1, h: 1 },
                    27: { x: 9, y: 5, w: 1, h: 1 },
                    28: { x: 8, y: 6, w: 1, h: 1 },
                    29: { x: 9, y: 6, w: 1, h: 1 },
                    30: { x: 8, y: 7, w: 1, h: 1 },
                    31: { x: 9, y: 7, w: 1, h: 1 }
                };

                const pos = equipmentSlotPositions[item.slotId];
                if (!pos) return; // Skip if no position defined

                x = pos.x;
                y = pos.y;
                w = pos.w;
                h = pos.h;
            } else {
                // For regular stash, calculate position based on slotId
                x = item.slotId % gridWidth;
                y = Math.floor(item.slotId / gridWidth);
                w = item.width || 1;
                h = item.height || 1;
            }

            // Create item element
            const itemEl = document.createElement('div');
            itemEl.className = 'stash-item';
            itemEl.style.gridColumn = `${x + 1} / span ${w}`;
            itemEl.style.gridRow = `${y + 1} / span ${h}`;

            // Apply rarity-based border color
            const rarityColor = rarityColors[item.rarity] || rarityColors['Common'];
            itemEl.style.borderColor = rarityColor;

            // Create inset border with box-shadow instead of background color
            itemEl.style.boxShadow = `inset 0 0 0 1px rgba(0,0,0,0.3), 0 0 0 1px ${rarityColor}30, inset 0 0 5px ${rarityColor}40`;

            // Apply background color based on rarity with more subtle transparency
            itemEl.style.backgroundColor = `${rarityColor}15`;  // 15 is hex for ~8% opacity

            // If we have an image path, use it, otherwise show text
            if (item.imagePath) {
                const img = document.createElement('img');
                img.src = item.imagePath;
                img.alt = item.name || 'Item';
                img.className = 'item-image';
                itemEl.appendChild(img);
            } else {
                // No image, just display the name
                itemEl.textContent = item.name || 'Unknown';
            }

            // Add count badge if more than 1
            if (item.itemCount > 1) {
                const countBadge = document.createElement('div');
                countBadge.className = 'item-count-badge';
                countBadge.textContent = item.itemCount;
                itemEl.appendChild(countBadge);
            }

            // Create tooltip (hidden until hover)
            const tooltip = document.createElement('div');
            tooltip.className = 'item-tooltip';

            // Build tooltip content with style matching search.js
            tooltip.innerHTML = `
                <div class="tooltip-header" style="background-color: ${rarityColor}44;">
                    <div class="tooltip-name">${item.name || 'Unknown'}</div>
                    <div class="tooltip-rarity">${item.rarity || 'Common'}</div>
                </div>
                <div class="tooltip-body">
                    <div class="tooltip-section primary-props">
                        ${formatPrimaryProps(item.pp)}
                    </div>
                    <div class="tooltip-section secondary-props">
                        ${formatSecondaryProps(item.sp)}
                    </div>
                </div>
            `;

            // Attach tooltip to item
            itemEl.appendChild(tooltip);

            // Mouse event handlers for tooltip positioning
            itemEl.addEventListener('mouseenter', (e) => {
                // Get tooltip and ensure it's visible but starting with opacity 0
                tooltip.style.display = 'block';

                // Move tooltip to body to avoid any potential containment issues
                document.body.appendChild(tooltip);

                // Initial positioning
                const rect = itemEl.getBoundingClientRect();
                const tooltipWidth = tooltip.offsetWidth || 250;
                const tooltipHeight = tooltip.offsetHeight || 150;

                let left = e.clientX + 15;
                let top = e.clientY + 15;

                // Make sure tooltip doesn't go outside viewport
                if (left + tooltipWidth > window.innerWidth) {
                    left = e.clientX - tooltipWidth - 15;
                }

                if (top + tooltipHeight > window.innerHeight) {
                    top = e.clientY - tooltipHeight - 15;
                }

                // Position the tooltip
                tooltip.style.left = `${left}px`;
                tooltip.style.top = `${top}px`;

                // Use setTimeout to trigger animation after append
                setTimeout(() => {
                    tooltip.classList.add('visible');
                }, 10);
            });

            itemEl.addEventListener('mouseleave', () => {
                tooltip.classList.remove('visible');

                // Hide after fade out animation completes
                setTimeout(() => {
                    tooltip.style.display = 'none';
                }, 200);
            });

            itemEl.addEventListener('mousemove', (e) => {
                // For fixed positioning, we need to use clientX/clientY coordinates directly
                const offsetX = 15;
                const offsetY = 15;

                // Get dimensions for positioning logic
                const tooltipWidth = tooltip.offsetWidth || 250;
                const tooltipHeight = tooltip.offsetHeight || 150;

                // Default position
                let left = e.clientX + offsetX;
                let top = e.clientY + offsetY;

                // Make sure tooltip doesn't go outside viewport
                if (left + tooltipWidth > window.innerWidth) {
                    left = e.clientX - tooltipWidth - offsetX;
                }

                if (top + tooltipHeight > window.innerHeight) {
                    top = e.clientY - tooltipHeight - offsetY;
                }

                // Position the tooltip
                tooltip.style.left = `${left}px`;
                tooltip.style.top = `${top}px`;
            });

            grid.appendChild(itemEl);
        });
    }

    gridContainer.appendChild(grid);
};

const createStashTabs = (stashes) => {
    const selector = document.getElementById('stashSelector');
    const preview = document.getElementById('currentStashPreview');
    const gridContainer = document.getElementById('interactiveStashGrid');
    const sortButton = document.querySelector('.sort-button');
    selector.innerHTML = '';

    // Ensure stashes is an object
    const stashesObj = stashes || {};
    const stashKeys = Object.keys(stashesObj);
    let firstStashUrl = null;

    stashKeys.forEach((stashId, index) => {
        const tab = document.createElement('div');
        tab.className = 'stash-tab';
        if (index === 0) {
            tab.classList.add('active');
            // Save the image URL for any backward compatibility needs
            if (stashes.previewImages) {
                firstStashUrl = stashes.previewImages[stashId];
            } else {
                firstStashUrl = stashes[stashId];
            }
            currentStashId = stashId;
            // Set initial stash
            updateCurrentStash(stashId);

            // For the first stash, immediately try to load and render the interactive grid
            processStashData(stashes, stashId).then(items => {
                renderInteractiveGrid(stashId, items);
            });
        }

        tab.textContent = getStashName(parseInt(stashId));
        tab.dataset.stashId = stashId;
        tab.onclick = (e) => {
            document.querySelectorAll('.stash-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            // Set image source for backward compatibility
            if (stashes.previewImages) {
                preview.src = stashes.previewImages[stashId];
            } else {
                preview.src = stashes[stashId];
            }

            currentStashId = stashId;
            updateCurrentStash(stashId);

            // Load and render the interactive grid for this stash
            processStashData(stashes, stashId).then(items => {
                renderInteractiveGrid(stashId, items);
            });
        };
        selector.appendChild(tab);
    });

    // Set up sort button click handler
    sortButton.onclick = () => triggerSort();

    return firstStashUrl;
};

const createStashTabsWithoutDefault = (stashes) => {
    const selector = document.getElementById('stashSelector');
    const preview = document.getElementById('currentStashPreview');
    const gridContainer = document.getElementById('interactiveStashGrid');
    const sortButton = document.querySelector('.sort-button');
    selector.innerHTML = '';

    // Ensure stashes is an object
    const stashesObj = stashes || {};

    // Determine if we're working with the new or old API format
    const isNewFormat = stashes.previewImages && stashes.stashData;

    // Get stash IDs - either from the new format's previewImages or directly from the object keys
    const stashKeys = isNewFormat
        ? Object.keys(stashes.previewImages || {})
        : Object.keys(stashesObj);

    let firstStashUrl = null;

    stashKeys.forEach((stashId, index) => {
        const tab = document.createElement('div');
        tab.className = 'stash-tab';

        // Just store the first URL for fallback
        if (index === 0) {
            if (isNewFormat) {
                firstStashUrl = stashes.previewImages[stashId];
            } else {
                firstStashUrl = stashes[stashId];
            }
        }

        tab.textContent = getStashName(parseInt(stashId));
        tab.dataset.stashId = stashId;
        tab.onclick = (e) => {
            document.querySelectorAll('.stash-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            // Set image source for backward compatibility
            if (isNewFormat) {
                preview.src = stashes.previewImages[stashId];
            } else {
                preview.src = stashes[stashId];
            }

            currentStashId = stashId;
            updateCurrentStash(stashId);

            // Load and render the interactive grid for this stash
            processStashData(stashes, stashId).then(items => {
                renderInteractiveGrid(stashId, items);
            });
        };
        selector.appendChild(tab);
    });

    // Set up sort button click handler
    sortButton.onclick = () => triggerSort();

    return firstStashUrl;
};

const updateCurrentStash = async (stashId) => {
    try {
        await fetch(`/api/character/${charId}/current-stash/${stashId}`, {
            method: 'POST'
        });
        console.log(`Current stash updated to: ${stashId}`);
    } catch (error) {
        console.error('Error updating current stash:', error);
    }
};

const triggerSort = async () => {
    if (!currentStashId) return;

    // prepare abort controller
    if (abortController) abortController.abort();
    abortController = new AbortController();

    const sortButton = document.querySelector('.sort-button');
    setSortingState(true);

    try {
        const response = await fetch(`/api/character/${charId}/stash/${currentStashId}/sort`, {
            method: 'POST',
            signal: abortController.signal
        });
        const result = await response.json();

        if (result.success) {
            await loadStashes();
            showNotification('Stash sorted successfully', 'success');
        } else {
            const errorMessage = result.error || 'Failed to sort stash. The stash might be full.';
            showNotification(errorMessage, 'error');
        }
    } catch (error) {
        if (error.name === 'AbortError') {
            showNotification('Sorting cancelled', 'info');
        } else {
            console.error('Error sorting stash:', error);
            showNotification('Network error while sorting stash', 'error');
        }
    } finally {
        setSortingState(false);
        abortController = null;
    }
};

function setSortingState(isSorting) {
    const sortButton = document.querySelector('.sort-button');
    if (!sortButton) return;

    sortButton.disabled = isSorting;
    if (isSorting) {
        sortButton.classList.add('sorting');
        sortButton.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M2 4h12M4 8h8M6 12h4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
            Sorting...
        `;
    } else {
        sortButton.classList.remove('sorting');
        sortButton.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M2 4h12M4 8h8M6 12h4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
            Sort Stash
        `;
    }
}

const loadStashes = async () => {
    const spinner = document.getElementById('stashSpinner');
    const selector = document.getElementById('stashSelector');
    const previewContainer = document.getElementById('stashPreview');
    const previewImage = document.getElementById('currentStashPreview');
    const gridContainer = document.getElementById('interactiveStashGrid');

    if (!spinner || !selector || !previewContainer || !previewImage) {
        console.error('Required DOM elements not found for stash display');
        return;
    }

    // show spinner, hide stash content
    spinner.classList.remove('hidden');
    selector.classList.add('hidden');
    previewContainer.classList.add('hidden');
    previewImage.src = "";
    if (gridContainer) gridContainer.innerHTML = "";

    try {
        // First, check if there's a currently selected stash ID on the server
        let currentStashData = null;
        try {
            const currentStashResponse = await fetch(`/api/character/${charId}/current-stash`);
            currentStashData = await currentStashResponse.json();

            if (currentStashData && currentStashData.stashId) {
                // Update our local current stash ID if the server has one
                currentStashId = currentStashData.stashId;
                console.log(`Using server-provided stash ID: ${currentStashId}`);
            }
        } catch (err) {
            console.error('Error fetching current stash ID:', err);
            // Continue execution even if this fails
        }

        // Fetch stash data - now the response format might be different
        const response = await fetch(`/api/character/${charId}/stashes`);
        const stashes = await response.json();

        // Detect if we have the new or old API response format
        const isNewFormat = stashes.previewImages && stashes.stashData;

        // Get stash keys based on format
        const stashKeys = isNewFormat
            ? Object.keys(stashes.previewImages || {})
            : Object.keys(stashes || {});

        if (stashKeys.length > 0) {
            // Create stash tabs but don't set first one active automatically
            const firstStashUrl = createStashTabsWithoutDefault(stashes);

            // If we have a stored current stash ID, use that
            const currentStashTab = document.querySelector(`[data-stash-id="${currentStashId}"]`);
            if (currentStashTab) {
                // Make the correct tab active
                document.querySelectorAll('.stash-tab').forEach(t => t.classList.remove('active'));
                currentStashTab.classList.add('active');

                // Keep the image source for fallback
                if (isNewFormat) {
                    previewImage.src = stashes.previewImages[currentStashId];
                } else {
                    previewImage.src = stashes[currentStashId];
                }

                // Process and render the interactive grid
                processStashData(stashes, currentStashId).then(items => {
                    renderInteractiveGrid(currentStashId, items);
                });

                console.log(`Selected stash tab: ${getStashName(parseInt(currentStashId))}`);
            } else {
                // If no current stash is set or found, use the first one as default
                const firstTab = document.querySelector('.stash-tab');
                if (firstTab) {
                    firstTab.classList.add('active');
                    currentStashId = firstTab.dataset.stashId;

                    // Keep the image source for fallback
                    if (isNewFormat) {
                        previewImage.src = stashes.previewImages[currentStashId];
                    } else {
                        previewImage.src = stashes[currentStashId];
                    }

                    // Process and render the interactive grid 
                    processStashData(stashes, currentStashId).then(items => {
                        renderInteractiveGrid(currentStashId, items);
                    });

                    // Update the server with our selection
                    updateCurrentStash(currentStashId);
                }
            }

            // hide spinner, show stash content
            selector.classList.remove('hidden');
            previewContainer.classList.remove('hidden');
        } else {
            // Show empty state
            previewContainer.innerHTML = '<div class="empty-state">No stashes found for this character</div>';
            previewContainer.classList.remove('hidden');
        }
        spinner.classList.add('hidden');
    } catch (error) {
        console.error('Error loading stashes:', error);
        handleApiError(error, document.getElementById('stashContainer'));
        spinner.classList.add('hidden');
    }
};

// Function to show notification
function showNotification(message, type = 'info') {
    const container = document.createElement('div');
    container.className = `notification ${type}`;
    container.textContent = message;
    document.body.appendChild(container);

    // Remove after animation
    setTimeout(() => {
        container.classList.add('fade-out');
        setTimeout(() => {
            if (container.parentNode) {
                document.body.removeChild(container);
            }
        }, 300);
    }, 3000);
}

// Add keyboard shortcuts: Ctrl+S to sort, Ctrl+X to cancel
document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        const sortButton = document.querySelector('.sort-button');
        sortButton && sortButton.click();
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'x') {
        e.preventDefault();
        if (abortController) abortController.abort();
    }
});

// Add an event listener for when sorting starts from a keybind
window.addEventListener('sortingStarted', () => {
    setSortingState(true);
});

window.addEventListener('sortingEnded', () => {
    setSortingState(false);
});

// Add update handler for character data
window.updateCharacterData = async () => {
    await updateCharacterInfo(charId);
    await loadStashes();
};

// Initialize page when DOM is loaded
window.addEventListener('DOMContentLoaded', async () => {
    try {
        // Check if there's a stash ID in the URL params (added by search page)
        const urlParams = new URLSearchParams(window.location.search);
        const stashIdParam = urlParams.get('stashId');
        if (stashIdParam) {
            // Set the current stash ID from URL parameter
            currentStashId = stashIdParam;
            console.log(`Using stash ID from URL: ${currentStashId}`);
        } else {
            // If not in URL, try to get it from server
            try {
                const currentStashResponse = await fetch(`/api/character/${charId}/current-stash`);
                const currentStashData = await currentStashResponse.json();

                if (currentStashData && currentStashData.stashId) {
                    // Update our local current stash ID if the server has one
                    currentStashId = currentStashData.stashId;
                    console.log(`Using server-provided stash ID: ${currentStashId}`);
                }
            } catch (err) {
                console.error('Error fetching current stash ID:', err);
            }
        }

        await updateCharacterInfo(charId);
        await loadStashes();
    } catch (error) {
        handleApiError(error, document.querySelector('.character-details'));
    }
});
