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

        results.forEach(result => {
            const item = document.createElement('div');
            item.className = 'result-item';
            const rarityColor = rarityColors[result.item.rarity] || '#ffffff';

            item.innerHTML = `
                <div class="character-name">${result.nickname} ${result.class} LvL ${result.level} Slot: ${result.slotId}</div>
                <div class="character-info">
                    <div>${result.item.name}</div>
                    <div>${result.item.rarity}</div>
                    <div>${result.itemCount}</div>
                </div>
                <div class="item-popup" style="position: absolute; display: none; z-index: 100; pointer-events: none;">
                    <div class="item-header" style="background-color: ${rarityColor}; color: #000;">${result.item.name}</div>
                    <div class="item-properties">
                        <div class="primary-props">${formatPrimaryProps(result.item.pp)}</div>
                        <div class="secondary-props">${formatSecondaryProps(result.item.sp)}</div>
                    </div>
                    <div class="item-meta">
                        <div>Rarity: ${result.item.rarity}</div>
                        <div>Count: ${result.itemCount}</div>
                    </div>
                </div>
            `;

            // Add event listeners for mouse interactions
            const popup = item.querySelector('.item-popup');

            item.addEventListener('mouseenter', (e) => {
                popup.style.display = 'block';
            });

            item.addEventListener('mousemove', (e) => {
                // Only move the tooltip within the item box area
                const offsetX = 15;
                const offsetY = 15;

                // Get bounding rect of the item box
                const rect = item.getBoundingClientRect();

                // Mouse position relative to viewport
                let left = e.clientX + offsetX;
                let top = e.clientY + offsetY;

                // Get viewport dimensions
                const viewportWidth = window.innerWidth;
                const viewportHeight = window.innerHeight;

                // Get tooltip dimensions
                const tooltipWidth = popup.offsetWidth;
                const tooltipHeight = popup.offsetHeight;

                // Adjust if tooltip would go beyond right edge
                if (left + tooltipWidth > viewportWidth) {
                    left = e.clientX - tooltipWidth - offsetX;
                }

                // Adjust if tooltip would go beyond bottom edge
                if (top + tooltipHeight > viewportHeight) {
                    top = e.clientY - tooltipHeight - offsetY;
                }

                // Ensure tooltip doesn't go beyond left or top edges
                left = Math.max(0, left);
                top = Math.max(0, top);

                popup.style.left = `${left - rect.left}px`;
                popup.style.top = `${top - rect.top}px`;
            });

            item.addEventListener('mouseleave', () => {
                popup.style.display = 'none';
            });

            item.onclick = () => {
                fetch(`/api/character/${result.id}/current-stash/${result.stash_id}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                })
                    .catch(error => {
                        console.error('Error:', error);
                    });
                window.location.href = `/character/${result.id}`;
            };

            // Set item to relative positioning so tooltip is positioned within it
            item.style.position = 'relative';

            container.appendChild(item);
        });
    };

    const performSearch = async (query) => {
        searchResults.innerHTML = '<div class="loading">Searching...</div>';

        try {
            let details;
            if (window.pywebview && window.pywebview.api) {
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
