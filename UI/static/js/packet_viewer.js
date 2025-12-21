(function () {
    // Expose a couple helpers onto window for inline handlers
    window.showError = function (msg) { const el = document.getElementById('packet-error'); if (el) { el.style.display = 'block'; el.textContent = msg; } };
    window.clearError = function () { const el = document.getElementById('packet-error'); if (el) { el.style.display = 'none'; el.textContent = ''; } };
    window.escapeHtml = function (unsafe) { return String(unsafe).replace(/[&<"'>]/g, function (m) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[m]; }); };

    // State
    window.packetViewer = window.packetViewer || {};
    let allPacketTypes = [];
    let selectedTypes = new Set();
    let hiddenTypes = new Set();
    let expandedIds = new Set(JSON.parse(localStorage.getItem('packetViewerExpanded') || '[]'));

    // Load captured types + hidden list
    async function loadPacketTypes() {
        try {
            const [typesRes, hiddenRes] = await Promise.all([fetch('/api/packet_viewer/types'), fetch('/api/packet_viewer/hidden')]);
            if (!typesRes.ok) {
                if (typesRes.status === 403) { showError('Developer mode required to view packet types.'); return; }
                showError(`Failed to load packet types: ${typesRes.status}`); return;
            }
            if (!hiddenRes.ok) hiddenTypes = new Set();
            else {
                const hiddenList = await hiddenRes.json();
                hiddenTypes = new Set(Array.isArray(hiddenList) ? hiddenList : []);
            }

            const newTypes = await typesRes.json();

            const prevSelected = new Set(selectedTypes);
            allPacketTypes = newTypes.filter(name => /^[A-Z0-9_]+$/.test(name));

            if (!prevSelected || prevSelected.size === 0) {
                selectedTypes = new Set(allPacketTypes.filter(t => !hiddenTypes.has(t)));
            } else {
                for (const t of allPacketTypes) {
                    if (!hiddenTypes.has(t) && !prevSelected.has(t)) prevSelected.add(t);
                }
                for (const s of Array.from(prevSelected)) {
                    if (!allPacketTypes.includes(s)) prevSelected.delete(s);
                }
                selectedTypes = prevSelected;
            }

            renderTypeFilters();
            await loadPackets();
        } catch (error) {
            console.error('Failed to load packet types:', error);
            showError('Failed to load packet types. See console for details.');
        }
    }

    // Expose toggle function for inline handlers
    window.toggleType = function (type) {
        if (selectedTypes.has(type)) selectedTypes.delete(type);
        else selectedTypes.add(type);
        loadPackets();
    };

    window.hideType = function (type) {
        const newHidden = new Set(hiddenTypes);
        newHidden.add(type);
        updateHiddenTypes(Array.from(newHidden));
    };

    window.unhideType = function (type) {
        const newHidden = new Set(hiddenTypes);
        newHidden.delete(type);
        updateHiddenTypes(Array.from(newHidden));
    };

    async function updateHiddenTypes(types) {
        try {
            const res = await fetch('/api/packet_viewer/hidden', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ types }) });
            if (res.ok) {
                hiddenTypes = new Set(types);
                renderTypeFilters();
                await loadPackets();
            } else {
                const text = await res.text();
                showError('Failed to update hidden types: ' + text);
            }
        } catch (err) {
            console.error('Failed to update hidden types', err);
            showError('Failed to update hidden types. See console for details.');
        }
    }

    function renderTypeFilters() {
        const container = document.getElementById('packet-type-filters');
        if (!container) return;
        if (allPacketTypes.length === 0) {
            container.innerHTML = '<div class="no-packets">No known packet types available.</div>';
            selectedTypes = new Set();
            return;
        }

        container.innerHTML = allPacketTypes.map(type => `
        <div style="display:flex;align-items:center;gap:8px;">
            <label class="packet-type-checkbox">
                <input type="checkbox" value="${type}" ${selectedTypes.has(type) ? 'checked' : ''} onchange="toggleType('${type}')">
                ${type}
            </label>
            ${hiddenTypes.has(type) ? `<button class="toggle-json" onclick="unhideType('${type}')">Unhide</button>` : `<button class="toggle-json" onclick="hideType('${type}')">Hide</button>`}
        </div>
    `).join('');

        selectedTypes = new Set(allPacketTypes.filter(t => !hiddenTypes.has(t)));

        const sel = document.getElementById('select-all');
        const desel = document.getElementById('deselect-all');
        if (sel) sel.onclick = () => { selectedTypes = new Set(allPacketTypes.filter(t => !hiddenTypes.has(t))); document.querySelectorAll('#packet-type-filters input').forEach(cb => cb.checked = true); loadPackets(); };
        if (desel) desel.onclick = () => { selectedTypes.clear(); document.querySelectorAll('#packet-type-filters input').forEach(cb => cb.checked = false); loadPackets(); };
    }

    async function loadPackets() {
        clearError();
        try {
            const types = Array.from(selectedTypes);
            const url = (types.length === 0 || types.length === allPacketTypes.length) ? '/api/packets' : `/api/packets?${types.map(t => `types=${encodeURIComponent(t)}`).join('&')}`;
            const response = await fetch(url);
            if (response.ok) {
                const packets = await response.json();
                renderPackets(packets);
            } else if (response.status === 403) {
                showError('Developer mode required to view packets.');
            } else {
                showError(`Failed to load packets: ${response.status}`);
            }
        } catch (error) {
            console.error('Failed to load packets:', error);
            showError('Failed to load packets. See console for details.');
        }
    }

    function renderPackets(packets) {
        const container = document.getElementById('packets-list');
        const countEl = document.getElementById('packet-count');
        if (countEl) countEl.textContent = `${packets.length} packets`;

        if (!container) return;
        if (!packets || packets.length === 0) {
            container.innerHTML = '<div class="no-packets">No packets captured yet. Start capture to see packets.</div>';
            return;
        }

        const incomingIds = packets.map(p => p.id);
        const existingNodes = Array.from(container.querySelectorAll('.packet-item'));

        existingNodes.forEach(node => {
            const id = Number(node.dataset.packetId);
            if (!incomingIds.includes(id)) node.remove();
        });

        for (let idx = 0; idx < packets.length; idx++) {
            const packet = packets[idx];
            const id = packet.id;
            let existing = container.querySelector(`.packet-item[data-packet-id="${id}"]`);
            if (existing) {
                const tsEl = existing.querySelector('.packet-timestamp');
                if (tsEl && tsEl.textContent !== packet.timestamp) tsEl.textContent = packet.timestamp;
                const jsonEl = existing.querySelector('.packet-json');
                const newContent = packet.json ? `<pre>${escapeHtml(JSON.stringify(packet.json, null, 2))}</pre>` : '<em>No parsed JSON</em>';
                if (jsonEl && jsonEl.innerHTML !== newContent) jsonEl.innerHTML = newContent;
                if (jsonEl) {
                    if (expandedIds.has(id)) jsonEl.classList.add('expanded'); else jsonEl.classList.remove('expanded');
                }
                const children = container.children;
                if (children[idx] !== existing) {
                    if (children.length > idx) container.insertBefore(existing, children[idx]); else container.appendChild(existing);
                }
            } else {
                const node = document.createElement('div');
                node.className = 'packet-item';
                node.dataset.packetId = id;

                const header = document.createElement('div');
                header.className = 'packet-header';

                const typeSpan = document.createElement('span');
                typeSpan.className = 'packet-type';
                typeSpan.textContent = packet.type;

                const tsSpan = document.createElement('span');
                tsSpan.className = 'packet-timestamp';
                tsSpan.textContent = packet.timestamp;

                const btn = document.createElement('button');
                btn.className = 'toggle-json';
                btn.textContent = 'Toggle JSON';
                btn.addEventListener('click', () => toggleJsonId(id));

                header.appendChild(typeSpan);
                header.appendChild(tsSpan);
                header.appendChild(btn);

                const jsonDiv = document.createElement('div');
                jsonDiv.className = 'packet-json' + (expandedIds.has(id) ? ' expanded' : '');
                jsonDiv.id = `json-${id}`;
                jsonDiv.innerHTML = packet.json ? `<pre>${escapeHtml(JSON.stringify(packet.json, null, 2))}</pre>` : '<em>No parsed JSON</em>';

                node.appendChild(header);
                node.appendChild(jsonDiv);

                const children = container.children;
                if (children.length > idx) container.insertBefore(node, children[idx]); else container.appendChild(node);
            }
        }
    }

    window.toggleJsonId = function (id) {
        const jsonEl = document.getElementById(`json-${id}`);
        if (!jsonEl) return;
        const isExpanded = jsonEl.classList.toggle('expanded');
        if (isExpanded) expandedIds.add(id);
        else expandedIds.delete(id);
        localStorage.setItem('packetViewerExpanded', JSON.stringify(Array.from(expandedIds)));
    };

    // Auto-refresh
    setInterval(loadPackets, 2000);
    setInterval(loadPacketTypes, 2000);

    // Start when DOM is ready
    document.addEventListener('DOMContentLoaded', () => loadPacketTypes());
})();