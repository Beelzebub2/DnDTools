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

function getClassImage(className) {
    if (!className) return '/assets/classes/fighter.png';

    // Convert class name to lowercase and handle potential variations
    const classMap = {
        'fighter': 'fighter.png',
        'ranger': 'ranger.png',
        'rogue': 'rogue.png',
        'wizard': 'wizard.png',
        'cleric': 'cleric.png',
        'warlock': 'warlock.png',
        'barbarian': 'barbarian.png',
        'bard': 'bard.png',
        'druid': 'druid.png',
        'sorcerer': 'sorcerer.png'
    };

    const classKey = className.toLowerCase();
    const imageName = classMap[classKey] || 'fighter.png'; // Default to fighter if not found

    return `/assets/classes/${imageName}`;
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
        const classImageSrc = getClassImage(details.class);
        charInfo.innerHTML = `
            <div class="character-hero-section">
                <div class="character-hero-left">
                    <img src="${classImageSrc}" 
                         alt="${details.class}" 
                         class="character-class-image"
                         onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                    <span class="material-icons character-class-fallback" style="display: none;">person</span>
                </div>
                <div class="character-hero-content">
                    <h1 class="character-hero-name">${details.nickname}</h1>
                    <div class="character-hero-subtitle">Level ${details.level} ${details.class}</div>
                    <div class="character-stats-grid">
                        <div class="stat-item">
                            <span class="material-icons">schedule</span>
                            <div class="stat-content">
                                <div class="stat-label">Last Updated</div>
                                <div class="stat-value">${formatDate(details.lastUpdate)}</div>
                            </div>
                        </div>
                        <div class="stat-item">
                            <span class="material-icons">inventory_2</span>
                            <div class="stat-content">
                                <div class="stat-label">Total Items</div>
                                <div class="stat-value">${details.totalItems}</div>
                            </div>
                        </div>
                        <div class="stat-item">
                            <span class="material-icons">storage</span>
                            <div class="stat-content">
                                <div class="stat-label">Stash Count</div>
                                <div class="stat-value">${details.stashCount}</div>
                            </div>
                        </div>
                    </div>
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
    // Get grid dimensions for bounds checking
    let gridWidth, gridHeight;
    if (stashId !== '2' && stashId !== '3' && stashId !== 'character') {
        gridWidth = 12;
        gridHeight = 20;
    } else {
        [gridWidth, gridHeight] = getStashDimensions(stashId);
    }

    // Helper function to validate item bounds and normalize data
    const normalizeItem = (item) => {
        if (!item) return null;

        // Ensure slotId is valid number and within bounds
        const slotId = typeof item.slotId === 'number' ? item.slotId : parseInt(item.slotId, 10);
        if (isNaN(slotId) || slotId < 0 || slotId >= (gridWidth * gridHeight)) {
            console.warn(`Item with invalid slotId ${slotId} filtered out`);
            return null;
        }

        // Extract row/col coordinates
        const x = slotId % gridWidth;
        const y = Math.floor(slotId / gridWidth);

        // Ensure width/height are valid and within bounds
        const width = Math.min(item.width || 1, gridWidth - x);
        const height = Math.min(item.height || 1, gridHeight - y);

        // Return normalized item with bounded dimensions
        return {
            ...item,
            slotId: slotId,
            width: width,
            height: height,
            rarity: item.rarity || 'Common',
            itemCount: item.itemCount || 1,
            pp: item.pp || [],
            sp: item.sp || []
        };
    };

    // Check if we're working with the new enhanced API response format
    if (stashData && typeof stashData === 'object' && stashData.stashData) {
        // New format: we have detailed item data directly from the API
        const items = stashData.stashData[stashId] || [];
        // Validate and normalize all items
        return items.map(normalizeItem).filter(item => item !== null);
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
                // Validate and normalize all items
                return details.stashes[stashId]
                    .map(normalizeItem)
                    .filter(item => item !== null);
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
const renderInteractiveGrid = (stashId, items) => {
    const gridContainer = document.getElementById('interactiveStashGrid');
    if (!gridContainer) return;

    // Clear existing content
    gridContainer.innerHTML = '';

    // Strictly enforce 12x20 grid for all standard stashes (not bag, equipment, or character)
    let gridWidth, gridHeight;
    if (stashId !== '2' && stashId !== '3' && stashId !== 'character') {
        gridWidth = 12;
        gridHeight = 20;
    } else {
        [gridWidth, gridHeight] = getStashDimensions(stashId);
    }

    // Calculate the total vendor value
    let totalValue = 0;
    if (items && items.length) {
        totalValue = items.reduce((sum, item) => {
            return sum + ((item.vendor_price || 0) * (item.itemCount || 1));
        }, 0);
    }

    // Update the total value display
    const totalValueElement = document.getElementById('totalStashValue');
    if (totalValueElement) {
        totalValueElement.textContent = totalValue.toLocaleString();
    }    // Special handling for equipment stashes
    if (stashId === '3') {
        // The renderEquipmentGrid function doesn't seem to exist
        // Instead, use the renderCombinedCharacterView with dummy bag data
        console.warn("Equipment view requested separately - redirecting to character view");
        renderCombinedCharacterView({
            stashData: {
                "3": items,
                "2": [] // Empty bag
            }
        });
        return;
    }

    // Create one single grid - no conditional logic that could create multiple grids
    const grid = document.createElement('div');
    grid.className = 'interactive-stash-grid';

    // Use explicit sizing for the grid to prevent expansion
    grid.style.gridTemplateColumns = `repeat(${gridWidth}, 45px)`;
    grid.style.gridTemplateRows = `repeat(${gridHeight}, 45px)`;

    // Force zero-sized implicit rows/columns
    grid.style.gridAutoRows = '0px';
    grid.style.gridAutoColumns = '0px';

    // Add max-width/max-height constraints based on grid dimensions
    grid.style.maxWidth = `${gridWidth * 48}px`; // 45px per cell + 3px gap
    grid.style.maxHeight = `${gridHeight * 48}px`;

    // Prevent overflow from causing expansion
    grid.style.overflow = 'hidden';

    // Ensure the grid container has enough space
    gridContainer.style.paddingBottom = '20px';

    // Add empty cells for grid structure - exactly the right number
    const totalCells = gridWidth * gridHeight;
    for (let i = 0; i < totalCells; i++) {
        const cell = document.createElement('div');
        cell.className = 'stash-grid-cell';
        grid.appendChild(cell);
    }

    // Filter items that are out of bounds before processing them
    const validItems = [];
    if (items && Array.isArray(items) && items.length > 0) {
        items.forEach(item => {
            if (!item) return;
            let x = item.slotId % gridWidth;
            let y = Math.floor(item.slotId / gridWidth);
            let w = item.width || 1;
            let h = item.height || 1;

            // Check if item is within bounds
            if (
                x >= 0 && x < gridWidth &&
                y >= 0 && y < gridHeight &&
                (x + w) <= gridWidth &&
                (y + h) <= gridHeight
            ) {
                validItems.push(item);
            } else {
                console.warn(`Item with slotId ${item.slotId} (size ${w}x${h}) is out of bounds for grid ${gridWidth}x${gridHeight}`);
            }
        });

        // Now process only the valid items
        validItems.forEach(item => {
            let x = item.slotId % gridWidth;
            let y = Math.floor(item.slotId / gridWidth);
            let w = item.width || 1;
            let h = item.height || 1;

            // Create item element
            const itemEl = document.createElement('div');
            itemEl.className = 'stash-item';

            // Use grid positioning instead of absolute for better alignment
            itemEl.style.gridColumn = `${x + 1} / span ${w}`;
            itemEl.style.gridRow = `${y + 1} / span ${h}`;

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
            itemEl.removeAttribute('title');
            itemEl.addEventListener('mouseenter', (e) => {
                if (tooltipHideTimeout) clearTimeout(tooltipHideTimeout);
                const rarityColor = rarityColors[item.rarity] || rarityColors['Common'];
                let html = `
                    <div class="tooltip-header" style="background-color: ${rarityColor}44;">
                        <div class="tooltip-name">${item.name || 'Unknown'}</div>
                        <div class="tooltip-rarity">${item.rarity || 'Common'}</div>
                    </div>
                    <div class="tooltip-body">
                        <div class="tooltip-section primary-props">${formatPrimaryProps(item.pp)}</div>
                        <div class="tooltip-section secondary-props">${formatSecondaryProps(item.sp)}</div>
                    </div>
                    <div class="tooltip-body">
                        <div class="tooltip-section primary-props" id="extra-info-placeholder">
                            Market Prices: Soon
                            <div>Vendor Price: ${item.vendor_price || 0} coins</div>
                        </div>
                    </div>
                `;
                showGlobalTooltip(html, e.clientX, e.clientY);
            });
            itemEl.addEventListener('mousemove', (e) => {
                if (globalTooltip && globalTooltip.style.display === 'block') {
                    showGlobalTooltip(globalTooltip.innerHTML, e.clientX, e.clientY);
                }
            });
            itemEl.addEventListener('mouseleave', () => {
                hideGlobalTooltip();
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

// Function to create stash tabs without setting a default active tab
const createStashTabsWithoutDefault = (stashes) => {
    const selector = document.getElementById('stashSelector');
    const preview = document.getElementById('currentStashPreview');
    const previewContainer = document.getElementById('stashPreview');
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

    // Add Character tab first if we have equipment (3) - we don't require bag (2) anymore
    if (stashKeys.includes('3')) {
        const tab = document.createElement('div');
        tab.className = 'stash-tab';
        tab.textContent = 'Character';
        tab.dataset.stashId = 'character';
        tab.onclick = (e) => {
            document.querySelectorAll('.stash-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            // Hide the static image preview
            preview.classList.add('hidden');

            // Set our tracking variables
            currentStashId = 'character';
            usingCombinedCharacterView = true;

            // Render combined equipment and bag view
            renderCombinedCharacterView(stashes);

            // Update the server with our selection, using equipment as the storage ID
            updateCurrentStash('3');
        };
        selector.appendChild(tab);
    }

    // Then add the other stash tabs (excluding bag and equipment which are now in Character tab)
    stashKeys.forEach((stashId, index) => {
        // Skip bag and equipment as they're now in the combined Character tab
        if (stashId === '2' || stashId === '3') {
            return;
        }

        const tab = document.createElement('div');
        tab.className = 'stash-tab';

        // Store URL for fallback
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

            // Always hide the static image preview - we use interactive grid instead
            preview.classList.add('hidden');

            // Hide any "Stash Preview" text 
            previewContainer.className = 'stash-content-area';

            currentStashId = stashId;
            usingCombinedCharacterView = false;
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
    // If we're using the combined character view, default to sorting the bag (2)
    const stashIdToSort = usingCombinedCharacterView ? "2" : currentStashId;

    if (!stashIdToSort) return;

    // prepare abort controller
    if (abortController) abortController.abort();
    abortController = new AbortController();

    const sortButton = document.querySelector('.sort-button');
    setSortingState(true);

    try {
        const response = await fetch(`/api/character/${charId}/stash/${stashIdToSort}/sort`, {
            method: 'POST',
            signal: abortController.signal
        });
        const result = await response.json();

        if (result.success) {
            await loadStashes();
            showNotification('Stash sorted successfully', 'success');
        } else {
            const errorMessage = result.error || 'Failed to sort stash. The stash might be full.';
            // Use the global notification function from app.js for consistent UI notifications
            if (typeof window.showNotification === 'function') {
                window.showNotification(errorMessage, 'error');
            } else {
                showNotification(errorMessage, 'error');
            }
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

// Animation for sorting text messages
let sortingTextInterval = null;
const sortingMessages = [
    'Having issues? Visit our Discord server!',
    'Enjoying the app? Star us on GitHub!',
    'Sorting your awesome loot...',
    'Finding the perfect spot for everything...',
    'Optimizing your inventory layout...',
    'Just a moment longer...'
];

function animateSortingText(start = true) {
    const sortingText = document.getElementById('sortingText');
    if (!sortingText) return;

    // Clear any existing interval
    if (sortingTextInterval) {
        clearInterval(sortingTextInterval);
        sortingTextInterval = null;
    }

    if (start) {
        let index = 0;
        sortingText.textContent = sortingMessages[0];

        // Change the message every 2 seconds
        sortingTextInterval = setInterval(() => {
            index = (index + 1) % sortingMessages.length;
            sortingText.textContent = sortingMessages[index];
        }, 2000);
    } else {
        // Reset to default message when stopping
        sortingText.textContent = 'Sorting items...';
    }
}

function setSortingState(isSorting) {
    const sortButton = document.querySelector('.sort-button');
    const sortingOverlay = document.getElementById('sortingOverlay');
    const interactiveStashGrid = document.getElementById('interactiveStashGrid');

    if (!sortButton) return;

    sortButton.disabled = isSorting;

    if (isSorting) {
        // Show sorting state on button
        sortButton.classList.add('sorting');
        sortButton.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M2 4h12M4 8h8M6 12h4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
            Sorting...
        `;

        // Hide the interactive grid and show the sorting overlay
        if (interactiveStashGrid) {
            interactiveStashGrid.style.opacity = '0';
            interactiveStashGrid.style.pointerEvents = 'none';
        }

        if (sortingOverlay) {
            sortingOverlay.classList.remove('hidden');
            // Start the sorting text animation
            animateSortingText(true);
        }
    } else {
        // Restore normal button state
        sortButton.classList.remove('sorting');
        sortButton.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M2 4h12M4 8h8M6 12h4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
            Sort Stash
        `;

        // Show the interactive grid and hide the sorting overlay
        if (interactiveStashGrid) {
            interactiveStashGrid.style.opacity = '1';
            interactiveStashGrid.style.pointerEvents = 'auto';
        }

        if (sortingOverlay) {
            sortingOverlay.classList.add('hidden');
            // Stop the sorting text animation
            animateSortingText(false);
        }
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
    }    // show spinner, hide stash content
    spinner.classList.remove('hidden');
    const stashSectionHeader = document.getElementById('stashSectionHeader');
    if (stashSectionHeader) {
        stashSectionHeader.classList.add('hidden');
    }
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

            // Check if we have both equipment and bag
            const hasCharacterTab = stashKeys.includes('2') && stashKeys.includes('3');

            // If currentStashId is 2 (bag) or 3 (equipment) and we have a Character tab,
            // redirect to the Character tab instead
            if (hasCharacterTab && (currentStashId === '2' || currentStashId === '3')) {
                currentStashId = 'character';
            }

            // If we have a stored current stash ID, use that
            const currentStashTab = document.querySelector(`[data-stash-id="${currentStashId}"]`);
            if (currentStashTab) {
                // Make the correct tab active
                document.querySelectorAll('.stash-tab').forEach(t => t.classList.remove('active'));
                currentStashTab.classList.add('active');

                if (currentStashId === 'character') {
                    // Hide the static image preview for Character tab
                    previewImage.classList.add('hidden');

                    // Set tracking variables
                    usingCombinedCharacterView = true;

                    // Show the preview container, it will be populated by renderCombinedCharacterView
                    previewContainer.classList.remove('hidden');

                    // Render the combined view
                    renderCombinedCharacterView(stashes);

                    // Update server with selection using bag as reference
                    updateCurrentStash('2');
                } else {
                    // Keep the image source for fallback
                    if (isNewFormat) {
                        previewImage.src = stashes.previewImages[currentStashId];
                    } else {
                        previewImage.src = stashes[currentStashId];
                    }

                    // Only show the preview container if we have a valid image source
                    if (previewImage.src && previewImage.src !== window.location.href) {
                        previewImage.classList.remove('hidden');
                        previewContainer.classList.remove('hidden');
                    }

                    usingCombinedCharacterView = false;

                    // Process and render the interactive grid
                    processStashData(stashes, currentStashId).then(items => {
                        renderInteractiveGrid(currentStashId, items);
                    });
                }

                console.log(`Selected stash tab: ${currentStashId === 'character' ? 'Character' : getStashName(parseInt(currentStashId))}`);
            } else {
                // If no current stash is set or found, default to Character tab if available
                const characterTab = document.querySelector('[data-stash-id="character"]');
                if (characterTab && hasCharacterTab) {
                    characterTab.classList.add('active');
                    currentStashId = 'character';
                    previewImage.classList.add('hidden');

                    // Set tracking variables
                    usingCombinedCharacterView = true;

                    // Show the preview container, it will be populated by renderCombinedCharacterView
                    previewContainer.classList.remove('hidden');

                    // Render the combined view
                    renderCombinedCharacterView(stashes);

                    // Update server with selection using bag as reference
                    updateCurrentStash('2');
                } else {
                    // Fall back to the first available tab
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

                        // Only show the preview container if we have a valid image source
                        if (previewImage.src && previewImage.src !== window.location.href) {
                            previewImage.classList.remove('hidden');
                            previewContainer.classList.remove('hidden');
                        }

                        usingCombinedCharacterView = false;

                        // Process and render the interactive grid 
                        processStashData(stashes, currentStashId).then(items => {
                            renderInteractiveGrid(currentStashId, items);
                        });

                        // Update the server with our selection
                        updateCurrentStash(currentStashId);
                    }
                }
            }            // Show the stash section header and tabs selector
            const stashSectionHeader = document.getElementById('stashSectionHeader');
            if (stashSectionHeader) {
                stashSectionHeader.classList.remove('hidden');
            }
            selector.classList.remove('hidden');

            // The preview container is shown only for tabs that have content
            // It gets shown in the conditional blocks above
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

    // Add inline styling to position the notification below the topbar
    container.style.position = 'fixed';
    container.style.top = '60px'; // Position below the topbar
    container.style.right = '20px';
    container.style.zIndex = '9999';
    container.style.padding = '12px 20px';
    container.style.borderRadius = '4px';
    container.style.boxShadow = '0 2px 10px rgba(0, 0, 0, 0.2)';
    container.style.animation = 'slideIn 0.3s ease-out forwards';

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

// Character capture animation function (placeholder for character page)
window.showCharacterCaptureAnimation = function (characterClass, characterNickname) {
    console.log(`Character captured: ${characterNickname} (${characterClass})`);
    // On character page, just log - the main animation happens on record page
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

        // Force render the Character tab if it exists
        setTimeout(() => {
            const characterTab = document.querySelector('[data-stash-id="character"]');
            if (characterTab) {
                characterTab.click();
            }
        }, 100);
    } catch (error) {
        handleApiError(error, document.querySelector('.character-details'));
    }
});

// Global price cache object to store results
const priceCache = {};
const priceFetchPromises = {}; // Track ongoing fetch promises
const PRICE_CACHE_EXPIRY = 600000; // 10 minutes in milliseconds

async function getMostRecentPrice(item) {
    const itemId = item.itemId;

    // Check client-side cache first
    const now = Date.now();
    if (priceCache[itemId] && now - priceCache[itemId].timestamp < PRICE_CACHE_EXPIRY) {
        console.log(`Using cached price for ${itemId}`);
        return priceCache[itemId].data;
    }

    // If there's already a fetch in progress for this item, return that promise
    if (priceFetchPromises[itemId]) {
        console.log(`Using existing fetch promise for ${itemId}`);
        return priceFetchPromises[itemId];
    }

    // No valid cache entry, use our Flask proxy endpoint
    const apiUrl = `/api/market/price/${itemId}`;

    try {
        // Store the promise in our tracking object so we can reuse it for concurrent requests
        priceFetchPromises[itemId] = (async () => {
            const response = await fetch(apiUrl);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();

            // Cache the result with timestamp
            if (data && data.success) {
                priceCache[itemId] = {
                    timestamp: now,
                    data: data
                };
                return data; // Return the entire data object
            } else {
                return "No Info";
            }
        })();

        // Wait for the fetch to complete
        const result = await priceFetchPromises[itemId];

        // Clear the promise now that it's done
        delete priceFetchPromises[itemId];

        return result;
    } catch (error) {
        console.error('Error fetching price:', error);

        // Clear the failed promise
        delete priceFetchPromises[itemId];

        return "Error";
    }
}

// --- GLOBAL TOOLTIP SINGLETON ---
let globalTooltip = null;
let tooltipHideTimeout = null;

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

    // Check if we have existing content with price info that's already loaded
    if (tooltip.innerHTML.includes('Estimated Price:') &&
        !tooltip.innerHTML.includes('Estimated Price: Loading...') &&
        html.includes('Estimated Price: Loading...')) {

        // Extract the completed price section from the existing tooltip
        const currentPriceInfo = tooltip.querySelector('#extra-info-placeholder');
        if (currentPriceInfo) {
            // Create a temporary container to parse the new HTML
            const tempContainer = document.createElement('div');
            tempContainer.innerHTML = html;

            // Replace the loading price section with our completed one
            const newPriceSection = tempContainer.querySelector('#extra-info-placeholder');
            if (newPriceSection) {
                newPriceSection.innerHTML = currentPriceInfo.innerHTML;
            }

            // Use the updated HTML
            html = tempContainer.innerHTML;
        }
    }

    tooltip.innerHTML = html;
    tooltip.style.display = 'block';
    tooltip.classList.add('visible');

    // Position
    const tooltipWidth = tooltip.offsetWidth || 250;
    const tooltipHeight = tooltip.offsetHeight || 150;
    let left = x + 15;
    let top = y + 15;
    if (left + tooltipWidth > window.innerWidth) left = x - tooltipWidth - 15;
    if (top + tooltipHeight > window.innerHeight) top = y - tooltipHeight - 15;
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

// Variable to track if we're using combined character view
let usingCombinedCharacterView = false;

// Special function to render combined character view (equipment and bag)
const renderCombinedCharacterView = async (stashes) => {
    const gridContainer = document.getElementById('interactiveStashGrid');
    if (!gridContainer) return;

    // Clear existing content
    gridContainer.innerHTML = '';

    // Process both equipment (3) and bag (2) stash data
    const equipmentItems = await processStashData(stashes, "3") || [];
    const bagItems = await processStashData(stashes, "2") || [];

    // Calculate total vendor value for all items
    let totalValue = 0;

    // Add equipment items value
    if (equipmentItems && equipmentItems.length) {
        totalValue += equipmentItems.reduce((sum, item) => {
            return sum + ((item.vendor_price || 0) * (item.itemCount || 1));
        }, 0);
    }

    // Add bag items value
    if (bagItems && bagItems.length) {
        totalValue += bagItems.reduce((sum, item) => {
            return sum + ((item.vendor_price || 0) * (item.itemCount || 1));
        }, 0);
    }

    // Update the total value display
    const totalValueElement = document.getElementById('totalStashValue');
    if (totalValueElement) {
        totalValueElement.textContent = totalValue.toLocaleString();
    }

    // Equipment dimensions and bag dimensions
    const [equipWidth, equipHeight] = getStashDimensions("3");
    const [bagWidth, bagHeight] = getStashDimensions("2");

    // Create main grid container with appropriate space for both
    const combinedGrid = document.createElement('div');
    combinedGrid.className = 'combined-character-grid';

    // Always render equipment section - even if empty
    const equipmentSection = document.createElement('div');
    equipmentSection.className = 'equipment-section';

    const equipmentTitle = document.createElement('div');
    equipmentTitle.className = 'section-title';
    equipmentTitle.textContent = 'Equipment';
    equipmentSection.appendChild(equipmentTitle);    // Create equipment grid
    const equipmentGrid = document.createElement('div');
    equipmentGrid.className = 'interactive-stash-grid equipment-grid';
    equipmentGrid.style.gridTemplateColumns = `repeat(${equipWidth}, 45px)`;
    equipmentGrid.style.gridTemplateRows = `repeat(${equipHeight}, 45px)`;

    // Ensure equipment grid is not constrained by the restrictions we put on standard stashes
    equipmentGrid.style.maxWidth = 'none';
    equipmentGrid.style.maxHeight = 'none';
    equipmentGrid.style.overflow = 'visible';

    // Load equipment slot configuration if not already loaded
    if (!equipmentSlotConfig) {
        equipmentSlotConfig = await fetchEquipmentSlotConfig();
    }

    // Build a map of equipment items by slotId
    const itemBySlot = {};
    if (equipmentItems && equipmentItems.length) {
        equipmentItems.forEach(item => {
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

        // Add tooltip
        if (!faded) {
            itemEl.removeAttribute('title');
            itemEl.addEventListener('mouseenter', (e) => {
                if (tooltipHideTimeout) clearTimeout(tooltipHideTimeout);
                // Build tooltip HTML
                const rarityColor = rarityColors[item.rarity] || rarityColors['Common'];
                let html = `
                    <div class="tooltip-header" style="background-color: ${rarityColor}44;">
                        <div class="tooltip-name">${item.name || 'Unknown'}</div>
                        <div class="tooltip-rarity">${item.rarity || 'Common'}</div>
                    </div>
                    <div class="tooltip-body">
                        <div class="tooltip-section primary-props">${formatPrimaryProps(item.pp)}</div>
                        <div class="tooltip-section secondary-props">${formatSecondaryProps(item.sp)}</div>
                    </div>
                    <div class="tooltip-body">
                        <div class="tooltip-section primary-props" id="extra-info-placeholder">
                            Market Prices: Soon
                            <div>Vendor Price: ${item.vendor_price || 0} coins</div>
                        </div>
                    </div>
                `;
                showGlobalTooltip(html, e.clientX, e.clientY);
            });
            itemEl.addEventListener('mousemove', (e) => {
                if (globalTooltip && globalTooltip.style.display === 'block') {
                    showGlobalTooltip(globalTooltip.innerHTML, e.clientX, e.clientY);
                }
            });
            itemEl.addEventListener('mouseleave', () => {
                hideGlobalTooltip();
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
        equipmentGrid.appendChild(slotCell);
    }

    // Append equipment grid to section
    equipmentSection.appendChild(equipmentGrid);

    // Always add the equipment section to combined grid
    combinedGrid.appendChild(equipmentSection);

    // Always add bag section too (even if empty)
    const bagSection = document.createElement('div');
    bagSection.className = 'bag-section';

    const bagTitle = document.createElement('div');
    bagTitle.className = 'section-title';
    bagTitle.textContent = 'Bag';
    bagSection.appendChild(bagTitle);    // Create bag grid
    const bagGrid = document.createElement('div');
    bagGrid.className = 'interactive-stash-grid bag-grid';
    bagGrid.style.gridTemplateColumns = `repeat(${bagWidth}, 45px)`;
    bagGrid.style.gridTemplateRows = `repeat(${bagHeight}, 45px)`;

    // Ensure bag grid is not constrained by the restrictions we put on standard stashes
    bagGrid.style.maxWidth = 'none';
    bagGrid.style.maxHeight = 'none';
    bagGrid.style.overflow = 'visible';

    // Create bag grid cells
    for (let y = 0; y < bagHeight; y++) {
        for (let x = 0; x < bagWidth; x++) {
            const cell = document.createElement('div');
            cell.className = 'stash-grid-cell';
            cell.style.gridColumn = `${x + 1}`;
            cell.style.gridRow = `${y + 1}`;
            bagGrid.appendChild(cell);
        }
    }

    // Add bag items to the grid
    if (bagItems && bagItems.length) {
        bagItems.forEach(item => {
            if (!item) return;
            let x = item.slotId % bagWidth;
            let y = Math.floor(item.slotId / bagWidth);
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

            // Create inset border with box-shadow
            itemEl.style.boxShadow = `inset 0 0 0 1px rgba(0,0,0,0.3), 0 0 0 1px ${rarityColor}30, inset 0 0 5px ${rarityColor}40`;

            // Apply background color based on rarity with subtle transparency
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

            // Add tooltip functionality
            itemEl.removeAttribute('title');
            itemEl.addEventListener('mouseenter', (e) => {
                if (tooltipHideTimeout) clearTimeout(tooltipHideTimeout);
                // Build tooltip HTML
                const rarityColor = rarityColors[item.rarity] || rarityColors['Common'];
                let html = `
                    <div class="tooltip-header" style="background-color: ${rarityColor}44;">
                        <div class="tooltip-name">${item.name || 'Unknown'}</div>
                        <div class="tooltip-rarity">${item.rarity || 'Common'}</div>
                    </div>
                    <div class="tooltip-body">
                        <div class="tooltip-section primary-props">${formatPrimaryProps(item.pp)}</div>
                        <div class="tooltip-section secondary-props">${formatSecondaryProps(item.sp)}</div>
                    </div>
                    <div class="tooltip-body">
                        <div class="tooltip-section primary-props" id="extra-info-placeholder">
                        Market Prices: Soon
                        <div>Vendor Price: ${item.vendor_price || 0} coins</div>
                        </div>
                    </div>
                `;
                showGlobalTooltip(html, e.clientX, e.clientY);
            });
            itemEl.addEventListener('mousemove', (e) => {
                if (globalTooltip && globalTooltip.style.display === 'block') {
                    showGlobalTooltip(globalTooltip.innerHTML, e.clientX, e.clientY);
                }
            });
            itemEl.addEventListener('mouseleave', () => {
                hideGlobalTooltip();
            });

            bagGrid.appendChild(itemEl);
        });
    }

    // Append bag grid to section
    bagSection.appendChild(bagGrid);

    // Append bag section to the combined grid
    combinedGrid.appendChild(bagSection);

    // Add the combined grid to the container
    gridContainer.appendChild(combinedGrid);
};


// Stash sort ordering popup
document.addEventListener('DOMContentLoaded', () => {
    const button = document.getElementById('orderingButton');
    const menu = document.getElementById('orderingMenu');
    let dragged = null;

    // Toggle menu visibility
    button.addEventListener('click', (e) => {
        e.stopPropagation();
        menu.classList.toggle('hidden');
    });

    // Close menu when clicking outside
    document.addEventListener('click', (e) => {
        if (!menu.contains(e.target) && !button.contains(e.target)) {
            menu.classList.add('hidden');
        }
    });

    // Setup draggable ordering options
    menu.querySelectorAll('.ordering-option').forEach(option => {
        option.addEventListener('dragstart', () => {
            dragged = option;
            option.classList.add('dragging');
        });

        option.addEventListener('dragend', () => {
            option.classList.remove('dragging');
            onOrderChange(); // Call onOrderChange when drag ends
        });

        option.addEventListener('dragover', (e) => {
            e.preventDefault();
            const bounding = option.getBoundingClientRect();
            const offset = bounding.y + bounding.height / 2;
            if (e.clientY < offset) {
                option.parentNode.insertBefore(dragged, option);
            } else {
                option.parentNode.insertBefore(dragged, option.nextSibling);
            }
        });

        // Click on option (excluding arrow buttons)
        option.addEventListener('click', (e) => {
            if (e.target.tagName === 'BUTTON') return;
            const sortKey = option.dataset.sort;
            console.log(`Selected sort: ${sortKey}`);
            menu.classList.add('hidden');
        });
    });

    // Handle arrow buttons
    menu.addEventListener('click', (e) => {
        const btn = e.target;
        const option = btn.closest('.ordering-option');
        if (!option) return;

        if (btn.classList.contains('arrow-up')) {
            moveOptionUp(btn);
        }

        if (btn.classList.contains('arrow-down')) {
            moveOptionDown(btn);
        }

        onOrderChange(); // Call onOrderChange when arrow buttons are clicked
    });
});

// Add this to where the arrow click handlers are defined
function animateSwap(element1, element2, direction) {
    const container = element1.parentNode;
    const containerHeight = container.offsetHeight;

    // Force container to maintain height during animation
    container.style.minHeight = `${containerHeight}px`;

    // Create placeholders with exact dimensions
    const placeholder1 = element1.cloneNode(false);
    const placeholder2 = element2.cloneNode(false);

    placeholder1.style.visibility = 'hidden';
    placeholder2.style.visibility = 'hidden';
    placeholder1.classList.add('placeholder');
    placeholder2.classList.add('placeholder');

    // Match dimensions exactly
    placeholder1.style.height = `${element1.offsetHeight}px`;
    placeholder2.style.height = `${element2.offsetHeight}px`;

    // Add animating class to both elements
    element1.classList.add('animating');
    element2.classList.add('animating');

    // Set absolute positioning to animate smoothly
    const rect1 = element1.getBoundingClientRect();
    const rect2 = element2.getBoundingClientRect();
    const parentRect = element1.parentNode.getBoundingClientRect();

    // Position elements absolutely during animation
    element1.style.position = 'absolute';
    element2.style.position = 'absolute';
    element1.style.width = `${rect1.width}px`;
    element1.style.height = `${rect1.height}px`;
    element2.style.width = `${rect2.width}px`;
    element2.style.height = `${rect2.height}px`;
    element1.style.top = `${rect1.top - parentRect.top}px`;
    element1.style.left = `${rect1.left - parentRect.left}px`;
    element2.style.top = `${rect2.top - parentRect.top}px`;
    element2.style.left = `${rect2.left - parentRect.left}px`;

    // Insert placeholders
    element1.parentNode.insertBefore(placeholder1, element1);
    element2.parentNode.insertBefore(placeholder2, element2);

    // Add transition for smooth movement
    element1.style.transition = 'all 0.3s ease';
    element2.style.transition = 'all 0.3s ease';

    // Set timeout to ensure DOM has updated
    setTimeout(() => {
        // Move elements to their new positions
        element1.style.top = `${rect2.top - parentRect.top}px`;
        element1.style.left = `${rect2.left - parentRect.left}px`;
        element2.style.top = `${rect1.top - parentRect.top}px`;
        element2.style.left = `${rect1.left - parentRect.left}px`;

        // After animation completes
        setTimeout(() => {            // Remove animation styles
            element1.style.position = '';
            element1.style.top = '';
            element1.style.left = '';
            element1.style.width = '';
            element1.style.height = '';
            element1.style.transition = '';
            element2.style.position = '';
            element2.style.top = '';
            element2.style.left = '';
            element2.style.width = '';
            element2.style.height = '';
            element2.style.transition = '';

            element1.classList.remove('animating');
            element2.classList.remove('animating');

            // Keep container height for a moment to prevent flickering
            setTimeout(() => {
                // Only remove minHeight if we're not in the middle of another animation
                if (!element1.parentNode.querySelector('.animating')) {
                    element1.parentNode.style.minHeight = '';
                }
            }, 50);

            // Actually swap the elements in the DOM
            if (direction === 'up') {
                element2.parentNode.insertBefore(element1, element2);
            } else {
                element1.parentNode.insertBefore(element2, element1);
            }

            // Remove placeholders
            placeholder1.parentNode.removeChild(placeholder1);
            placeholder2.parentNode.removeChild(placeholder2);
        }, 300); // Match this with the CSS transition duration
    }, 10); // Small delay to ensure positions are calculated correctly
}

// Function to handle click on up arrow
function moveOptionUp(button) {
    const option = button.closest('.ordering-option');
    const previousOption = option.previousElementSibling;

    if (previousOption && previousOption.classList.contains('ordering-option')) {
        animateSwap(option, previousOption, 'up');
        updateOrder(); // Update the ordering after animation
    }
}

// Function to handle click on down arrow
function moveOptionDown(button) {
    const option = button.closest('.ordering-option');
    const nextOption = option.nextElementSibling;

    if (nextOption && nextOption.classList.contains('ordering-option')) {
        animateSwap(option, nextOption, 'down');
        updateOrder(); // Update the ordering after animation
    }
}

// Function to handle changes in order
function onOrderChange() {
    const order = getOrderingOptions();
    console.log("Order changed:", order);

    fetch('/api/sort_order', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ order: order })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                console.log('Sort order updated successfully');
            } else {
                console.error('Failed to update sort order');
            }
        })
        .catch(error => {
            console.error('Error during sort order update:', error);
        });
}

function getOrderingOptions() {
    const menu = document.getElementById('orderingMenu');
    const options = menu.querySelectorAll('.ordering-option');
    const currentOrder = Array.from(options).map(option => option.dataset.sort);
    return currentOrder;
}