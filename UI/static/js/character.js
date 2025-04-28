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

const updateCharacterInfo = async (characterId) => {
    const charInfo = document.getElementById('characterInfo');
    const charHeader = document.getElementById('characterHeader');
    try {
        let details;
        if (window.pywebview && window.pywebview.api) {
            details = await window.pywebview.api.get_character_details(characterId);
        } else {
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

const createStashTabs = (stashes) => {
    const selector = document.getElementById('stashSelector');
    const preview = document.getElementById('currentStashPreview');
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
            firstStashUrl = stashes[stashId];
            currentStashId = stashId;
            // Set initial stash
            updateCurrentStash(stashId);
        }

        tab.textContent = getStashName(parseInt(stashId));
        tab.dataset.stashId = stashId;
        tab.onclick = (e) => {
            document.querySelectorAll('.stash-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            preview.src = stashes[stashId];
            currentStashId = stashId;
            updateCurrentStash(stashId);
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
    const sortButton = document.querySelector('.sort-button');
    selector.innerHTML = '';

    // Ensure stashes is an object
    const stashesObj = stashes || {};
    const stashKeys = Object.keys(stashesObj);
    let firstStashUrl = null;

    stashKeys.forEach((stashId, index) => {
        const tab = document.createElement('div');
        tab.className = 'stash-tab';

        // Just store the first URL for fallback
        if (index === 0) {
            firstStashUrl = stashes[stashId];
        }

        tab.textContent = getStashName(parseInt(stashId));
        tab.dataset.stashId = stashId;
        tab.onclick = (e) => {
            document.querySelectorAll('.stash-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            preview.src = stashes[stashId];
            currentStashId = stashId;
            updateCurrentStash(stashId);
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

    if (!spinner || !selector || !previewContainer || !previewImage) {
        console.error('Required DOM elements not found for stash display');
        return;
    }

    // show spinner, hide stash content
    spinner.classList.remove('hidden');
    selector.classList.add('hidden');
    previewContainer.classList.add('hidden');
    previewImage.src = "";

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

        const response = await fetch(`/api/character/${charId}/stashes`);
        const stashes = await response.json();

        // Check if we have any stashes
        const stashKeys = Object.keys(stashes || {});
        if (stashKeys.length > 0) {
            // Create stash tabs but don't set first one active automatically
            const firstStashUrl = createStashTabsWithoutDefault(stashes);

            // If we have a stored current stash ID, use that
            const currentStashTab = document.querySelector(`[data-stash-id="${currentStashId}"]`);
            if (currentStashTab) {
                // Make the correct tab active
                document.querySelectorAll('.stash-tab').forEach(t => t.classList.remove('active'));
                currentStashTab.classList.add('active');
                previewImage.src = stashes[currentStashId];
                console.log(`Selected stash tab: ${getStashName(parseInt(currentStashId))}`);
            } else {
                // If no current stash is set or found, use the first one as default
                const firstTab = document.querySelector('.stash-tab');
                if (firstTab) {
                    firstTab.classList.add('active');
                    currentStashId = firstTab.dataset.stashId;
                    previewImage.src = stashes[currentStashId];
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
