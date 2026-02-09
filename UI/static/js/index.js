function formatDate(dateString) {
    if (!dateString) return 'Unknown';
    try {
        const date = new Date(dateString);
        if (isNaN(date.getTime())) return dateString;
        return date.toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
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

async function loadCharacters() {
    const grid = document.getElementById('characterGrid');
    const loading = document.getElementById('loading');

    try {
        let characters;
        if (window.pywebview && window.pywebview.api) {
            // Use lightweight summary endpoint (no stash item data)
            characters = await (window.pywebview.api.get_characters_summary
                ? window.pywebview.api.get_characters_summary()
                : window.pywebview.api.get_characters());
        } else {
            const response = await fetch('/api/characters');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            characters = await response.json();
        }

        console.log('Characters received:', characters);

        // Check if characters is null, undefined, or an error object
        if (!characters || (characters.error && characters.error.length > 0)) {
            console.warn('No characters found or error in response:', characters);
            if (loading) loading.remove();
            grid.innerHTML = `
                <div class="empty-state">
                    <span class="material-icons">person_off</span>
                    <h3>No Characters Found</h3>
                    <p>Start by capturing your first character using the Capture page. Launch the game, select a character, and enable capture to begin collecting data.</p>
                </div>`;
            return;
        }

        if (loading) loading.remove();

        if (!Array.isArray(characters) || characters.length === 0) {
            grid.innerHTML = `
                <div class="empty-state">
                    <span class="material-icons">person_off</span>
                    <h3>No Characters Found</h3>
                    <p>Start by capturing your first character using the Capture page. Launch the game, select a character, and enable capture to begin collecting data.</p>
                </div>`;
            return;
        }

        // Sort characters by recently updated (most recent first)
        characters.sort((a, b) => {
            const dateA = new Date(a.lastUpdate || 0);
            const dateB = new Date(b.lastUpdate || 0);
            return dateB - dateA; // Descending order (most recent first)
        });

        characters.forEach(char => {
            const card = document.createElement('div');
            card.className = 'character-card';
            card.setAttribute('data-class', (char.class || '').toLowerCase());
            card.onclick = () => window.location.href = `/character/${char.id}`;

            const classImageSrc = getClassImage(char.class);
            const timeSinceUpdate = getTimeSinceUpdate(char.lastUpdate);
            const stashCount = char.stashes ? Object.keys(char.stashes).length : 0;

            // Calculate total items — summary returns {stashId: count}
            let totalItems = 0;
            if (char.stashes) {
                Object.values(char.stashes).forEach(stash => {
                    if (typeof stash === 'number') totalItems += stash;
                    else if (Array.isArray(stash)) totalItems += stash.length;
                });
            }

            card.innerHTML = `
                <div class="card-bg"></div>
                <div class="card-content">
                    <div class="card-header">
                        <div class="class-image-container">
                            <img src="${classImageSrc}" 
                                 alt="${char.class}" 
                                 class="class-image"
                                 onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                            <span class="material-icons class-icon-fallback" style="display: none;">person</span>
                            <div class="level-badge" title="Level ${char.level}">${char.level}</div>
                        </div>
                        <div class="character-title">
                            <div class="character-name">${char.nickname}</div>
                            <div class="character-subtitle">${char.class}</div>
                        </div>
                    </div>

                    <div class="character-stats">
                        <div class="stat-item">
                            <span class="stat-label">Rank</span>
                            <span class="stat-value">
                                <span class="material-icons">military_tech</span>
                                ${(char.rank && char.rank.name) || 'Unknown'}
                            </span>
                        </div>
                        <div class="stat-item">
                            <span class="stat-label">Stash Tabs</span>
                            <span class="stat-value">
                                <span class="material-icons">inventory_2</span>
                                ${stashCount}
                            </span>
                        </div>
                    </div>

                    <div class="card-footer">
                        <div class="last-updated" title="${formatDate(char.lastUpdate)}">
                            <span class="material-icons">schedule</span>
                            ${timeSinceUpdate}
                        </div>
                        <div class="view-btn">
                            View Stash <span class="material-icons">arrow_forward</span>
                        </div>
                    </div>
                </div>
            `;

            grid.appendChild(card);
        });
    } catch (error) {
        console.error('Failed to load characters:', error);
        if (loading) {
            loading.innerHTML = `
                <div class="error-state">
                    <span class="material-icons">error_outline</span>
                    <h3>Error Loading Characters</h3>
                    <p>Failed to load character data. Please check your connection and try again.</p>
                </div>`;
        } else {
            grid.innerHTML = `
                <div class="error-state">
                    <span class="material-icons">error_outline</span>
                    <h3>Error Loading Characters</h3>
                    <p>Failed to load character data. Please check your connection and try again.</p>
                </div>`;
        }
    }
}

function getTimeSinceUpdate(dateString) {
    if (!dateString) return 'Never';

    try {
        const updateDate = new Date(dateString);
        const now = new Date();
        const diffMs = now - updateDate;
        const diffMins = Math.floor(diffMs / (1000 * 60));
        const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
        const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        if (diffHours < 24) return `${diffHours}h ago`;
        if (diffDays < 7) return `${diffDays}d ago`;

        return formatDate(dateString);
    } catch (e) {
        return formatDate(dateString);
    }
}

window.addEventListener('DOMContentLoaded', loadCharacters);

// Add global function to refresh character list
window.updateCharacterList = async function () {
    const grid = document.getElementById('characterGrid');
    if (grid) {
        grid.innerHTML = '<div class="loading" id="loading">Loading characters...</div>';
        await loadCharacters();
    }
};

// Character capture animation function (simplified for index page)
window.showCharacterCaptureAnimation = function (characterClass, characterNickname) {
    console.log(`Character captured: ${characterNickname} (${characterClass})`);
    // Could add a simple notification here if needed
};
