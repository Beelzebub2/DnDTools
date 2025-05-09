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

async function loadCharacters() {
    const grid = document.querySelector('.character-grid');
    const loading = document.getElementById('loading');
    try {
        let characters;
        if (window.pywebview && window.pywebview.api) {
            characters = await window.pywebview.api.get_characters();
        } else {
            const response = await fetch('/api/characters');
            characters = await response.json();
        }
        console.log('Characters received:', characters);
        loading.remove();

        if (!characters || characters.length === 0) {
            grid.innerHTML = `
                <div class="empty-state">
                    <span class="material-icons">person_off</span>
                    <h3>No Characters Found</h3>
                    <p>Go to Record Character to capture your first character</p>
                </div>`;
            return;
        }

        characters.forEach(char => {
            const card = document.createElement('div');
            card.className = 'character-card';
            card.onclick = () => window.location.href = `/character/${char.id}`;
            const classIcon = getClassIcon(char.class);
            card.innerHTML = `
                <div class="card-header">
                    <span class="class-icon material-icons">${classIcon}</span>
                    <div class="character-name">${char.nickname}</div>
                </div>
                <div class="character-info">
                    <div class="info-row">
                        <span class="info-label">Class:</span>
                        <span class="info-value">${char.class}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Level:</span>
                        <span class="info-value">${char.level}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Last Updated:</span>
                        <span class="info-value">${formatDate(char.lastUpdate)}</span>
                    </div>
                </div>`;
            grid.appendChild(card);
        });
    } catch (error) {
        console.error('Failed to load characters:', error);
        loading.innerHTML = `
            <div class="error-state">
                <span class="material-icons">error_outline</span>
                <h3>Error Loading Characters</h3>
                <p>${error.toString()}</p>
            </div>`;
    }
}

window.addEventListener('DOMContentLoaded', loadCharacters);

function getClassIcon(className) {
    const icons = {
        'Fighter': 'sports_kabaddi',
        'Ranger': 'gps_fixed',
        'Rogue': 'vpn_key',
        'Wizard': 'auto_fix_high',
        'Cleric': 'health_and_safety',
        'Warlock': 'whatshot',
        'Barbarian': 'security'
    };
    return icons[className] || 'person';
}

// Add global function to refresh character list
window.updateCharacterList = async function () {
    const grid = document.querySelector('.character-grid');
    if (grid) {
        grid.innerHTML = '<div class="loading" id="loading">Loading characters...</div>';
        await loadCharacters();
    }
};
