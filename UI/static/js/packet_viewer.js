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

    function loadExpandedIds() {
        try {
            const raw = localStorage.getItem('packetViewerExpanded');
            if (!raw) return new Set();

            const parsed = JSON.parse(raw);
            if (!Array.isArray(parsed)) {
                throw new TypeError('packetViewerExpanded must be an array');
            }

            return new Set(parsed.filter(function (id) {
                return Number.isFinite(Number(id));
            }));
        } catch (error) {
            // A partial/corrupt browser-storage write must never prevent the
            // packet viewer from loading for the rest of the app session.
            try { localStorage.removeItem('packetViewerExpanded'); } catch (storageError) { /* ignore */ }
            return new Set();
        }
    }

    function saveExpandedIds(expandedIds) {
        try {
            localStorage.setItem('packetViewerExpanded', JSON.stringify(Array.from(expandedIds)));
        } catch (error) {
            // Expansion state is optional. Browsing packets must continue when
            // storage is unavailable, disabled, or over quota.
        }
    }

    /* ── State ── */
    let allPacketTypes = [];
    let knownTypes = new Set();       // tracks the previous recent-type snapshot
    let selectedTypes = new Set();
    let hiddenTypes = new Set();
    let expandedIds = loadExpandedIds();
    let filtersCollapsed = false;
    let initialLoadDone = false;
    let packetCache = new Map();
    let lastSeenPacketId = null;
    let packetsRequestInFlight = false;
    let packetsReloadPending = false;
    let typesRequestInFlight = false;
    let disposed = false;
    let packetPollId = null;
    let typePollId = null;
    const activeControllers = new Set();
    const MAX_RENDERED_PACKETS = 250;

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
        if (disposed || typesRequestInFlight) return false;
        typesRequestInFlight = true;
        var controller = new AbortController();
        activeControllers.add(controller);
        try {
            const [typesRes, hiddenRes] = await Promise.all([
                fetch('/api/packet_viewer/types', { signal: controller.signal, cache: 'no-store' }),
                fetch('/api/packet_viewer/hidden', { signal: controller.signal, cache: 'no-store' })
            ]);

            if (!typesRes.ok) {
                if (typesRes.status === 403) { showError('Developer mode required to view packet types.'); return false; }
                showError('Failed to load packet types: ' + typesRes.status);
                return false;
            }

            if (!hiddenRes.ok) {
                hiddenTypes = new Set();
            } else {
                const hiddenList = await hiddenRes.json();
                hiddenTypes = new Set(Array.isArray(hiddenList) ? hiddenList : []);
            }

            const newTypes = await typesRes.json();
            const incomingTypes = newTypes.filter(function (name) { return /^[A-Z0-9_]+$/.test(name); });
            var selectionChanged = false;

            if (!initialLoadDone) {
                // First load: select everything that isn't hidden
                allPacketTypes = incomingTypes;
                knownTypes = new Set(incomingTypes);
                selectedTypes = new Set(incomingTypes.filter(function (t) { return !hiddenTypes.has(t); }));
                initialLoadDone = true;
                selectionChanged = true;
                renderTypeFilters();
            } else {
                // Subsequent refreshes: auto-select types new to this snapshot.
                var needsRerender = false;
                for (var i = 0; i < incomingTypes.length; i++) {
                    var t = incomingTypes[i];
                    if (!knownTypes.has(t)) {
                        knownTypes.add(t);
                        if (!hiddenTypes.has(t)) {
                            selectedTypes.add(t);
                        }
                        needsRerender = true;
                        selectionChanged = true;
                    }
                }
                // Remove types that no longer exist from our lists
                selectedTypes.forEach(function (s) {
                    if (!incomingTypes.includes(s)) {
                        selectedTypes.delete(s);
                        needsRerender = true;
                        selectionChanged = true;
                    }
                });
                allPacketTypes = incomingTypes;
                // Recent packet types come from bounded history, so a type can
                // disappear and later return. Forget absent types so returning
                // ones are rendered and selected again.
                knownTypes = new Set(incomingTypes);
                if (needsRerender) {
                    renderTypeFilters();
                }
            }
            return selectionChanged;
        } catch (error) {
            if (error && error.name === 'AbortError') return false;
            console.error('Failed to load packet types:', error);
            showError('Failed to load packet types. See console for details.');
            return false;
        } finally {
            activeControllers.delete(controller);
            typesRequestInFlight = false;
        }
    }

    /* ── Toggle / Hide / Unhide Types ── */
    function toggleType(type) {
        if (selectedTypes.has(type)) selectedTypes.delete(type);
        else selectedTypes.add(type);
        updateChipStates();
        loadPackets({ reset: true });
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
                await loadPackets({ reset: true });
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
                loadPackets({ reset: true });
            };
        }
        if (deselBtn) {
            deselBtn.onclick = function (e) {
                e.stopPropagation();
                selectedTypes.clear();
                updateChipStates();
                loadPackets({ reset: true });
            };
        }
    }

    /* ── Load Packets ── */
    async function loadPackets(options) {
        options = options || {};
        var reset = options.reset === true;
        if (disposed) return;
        if (packetsRequestInFlight) {
            if (reset) packetsReloadPending = true;
            return;
        }

        packetsRequestInFlight = true;
        var controller = new AbortController();
        activeControllers.add(controller);
        try {
            var types = Array.from(selectedTypes);

            // Nothing selected → show empty state immediately, no fetch needed
            if (types.length === 0) {
                packetCache.clear();
                lastSeenPacketId = null;
                renderPackets([]);
                return;
            }

            // Initial/filter reads are capped snapshots. Polls only request ids
            // newer than the latest record already rendered.
            var params = types.map(function (t) { return 'types=' + encodeURIComponent(t); });
            params.push('limit=' + MAX_RENDERED_PACKETS);
            if (!reset && lastSeenPacketId !== null) {
                params.push('after_id=' + encodeURIComponent(lastSeenPacketId));
            }
            var url = '/api/packets?' + params.join('&');

            var response = await fetch(url, {
                signal: controller.signal,
                cache: 'no-store'
            });
            if (response.ok) {
                var packets = await response.json();
                if (reset) {
                    packetCache.clear();
                    lastSeenPacketId = null;
                }

                packets.forEach(function (packet) {
                    var id = Number(packet.id);
                    if (!Number.isFinite(id)) return;
                    packetCache.set(id, packet);
                    if (lastSeenPacketId === null || id > lastSeenPacketId) {
                        lastSeenPacketId = id;
                    }
                });

                while (packetCache.size > MAX_RENDERED_PACKETS) {
                    packetCache.delete(packetCache.keys().next().value);
                }

                clearError();
                renderPackets(Array.from(packetCache.values()).sort(function (a, b) {
                    return Number(a.id) - Number(b.id);
                }));
            } else if (response.status === 403) {
                showError('Developer mode required to view packets.');
            } else {
                showError('Failed to load packets: ' + response.status);
            }
        } catch (error) {
            if (error && error.name === 'AbortError') return;
            console.error('Failed to load packets:', error);
            showError('Failed to load packets. See console for details.');
        } finally {
            activeControllers.delete(controller);
            packetsRequestInFlight = false;
            if (packetsReloadPending && !disposed) {
                packetsReloadPending = false;
                loadPackets({ reset: true });
            }
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

        var incomingIds = new Set(packets.map(function (p) { return Number(p.id); }));
        var existingNodes = Array.from(container.querySelectorAll('.packet-item'));
        var existingById = new Map();

        // Remove nodes no longer present
        existingNodes.forEach(function (node) {
            var id = Number(node.dataset.packetId);
            if (!incomingIds.has(id)) node.remove();
            else existingById.set(id, node);
        });

        // Expanded ids used to grow for the entire process lifetime. Keep only
        // ids that can still be rendered.
        var expandedChanged = false;
        expandedIds.forEach(function (id) {
            if (!incomingIds.has(Number(id))) {
                expandedIds.delete(id);
                expandedChanged = true;
            }
        });
        if (expandedChanged) {
            saveExpandedIds(expandedIds);
        }

        // Packet records are immutable. Reuse existing nodes and only format
        // JSON for newly arrived packets.
        for (var idx = 0; idx < packets.length; idx++) {
            var packet = packets[idx];
            var id = Number(packet.id);
            var node = existingById.get(id);
            if (!node) {
                node = createPacketNode(packet, idx);
                existingById.set(id, node);
            }

            var currentAtIndex = container.children[idx];
            if (currentAtIndex !== node) {
                container.insertBefore(node, currentAtIndex || null);
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
        if (packet.json_omitted) {
            var omittedReasons = {
                wire_payload_too_large: 'Wire payload is too large to expand safely',
                expanded_json_too_large: 'Expanded JSON exceeds the packet viewer memory limit',
                json_conversion_failed: 'Parsed message could not be converted to JSON'
            };
            var reason = omittedReasons[packet.json_omitted_reason] ||
                'Packet JSON was omitted to protect memory';
            return '<div class="packet-json-empty packet-json-empty--warn"><span class="material-icons">data_usage</span> ' +
                escapeHtml(reason) + ' (' + formatBytes(packet.raw_length || 0) + ')</div>';
        }
        if (packet.parsed === false) {
            var diagnostic = packet.parse_error
                ? '<br><small>' + escapeHtml(packet.parse_error) + '</small>'
                : '';
            var preview = packet.raw_preview_hex
                ? '<details><summary>Raw payload preview</summary><pre>' + escapeHtml(packet.raw_preview_hex) +
                    (packet.raw_preview_truncated ? '…' : '') + '</pre></details>'
                : '';
            return '<div class="packet-json-empty packet-json-empty--warn"><span class="material-icons">warning</span> Could not parse proto message (raw_length: ' +
                (packet.raw_length || '?') + ' bytes)' + diagnostic + preview + '</div>';
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

        saveExpandedIds(expandedIds);
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
    function pollPackets() {
        if (disposed || document.hidden || !document.getElementById('packets-list')) return;
        loadPackets();
    }

    async function pollTypes() {
        if (disposed || document.hidden) return;
        var selectionChanged = await loadPacketTypes();
        if (selectionChanged) loadPackets({ reset: true });
    }

    function onVisibilityChange() {
        if (!document.hidden && !disposed) {
            pollTypes();
            pollPackets();
        }
    }

    /* ── Init ── */
    function packetViewerInit() {
        setupFilterToggle();
        loadPacketTypes().then(function () {
            if (!disposed) loadPackets({ reset: true });
        });
        packetPollId = setInterval(pollPackets, 2000);
        typePollId = setInterval(pollTypes, 15000);
        document.addEventListener('visibilitychange', onVisibilityChange);

        // Register cleanup for AJAX router
        window.__pageCleanup = window.__pageCleanup || [];
        window.__pageCleanup.push(function () {
            disposed = true;
            clearInterval(packetPollId);
            clearInterval(typePollId);
            document.removeEventListener('visibilitychange', onVisibilityChange);
            activeControllers.forEach(function (controller) { controller.abort(); });
            activeControllers.clear();
            packetCache.clear();
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
