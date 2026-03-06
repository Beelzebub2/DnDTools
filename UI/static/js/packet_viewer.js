/* ─── Packet Viewer ─── */
(function () {
    'use strict';

    /* ── Helpers ── */
    function escapeHtml(unsafe) {
        return String(unsafe).replace(/[&<"'>]/g, function (m) {
            return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[m];
        });
    }

    function showError(msg) {
        const el = document.getElementById('packet-error');
        const textEl = document.getElementById('packet-error-text');
        if (el) { el.style.display = 'flex'; }
        if (textEl) { textEl.textContent = msg; }
    }

    function clearError() {
        const el = document.getElementById('packet-error');
        const textEl = document.getElementById('packet-error-text');
        if (el) { el.style.display = 'none'; }
        if (textEl) { textEl.textContent = ''; }
    }

    // Expose for any external callers
    window.showError = showError;
    window.clearError = clearError;
    window.escapeHtml = escapeHtml;

    /* ── State ── */
    let allPacketTypes = [];
    let knownTypes = new Set();       // tracks all types we've ever seen, to detect truly new ones
    let selectedTypes = new Set();
    let hiddenTypes = new Set();
    let expandedIds = new Set(JSON.parse(localStorage.getItem('packetViewerExpanded') || '[]'));
    let filtersCollapsed = false;
    let initialLoadDone = false;

    /* ── Filter Panel Toggle ── */
    function setupFilterToggle() {
        const toggle = document.getElementById('pv-filters-toggle');
        const body = document.getElementById('packet-type-filters');
        const chevron = document.getElementById('pv-filters-chevron');
        if (!toggle || !body) return;

        toggle.addEventListener('click', function (e) {
            // Don't toggle if clicking a button inside the header
            if (e.target.closest('.pv-btn')) return;
            filtersCollapsed = !filtersCollapsed;
            body.classList.toggle('collapsed', filtersCollapsed);
            if (chevron) chevron.classList.toggle('open', !filtersCollapsed);
        });
    }

    /* ── Load Packet Types ── */
    async function loadPacketTypes() {
        try {
            const [typesRes, hiddenRes] = await Promise.all([
                fetch('/api/packet_viewer/types'),
                fetch('/api/packet_viewer/hidden')
            ]);

            if (!typesRes.ok) {
                if (typesRes.status === 403) { showError('Developer mode required to view packet types.'); return; }
                showError('Failed to load packet types: ' + typesRes.status);
                return;
            }

            if (!hiddenRes.ok) {
                hiddenTypes = new Set();
            } else {
                const hiddenList = await hiddenRes.json();
                hiddenTypes = new Set(Array.isArray(hiddenList) ? hiddenList : []);
            }

            const newTypes = await typesRes.json();
            const incomingTypes = newTypes.filter(function (name) { return /^[A-Z0-9_]+$/.test(name); });

            if (!initialLoadDone) {
                // First load: select everything that isn't hidden
                allPacketTypes = incomingTypes;
                knownTypes = new Set(incomingTypes);
                selectedTypes = new Set(incomingTypes.filter(function (t) { return !hiddenTypes.has(t); }));
                initialLoadDone = true;
                renderTypeFilters();
            } else {
                // Subsequent refreshes: only auto-select truly NEW types we haven't seen before
                var needsRerender = false;
                for (var i = 0; i < incomingTypes.length; i++) {
                    var t = incomingTypes[i];
                    if (!knownTypes.has(t)) {
                        knownTypes.add(t);
                        if (!hiddenTypes.has(t)) {
                            selectedTypes.add(t);
                        }
                        needsRerender = true;
                    }
                }
                // Remove types that no longer exist from our lists
                selectedTypes.forEach(function (s) {
                    if (!incomingTypes.includes(s)) { selectedTypes.delete(s); needsRerender = true; }
                });
                allPacketTypes = incomingTypes;
                if (needsRerender) {
                    renderTypeFilters();
                }
            }

            await loadPackets();
        } catch (error) {
            console.error('Failed to load packet types:', error);
            showError('Failed to load packet types. See console for details.');
        }
    }

    /* ── Toggle / Hide / Unhide Types ── */
    function toggleType(type) {
        if (selectedTypes.has(type)) selectedTypes.delete(type);
        else selectedTypes.add(type);
        updateChipStates();
        loadPackets();
    }

    function hideType(type, event) {
        if (event) { event.stopPropagation(); }
        var newHidden = new Set(hiddenTypes);
        newHidden.add(type);
        selectedTypes.delete(type);
        updateHiddenTypes(Array.from(newHidden));
    }

    function unhideType(type, event) {
        if (event) { event.stopPropagation(); }
        var newHidden = new Set(hiddenTypes);
        newHidden.delete(type);
        selectedTypes.add(type);
        updateHiddenTypes(Array.from(newHidden));
    }

    async function updateHiddenTypes(types) {
        try {
            var res = await fetch('/api/packet_viewer/hidden', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ types: types })
            });
            if (res.ok) {
                hiddenTypes = new Set(types);
                renderTypeFilters();
                await loadPackets();
            } else {
                var text = await res.text();
                showError('Failed to update hidden types: ' + text);
            }
        } catch (err) {
            console.error('Failed to update hidden types', err);
            showError('Failed to update hidden types. See console for details.');
        }
    }

    // Expose for inline handlers (not used anymore but keeps compat)
    window.toggleType = toggleType;
    window.hideType = hideType;
    window.unhideType = unhideType;

    /* ── Update chip active/inactive visuals without full re-render ── */
    function updateChipStates() {
        var chips = document.querySelectorAll('#packet-type-filters .pv-filter-chip');
        chips.forEach(function (chip) {
            var type = chip.dataset.type;
            if (!type) return;
            chip.classList.toggle('active', selectedTypes.has(type));
        });
    }

    /* ── Render Type Filter Chips ── */
    function renderTypeFilters() {
        var container = document.getElementById('packet-type-filters');
        if (!container) return;

        if (allPacketTypes.length === 0) {
            container.innerHTML = '<div class="no-packets"><span class="material-icons">filter_list_off</span><h3>No packet types</h3><p>No known packet types available yet.</p></div>';
            selectedTypes = new Set();
            return;
        }

        var fragment = document.createDocumentFragment();

        allPacketTypes.forEach(function (type) {
            var isSelected = selectedTypes.has(type);

            var chip = document.createElement('div');
            chip.className = 'pv-filter-chip';
            chip.dataset.type = type;
            if (isSelected) chip.classList.add('active');

            // Checkbox indicator
            var check = document.createElement('span');
            check.className = 'pv-chip-check';
            chip.appendChild(check);

            // Label
            var label = document.createElement('span');
            label.className = 'pv-chip-label';
            label.textContent = type;
            chip.appendChild(label);

            // Click chip to toggle selection
            chip.addEventListener('click', function () {
                toggleType(type);
            });

            fragment.appendChild(chip);
        });

        container.innerHTML = '';
        container.appendChild(fragment);

        // Restore collapsed state
        if (filtersCollapsed) {
            container.classList.add('collapsed');
        }

        // Wire select/deselect buttons
        var selBtn = document.getElementById('select-all');
        var deselBtn = document.getElementById('deselect-all');

        if (selBtn) {
            selBtn.onclick = function (e) {
                e.stopPropagation();
                selectedTypes = new Set(allPacketTypes.filter(function (t) { return !hiddenTypes.has(t); }));
                updateChipStates();
                loadPackets();
            };
        }
        if (deselBtn) {
            deselBtn.onclick = function (e) {
                e.stopPropagation();
                selectedTypes.clear();
                updateChipStates();
                loadPackets();
            };
        }
    }

    /* ── Load Packets ── */
    async function loadPackets() {
        clearError();
        try {
            var types = Array.from(selectedTypes);

            // Nothing selected → show empty state immediately, no fetch needed
            if (types.length === 0) {
                renderPackets([]);
                return;
            }

            // Always pass explicit types so the backend only returns what's selected
            var url = '/api/packets?' + types.map(function (t) { return 'types=' + encodeURIComponent(t); }).join('&');

            var response = await fetch(url);
            if (response.ok) {
                var packets = await response.json();
                renderPackets(packets);
            } else if (response.status === 403) {
                showError('Developer mode required to view packets.');
            } else {
                showError('Failed to load packets: ' + response.status);
            }
        } catch (error) {
            console.error('Failed to load packets:', error);
            showError('Failed to load packets. See console for details.');
        }
    }

    /* ── Render Packets ── */
    function renderPackets(packets) {
        var container = document.getElementById('packets-list');
        var countTextEl = document.getElementById('packet-count-text');
        if (countTextEl) {
            countTextEl.textContent = packets.length + ' packet' + (packets.length !== 1 ? 's' : '');
        }

        if (!container) return;

        if (!packets || packets.length === 0) {
            container.innerHTML = '<div class="no-packets"><span class="material-icons" aria-hidden="true">wifi_tethering</span><h3>No packets captured yet</h3><p>Start capture to see packets appear here in real-time.</p></div>';
            return;
        }

        // Clear empty-state placeholder if present
        var noPacketsEl = container.querySelector('.no-packets');
        if (noPacketsEl) noPacketsEl.remove();

        var incomingIds = packets.map(function (p) { return p.id; });
        var existingNodes = Array.from(container.querySelectorAll('.packet-item'));

        // Remove nodes no longer present
        existingNodes.forEach(function (node) {
            var id = Number(node.dataset.packetId);
            if (!incomingIds.includes(id)) node.remove();
        });

        // Insert/update nodes
        for (var idx = 0; idx < packets.length; idx++) {
            var packet = packets[idx];
            var id = packet.id;
            var existing = container.querySelector('.packet-item[data-packet-id="' + id + '"]');

            if (existing) {
                // Update timestamp
                var tsEl = existing.querySelector('.packet-timestamp');
                if (tsEl && tsEl.textContent !== packet.timestamp) tsEl.textContent = packet.timestamp;

                // Update JSON content
                var jsonEl = existing.querySelector('.packet-json');
                var newContent = formatJsonContent(packet);
                if (jsonEl && jsonEl.innerHTML !== newContent) jsonEl.innerHTML = newContent;

                // Update expanded state
                if (jsonEl) {
                    var isExpanded = expandedIds.has(id);
                    jsonEl.classList.toggle('expanded', isExpanded);
                    existing.classList.toggle('expanded', isExpanded);
                }

                // Maintain order
                var children = container.children;
                if (children[idx] !== existing) {
                    if (children.length > idx) container.insertBefore(existing, children[idx]);
                    else container.appendChild(existing);
                }
            } else {
                container.insertBefore(createPacketNode(packet, idx), container.children[idx] || null);
            }
        }
    }

    /* ── Create a single packet node ── */
    function createPacketNode(packet, index) {
        var id = packet.id;
        var isExpanded = expandedIds.has(id);

        var node = document.createElement('div');
        node.className = 'packet-item' + (isExpanded ? ' expanded' : '');
        node.dataset.packetId = id;
        node.style.animationDelay = Math.min(index * 30, 300) + 'ms';

        // Header
        var header = document.createElement('div');
        header.className = 'packet-header';

        // Left side
        var left = document.createElement('div');
        left.className = 'left';

        var iconWrap = document.createElement('div');
        iconWrap.className = 'packet-type-icon';
        var icon = document.createElement('span');
        icon.className = 'material-icons';
        icon.setAttribute('aria-hidden', 'true');
        icon.textContent = getPacketIcon(packet.type);
        iconWrap.appendChild(icon);
        left.appendChild(iconWrap);

        var info = document.createElement('div');
        info.className = 'packet-info';

        var typeSpan = document.createElement('span');
        typeSpan.className = 'packet-type';
        typeSpan.textContent = packet.type || 'Unknown';
        info.appendChild(typeSpan);

        var metaLine = document.createElement('div');
        metaLine.className = 'packet-meta';

        var tsSpan = document.createElement('span');
        tsSpan.className = 'packet-timestamp';
        tsSpan.textContent = packet.timestamp || '—';
        metaLine.appendChild(tsSpan);

        if (packet.raw_length) {
            var sizeSpan = document.createElement('span');
            sizeSpan.className = 'packet-badge';
            sizeSpan.textContent = formatBytes(packet.raw_length);
            sizeSpan.title = packet.raw_length + ' bytes';
            metaLine.appendChild(sizeSpan);
        }

        if (packet.proto_type !== undefined) {
            var protoSpan = document.createElement('span');
            protoSpan.className = 'packet-badge';
            protoSpan.textContent = '#' + packet.proto_type;
            protoSpan.title = 'Proto type ID';
            metaLine.appendChild(protoSpan);
        }

        if (packet.parsed === false) {
            var unparsedSpan = document.createElement('span');
            unparsedSpan.className = 'packet-badge packet-badge--warn';
            unparsedSpan.textContent = 'unparsed';
            unparsedSpan.title = 'Could not deserialize proto message';
            metaLine.appendChild(unparsedSpan);
        }

        info.appendChild(metaLine);

        left.appendChild(info);
        header.appendChild(left);

        // Right side actions
        var actions = document.createElement('div');
        actions.className = 'packet-header-actions';

        // Copy button — show for any packet that has JSON data (even empty {})
        var hasJsonData = packet.json !== null && packet.json !== undefined;
        if (hasJsonData) {
            var copyBtn = document.createElement('button');
            copyBtn.type = 'button';
            copyBtn.className = 'packet-copy-btn';
            copyBtn.title = 'Copy JSON to clipboard';

            var copyIcon = document.createElement('span');
            copyIcon.className = 'material-icons';
            copyIcon.textContent = 'content_copy';
            copyBtn.appendChild(copyIcon);

            var copyFb = document.createElement('span');
            copyFb.className = 'packet-copy-feedback';
            copyBtn.appendChild(copyFb);

            copyBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                copyText(JSON.stringify(packet.json, null, 2), copyBtn);
            });
            actions.appendChild(copyBtn);
        }

        // Expand chevron
        var expandIcon = document.createElement('div');
        expandIcon.className = 'pv-expand-icon';
        var chevron = document.createElement('span');
        chevron.className = 'material-icons';
        chevron.textContent = 'expand_more';
        expandIcon.appendChild(chevron);
        actions.appendChild(expandIcon);

        header.appendChild(actions);

        // Make the whole header clickable for toggle
        header.addEventListener('click', function () {
            toggleJsonId(id);
        });

        node.appendChild(header);

        // JSON body
        var jsonDiv = document.createElement('div');
        jsonDiv.className = 'packet-json' + (isExpanded ? ' expanded' : '');
        jsonDiv.id = 'json-' + id;
        jsonDiv.innerHTML = formatJsonContent(packet);
        node.appendChild(jsonDiv);

        return node;
    }

    /* ── Format JSON panel content based on packet state ── */
    function formatJsonContent(packet) {
        if (packet.json !== null && packet.json !== undefined) {
            var jsonStr = JSON.stringify(packet.json, null, 2);
            if (jsonStr === '{}') {
                return '<div class="packet-json-empty"><span class="material-icons">check_circle</span> Empty response (no payload data)</div>';
            }
            return '<pre>' + escapeHtml(jsonStr) + '</pre>';
        }
        if (packet.parsed === false) {
            return '<div class="packet-json-empty packet-json-empty--warn"><span class="material-icons">warning</span> Could not parse proto message (raw_length: ' + (packet.raw_length || '?') + ' bytes)</div>';
        }
        return '<div class="packet-json-empty"><span class="material-icons">info</span> No parsed JSON available</div>';
    }

    /* ── Format bytes to human readable ── */
    function formatBytes(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    /* ── Get an appropriate material icon for packet type ── */
    function getPacketIcon(type) {
        if (!type) return 'description';
        var t = type.toUpperCase();
        if (t.indexOf('LOGIN') >= 0 || t.indexOf('AUTH') >= 0) return 'login';
        if (t.indexOf('CHAT') >= 0 || t.indexOf('MESSAGE') >= 0) return 'chat';
        if (t.indexOf('MOVE') >= 0 || t.indexOf('POSITION') >= 0) return 'open_with';
        if (t.indexOf('INVENTORY') >= 0 || t.indexOf('STASH') >= 0) return 'inventory_2';
        if (t.indexOf('MERCHANT') >= 0 || t.indexOf('SHOP') >= 0) return 'store';
        if (t.indexOf('QUEST') >= 0) return 'assignment';
        if (t.indexOf('CRAFT') >= 0) return 'build';
        if (t.indexOf('RECOVERY') >= 0) return 'restore';
        if (t.indexOf('EXPRESS') >= 0 || t.indexOf('DELIVERY') >= 0 || t.indexOf('PARCEL') >= 0) return 'local_shipping';
        if (t.indexOf('STOCK') >= 0 || t.indexOf('BUY') >= 0 || t.indexOf('SELL') >= 0) return 'shopping_cart';
        if (t.indexOf('GEAR') >= 0 || t.indexOf('ITEM') >= 0 || t.indexOf('EQUIP') >= 0) return 'shield';
        if (t.indexOf('PARTY') >= 0 || t.indexOf('GROUP') >= 0) return 'group';
        if (t.indexOf('TRADE') >= 0 || t.indexOf('MARKET') >= 0) return 'storefront';
        if (t.indexOf('DAMAGE') >= 0 || t.indexOf('KILL') >= 0 || t.indexOf('COMBAT') >= 0) return 'gavel';
        if (t.indexOf('SPAWN') >= 0) return 'person_add';
        if (t.indexOf('LOBBY') >= 0 || t.indexOf('MATCH') >= 0) return 'sports_esports';
        if (t.indexOf('ERROR') >= 0 || t.indexOf('FAIL') >= 0) return 'error_outline';
        if (t.indexOf('ALIVE') >= 0 || t.indexOf('PING') >= 0 || t.indexOf('HEARTBEAT') >= 0 || t.indexOf('KEEP_ALIVE') >= 0) return 'favorite';
        if (t.indexOf('POLICY') >= 0 || t.indexOf('SERVICE') >= 0) return 'policy';
        if (t.indexOf('CHARACTER') >= 0 || t.indexOf('CLASS') >= 0) return 'person';
        if (t.indexOf('FRIEND') >= 0) return 'people';
        if (t.indexOf('RANKING') >= 0 || t.indexOf('RANK') >= 0) return 'leaderboard';
        if (t.indexOf('RELIGION') >= 0) return 'auto_awesome';
        return 'description';
    }

    /* ── Toggle JSON expand/collapse ── */
    function toggleJsonId(id) {
        var jsonEl = document.getElementById('json-' + id);
        var itemEl = jsonEl ? jsonEl.closest('.packet-item') : null;
        if (!jsonEl) return;

        var isExpanded = jsonEl.classList.toggle('expanded');
        if (itemEl) itemEl.classList.toggle('expanded', isExpanded);

        if (isExpanded) expandedIds.add(id);
        else expandedIds.delete(id);

        localStorage.setItem('packetViewerExpanded', JSON.stringify(Array.from(expandedIds)));
    }
    window.toggleJsonId = toggleJsonId;

    /* ── Copy with feedback ── */
    function copyText(text, buttonEl) {
        if (!navigator.clipboard) {
            try { window.prompt('Copy text:', text); } catch (e) { /* ignore */ }
            return;
        }
        navigator.clipboard.writeText(text).then(function () {
            if (window.showNotification) {
                try { window.showNotification('Copied to clipboard', 'info'); } catch (e) { /* ignore */ }
            }
            if (buttonEl) {
                var fb = buttonEl.querySelector('.packet-copy-feedback');
                if (fb) {
                    fb.textContent = 'Copied';
                    fb.classList.add('visible');
                    setTimeout(function () { fb.classList.remove('visible'); fb.textContent = ''; }, 1200);
                }
            }
        }).catch(function (err) {
            console.error('Clipboard error', err);
            if (buttonEl) {
                var fb = buttonEl.querySelector('.packet-copy-feedback');
                if (fb) {
                    fb.textContent = 'Failed';
                    fb.classList.add('visible');
                    setTimeout(function () { fb.classList.remove('visible'); fb.textContent = ''; }, 1200);
                }
            }
        });
    }

    /* ── Auto-refresh ── */
    var _packetPollId = setInterval(loadPackets, 2000);
    var _typePollId = setInterval(loadPacketTypes, 2000);

    /* ── Init ── */
    function packetViewerInit() {
        setupFilterToggle();
        loadPacketTypes();

        // Register cleanup for AJAX router
        window.__pageCleanup = window.__pageCleanup || [];
        window.__pageCleanup.push(function () {
            clearInterval(_packetPollId);
            clearInterval(_typePollId);
            window.showError = undefined;
            window.clearError = undefined;
            window.escapeHtml = undefined;
            window.toggleType = undefined;
            window.hideType = undefined;
            window.unhideType = undefined;
            window.toggleJsonId = undefined;
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', packetViewerInit, { once: true });
    } else {
        packetViewerInit();
    }
})();
