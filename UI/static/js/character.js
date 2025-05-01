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
        return [8, 7]; // Wider format for equipment layout
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

// Variable to store the equipment slots configuration
let equipmentSlotConfig = null;

// Function to fetch the equipment slots configuration
async function fetchEquipmentSlotConfig() {
    try {
        const response = await fetch('/assets/equipment_slots.json');
        if (!response.ok) {
            throw new Error(`Failed to load equipment slot configuration: ${response.status}`);
        }
        const data = await response.json();
        return data.equipment_slots || {};
    } catch (error) {
        console.error('Error loading equipment slot configuration:', error);
        return {};
    }
}

// Render the interactive grid for a stash
const renderInteractiveGrid = async (stashId, items) => {
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

    // Load equipment slot configuration if needed and not already loaded
    if (isEquipment && !equipmentSlotConfig) {
        equipmentSlotConfig = await fetchEquipmentSlotConfig();
    }

    if (!isEquipment) {
        // Standard stash rendering
        for (let y = 0; y < gridHeight; y++) {
            for (let x = 0; x < gridWidth; x++) {
                const cell = document.createElement('div');
                cell.className = 'stash-grid-cell';
                cell.style.gridColumn = `${x + 1}`;
                cell.style.gridRow = `${y + 1}`;
                grid.appendChild(cell);
            }
        }

        // Add items to the grid
        if (items && items.length) {
            items.forEach(item => {
                if (!item) return;
                let x = item.slotId % gridWidth;
                let y = Math.floor(item.slotId / gridWidth);
                let w = item.width || 1;
                let h = item.height || 1;

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

                // Build tooltip content with a placeholder
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
                    <div class="tooltip-body">
                        <div class="tooltip-section primary-props" id="extra-info-placeholder">
                            Estimated Price: Loading...
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

                    // When item is hovered, load extra info asynchronously
                    const extraInfoSection = tooltip.querySelector('#extra-info-placeholder');
                    if (extraInfoSection) {
                        extraInfoSection.textContent = 'Estimated Price: Loading...';
                        getMostRecentPrice(item).then(price => {
                            if (typeof price === 'object' && price !== null) {
                                // If getMostRecentPrice is updated to return the full object
                                const avg = price.average !== undefined ? price.average : 'No Info';
                                const med = price.median !== undefined ? price.median : 'No Info';
                                extraInfoSection.textContent = `Estimated Price: Avg ${avg}g, Median ${med}g`;
                            } else {
                                // If getMostRecentPrice returns just a number
                                extraInfoSection.textContent = `Estimated Price: ${price}g`;
                            }
                        }).catch(error => {
                            if (extraInfoSection) {
                                extraInfoSection.textContent = `Failed to fetch price: ${error.message}`;
                            }
                        });
                    }

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
    } else {
        // Equipment: render slots and place items inside their slot
        // Build a map of items by slotId
        const itemBySlot = {};
        if (items && items.length) {
            items.forEach(item => {
                if (item && item.slotId != null) {
                    itemBySlot[item.slotId.toString()] = item;
                }
            });
        }
        // Helper to create an item element (optionally faded and not hoverable)
        function createItemElement(item, faded = false) {
            const itemEl = document.createElement('div');
            itemEl.className = 'stash-item';
            itemEl.style.width = `100%`;
            itemEl.style.height = `100%`;
            if (faded) {
                itemEl.style.opacity = '0.4';
                itemEl.style.pointerEvents = 'none';
            }
            const rarityColor = rarityColors[item.rarity] || rarityColors['Common'];
            itemEl.style.borderColor = rarityColor;
            itemEl.style.boxShadow = `inset 0 0 0 1px rgba(0,0,0,0.3), 0 0 0 1px ${rarityColor}30, inset 0 0 5px ${rarityColor}40`;
            itemEl.style.backgroundColor = `${rarityColor}15`;
            if (item.imagePath) {
                const img = document.createElement('img');
                img.src = item.imagePath;
                img.alt = item.name || 'Item';
                img.className = 'item-image';
                itemEl.appendChild(img);
            } else {
                itemEl.textContent = item.name || 'Unknown';
            }
            if (item.itemCount > 1) {
                const countBadge = document.createElement('div');
                countBadge.className = 'item-count-badge';
                countBadge.textContent = item.itemCount;
                itemEl.appendChild(countBadge);
            }
            if (!faded) {
                const tooltip = document.createElement('div');
                tooltip.className = 'item-tooltip';
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
                    <div class="tooltip-body">
                        <div class="tooltip-section primary-props" id="extra-info-placeholder">
                            Estimated Price: Loading...
                        </div>
                    </div>
                `;
                itemEl.appendChild(tooltip);
                itemEl.addEventListener('mouseenter', (e) => {
                    tooltip.style.display = 'block';
                    document.body.appendChild(tooltip);
                    const extraInfoSection = tooltip.querySelector('#extra-info-placeholder');
                    if (extraInfoSection) {
                        extraInfoSection.textContent = 'Estimated Price: Loading...';
                        getMostRecentPrice(item).then(price => {
                            if (typeof price === 'object' && price !== null) {
                                // If getMostRecentPrice is updated to return the full object
                                const avg = price.average !== undefined ? price.average : 'No Info';
                                const med = price.median !== undefined ? price.median : 'No Info';
                                extraInfoSection.textContent = `Estimated Price: Avg ${avg}g, Median ${med}g`;
                            } else {
                                // If getMostRecentPrice returns just a number
                                extraInfoSection.textContent = `Estimated Price: ${price}g`;
                            }
                        }).catch(error => {
                            if (extraInfoSection) {
                                extraInfoSection.textContent = `Failed to fetch price: ${error.message}`;
                            }
                        });
                    }
                    const rect = itemEl.getBoundingClientRect();
                    const tooltipWidth = tooltip.offsetWidth || 250;
                    const tooltipHeight = tooltip.offsetHeight || 150;
                    let left = e.clientX + 15;
                    let top = e.clientY + 15;
                    if (left + tooltipWidth > window.innerWidth) {
                        left = e.clientX - tooltipWidth - 15;
                    }
                    if (top + tooltipHeight > window.innerHeight) {
                        top = e.clientY - tooltipHeight - 15;
                    }
                    tooltip.style.left = `${left}px`;
                    tooltip.style.top = `${top}px`;
                    setTimeout(() => {
                        tooltip.classList.add('visible');
                    }, 10);
                });
                itemEl.addEventListener('mouseleave', () => {
                    tooltip.classList.remove('visible');
                    setTimeout(() => {
                        tooltip.style.display = 'none';
                    }, 200);
                });
                itemEl.addEventListener('mousemove', (e) => {
                    const offsetX = 15;
                    const offsetY = 15;
                    const tooltipWidth = tooltip.offsetWidth || 250;
                    const tooltipHeight = tooltip.offsetHeight || 150;
                    let left = e.clientX + offsetX;
                    let top = e.clientY + offsetY;
                    if (left + tooltipWidth > window.innerWidth) {
                        left = e.clientX - tooltipWidth - offsetX;
                    }
                    if (top + tooltipHeight > window.innerHeight) {
                        top = e.clientY - tooltipHeight - offsetY;
                    }
                    tooltip.style.left = `${left}px`;
                    tooltip.style.top = `${top}px`;
                });
            }
            return itemEl;
        }
        // Render each equipment slot
        for (const [slotId, slotData] of Object.entries(equipmentSlotConfig)) {
            const slotCell = document.createElement('div');
            slotCell.className = 'equipment-slot';
            slotCell.style.gridColumn = `${slotData.x + 1} / span ${slotData.w}`;
            slotCell.style.gridRow = `${slotData.y + 1} / span ${slotData.h}`;
            slotCell.dataset.slotId = slotId;
            slotCell.title = slotData.name;
            // If there is an item for this slot, render it inside the slot
            const item = itemBySlot[slotId];
            if (item) {
                slotCell.appendChild(createItemElement(item));
            } else {
                // Special logic for faded weapon ghosting
                if (slotId === '11' && !itemBySlot['11'] && itemBySlot['10']) {
                    slotCell.appendChild(createItemElement(itemBySlot['10'], true));
                }
                if (slotId === '13' && !itemBySlot['13'] && itemBySlot['12']) {
                    slotCell.appendChild(createItemElement(itemBySlot['12'], true));
                }
            }
            grid.appendChild(slotCell);
        }
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


async function getMostRecentPrice(item) {
    const itemId = item.itemId;

    // Use our Flask proxy endpoint to avoid CORS issues
    const apiUrl = `/api/market/price/${itemId}`;

    try {
        const response = await fetch(apiUrl);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();

        // Return the full data object for more flexibility in the UI
        if (data && data.success) {
            return data; // Return the entire data object
        } else {
            return "No Info";
        }
    } catch (error) {
        console.error('Error fetching price:', error);
        return "Error";
    }
}
