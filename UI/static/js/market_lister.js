/* global showNotification */
(() => {
    const API = '/api/market-lister';
    const POLL_MS = 500;
    const MAX_SNAPSHOT_AGE_S = 120; // keep in sync with marketplace_state.MAX_SNAPSHOT_AGE_S
    const LISTINGS_MISSING_TEXT = 'Open Trade → Marketplace → My Listings in the game so DnDTools can see your listing spots.';
    const LISTINGS_STALE_TEXT = 'My Listings info is out of date — open (or re-open) Trade → Marketplace → My Listings in the game.';
    const STASH_NAMES = { 2: 'Inventory', 4: 'Storage', 5: 'Purchased 1', 6: 'Purchased 2', 7: 'Purchased 3',
        8: 'Purchased 4', 9: 'Purchased 5', 20: 'Shared Seasonal', 30: 'Shared Stash' };
    const POINT_KEYS = ['spot_row_origin', 'next_page_arrow', 'tab_icon_origin', 'inv_grid_origin',
        'stash_grid_origin', 'price_field', 'create_listing_button'];
    const LENGTH_KEYS = ['spot_row_spacing', 'tab_icon_spacing', 'cell'];
    const LISTING_FEE_RATE = 0.05; // keep in sync with market_rules.LISTING_FEE_RATE
    const LISTING_FEE_MIN = 15;
    const listingFee = (price) => Math.max(LISTING_FEE_MIN, Math.ceil(price * LISTING_FEE_RATE));
    const $ = (id) => document.getElementById(id);
    let plan = null;
    let characterStashes = {};
    let needsGamePricing = false; // no DarkerDB key: prices come from the in-game market
    let pollTimer = null;
    let disposed = false;
    let watching = false; // only announce completion for runs started from this page view

    const notify = (msg, type = 'info') => {
        if (typeof showNotification === 'function') showNotification(msg, type, { duration: 5000 });
    };

    const api = async (path, options = {}) => {
        const response = await fetch(API + path, {
            headers: { 'Content-Type': 'application/json' }, ...options,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
        return data;
    };
    const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

    const text = (tag, value, className) => {
        const el = document.createElement(tag);
        el.textContent = value;
        if (className) el.className = className;
        return el;
    };

    const readRules = () => ({
        min_rarity: Number($('mlMinRarity').value),
        min_price: Number($('mlMinPrice').value),
        undercut_pct: Number($('mlUndercut').value),
        max_items_per_run: Number($('mlMaxItems').value),
        source_stash_ids: [...document.querySelectorAll('.mlSource:checked')].map((c) => c.value),
    });

    const fillRules = (rules) => {
        $('mlMinRarity').value = String(rules.min_rarity);
        $('mlMinPrice').value = rules.min_price;
        $('mlUndercut').value = rules.undercut_pct;
        $('mlMaxItems').value = rules.max_items_per_run;
        renderSources(rules.source_stash_ids);
    };

    const loadCharacters = async () => {
        const response = await fetch('/api/characters');
        const characters = await response.json();
        const select = $('mlCharacter');
        select.replaceChildren(...characters.map((c) => {
            const option = text('option', `${c.nickname} (${c.class} ${c.level})`);
            option.value = c.id;
            return option;
        }));
        characterStashes = Object.fromEntries(characters.map((c) => [c.id, Object.keys(c.stashes || {})]));
    };

    // One checkbox per tab this character actually has (inventory + stash tabs; not equipment).
    const renderSources = (selected) => {
        const ids = (characterStashes[$('mlCharacter').value] || [])
            .filter((id) => id === '2' || (Number(id) >= 4 && Number(id) < 100))
            .sort((a, b) => Number(a) - Number(b));
        $('mlSources').replaceChildren(...ids.map((id) => {
            const label = document.createElement('label');
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.value = id;
            box.className = 'mlSource';
            box.checked = selected.includes(id);
            label.append(box, ` ${STASH_NAMES[id] || `Stash tab ${id}`}`);
            return label;
        }));
    };

    const renderListingStatus = (listings) => {
        const el = $('mlListingStatus');
        if (!listings) return;
        const fresh = listings.seen && listings.age_s !== null && listings.age_s <= MAX_SNAPSHOT_AGE_S;
        if (fresh) {
            el.textContent = `My Listings detected ${listings.age_s} s ago — ${listings.free} free listing spots.`;
        } else {
            el.textContent = listings.seen ? LISTINGS_STALE_TEXT : LISTINGS_MISSING_TEXT;
        }
        el.classList.toggle('ok', fresh);
        const payouts = fresh ? (listings.payouts || 0) : 0;
        $('mlPayouts').hidden = payouts === 0;
        $('mlPayoutText').textContent = `${payouts} sold/expired listing(s) waiting in My Listings — collect them before the game destroys them (7 days).`;
    };

    const renderHistory = async () => {
        try {
            const h = await api('/history');
            $('mlHistory').textContent = `${h.listings || 0} listings saved across ${h.items || 0} items · `
                + `${h.vanished || 0} likely sold · your sales seen: ${h.my_sold || 0}`;
        } catch (error) {
            $('mlHistory').textContent = 'Market data unavailable.';
        }
    };

    const CONFIDENCE_LABELS = { high: 'High', medium: 'Medium', low: 'Low' };
    const RARITY_LABELS = { 0: '—', 1: 'Poor', 2: 'Common', 3: 'Uncommon', 4: 'Rare', 5: 'Epic', 6: 'Legendary',
        7: 'Unique', 8: 'Artifact' };
    let priceQueryTimer = null;

    const gold = (value) => `${Math.round(value).toLocaleString()}g`;

    const searchPrices = async () => {
        const query = $('mlPriceQuery').value.trim();
        if (query.length < 2) { $('mlPriceTable').hidden = true; return; }
        try {
            const data = await api(`/prices?q=${encodeURIComponent(query)}`);
            $('mlPriceRows').replaceChildren(...data.items.map((row) => {
                const tr = document.createElement('tr');
                const cells = [row.name, RARITY_LABELS[row.rarity] || row.rarity, String(row.listings),
                    gold(row.min_unit), gold(row.median_unit), gold(row.max_unit),
                    row.vendor_price ? gold(row.vendor_price) : '—'];
                tr.replaceChildren(...cells.map((value) => { const td = document.createElement('td'); td.textContent = value; return td; }));
                return tr;
            }));
            $('mlPriceTable').hidden = data.items.length === 0;
            if (!data.items.length) notify('No saved listings match that name yet.', 'info');
        } catch (error) {
            notify(error.message, 'error');
        }
    };

    const renderPlan = () => {
        $('mlPlanCard').hidden = false;
        $('mlWarnings').replaceChildren(...plan.warnings.map((w) => text('li', w)));
        $('mlPlanRows').replaceChildren(...plan.entries.map((entry, index) => {
            const row = document.createElement('tr');
            const include = document.createElement('input');
            include.type = 'checkbox';
            include.checked = true;
            include.dataset.index = index;
            include.className = 'mlInclude';
            const price = document.createElement('input');
            price.type = 'number';
            price.min = '1';
            price.value = needsGamePricing ? '' : entry.price;
            price.placeholder = needsGamePricing ? 'from game' : '';
            price.disabled = needsGamePricing;
            price.dataset.index = index;
            price.className = 'mlPrice';
            const fee = text('span', needsGamePricing ? '—' : `${entry.fee}g`);
            price.addEventListener('input', () => {
                const value = Number(price.value);
                fee.textContent = Number.isFinite(value) && value > 0 ? `${listingFee(value)}g` : '—';
            });
            const nameCell = document.createElement('div');
            nameCell.append(text('span', entry.flag ? `⚠️ ${entry.name}` : entry.name, entry.flag ? 'ml-flagged' : ''));
            const rolls = [...(entry.base_rolls || []), ...(entry.rolls || [])].map(([stat, value]) => `${stat} ${value}`).join(', ');
            if (rolls) nameCell.append(text('div', rolls, 'ml-muted ml-small'));
            if (entry.compared) nameCell.append(text('div', entry.compared, 'ml-muted ml-small'));
            if (entry.flag) nameCell.append(text('div', entry.flag, 'ml-flag-text ml-small'));
            const confidence = text('span', CONFIDENCE_LABELS[entry.confidence] || '—',
                entry.confidence ? `ml-conf ml-conf-${entry.confidence}` : '');
            const cells = [include, nameCell, text('span', STASH_NAMES[entry.stash_id] || entry.stash_id),
                confidence, price, fee];
            row.replaceChildren(...cells.map((c) => { const td = document.createElement('td'); td.append(c); return td; }));
            return row;
        }));
        $('mlSkippedSummary').textContent = `Skipped (${plan.skipped.length})`;
        $('mlSkipped').replaceChildren(...plan.skipped.map((s) => text('li', `${s.name} — ${s.reason}`)));
        $('mlResults').replaceChildren();
        $('mlPriceGame').hidden = !needsGamePricing || plan.entries.length === 0;
        $('mlDryRun').hidden = needsGamePricing;
        $('mlStart').hidden = needsGamePricing;
        const empty = plan.entries.length === 0;
        $('mlSkippedSummary').parentElement.open = empty;
        const planned = `Plan ready: ${plan.entries.length} item(s) to list, ${plan.skipped.length} skipped.`;
        notify(empty ? (plan.warnings[plan.warnings.length - 1] || planned) : planned, empty ? 'warning' : 'success');
        $('mlPlanCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
    };

    const selectedEntries = () => [...document.querySelectorAll('.mlInclude:checked')].map((box) => {
        const index = Number(box.dataset.index);
        const price = document.querySelector(`.mlPrice[data-index="${index}"]`);
        return { ...plan.entries[index], price: Number(price.value) };
    });

    const setRunning = (running) => {
        $('mlStart').disabled = running;
        $('mlDryRun').disabled = running;
        $('mlBuildPlan').disabled = running;
        $('mlHoverTest').disabled = running;
        $('mlPriceGame').disabled = running;
        $('mlCollect').disabled = running;
        $('mlCrawlUpdate').disabled = running;
        $('mlCrawlDeep').disabled = running;
        $('mlCancel').hidden = !running;
    };

    const renderResults = (status) => {
        $('mlResults').replaceChildren(...status.results.map((r) => text('li', `${r.name}: ${r.status}${r.message ? ` — ${r.message}` : ''}`, `ml-result-${r.status}`)));
    };

    const poll = async () => {
        if (disposed) return;
        try {
            const status = await api('/status');
            renderResults(status);
            renderListingStatus(status.listings);
            const running = status.state === 'running';
            setRunning(running);
            if (running) {
                pollTimer = setTimeout(poll, POLL_MS);
            } else if (status.state === 'done' && watching) {
                watching = false;
                if (status.mode === 'price' && status.plan) {
                    plan = status.plan;
                    needsGamePricing = false;
                    renderPlan();
                }
                renderHistory();
                notify(status.stopped_reason || 'Market lister finished.', status.stopped_reason ? 'warning' : 'success');
            }
        } catch (error) {
            notify(error.message, 'error');
            setRunning(false);
        }
    };

    const buildPlan = async () => {
        $('mlBuildPlan').disabled = true;
        try {
            const rules = readRules();
            await post('/rules', rules);
            const data = await post('/plan', { character_id: $('mlCharacter').value, rules });
            plan = data.plan;
            needsGamePricing = Boolean(data.needs_game_pricing);
            renderListingStatus(data.listings);
            renderPlan();
        } catch (error) {
            notify(error.message, 'error');
        } finally {
            $('mlBuildPlan').disabled = false;
        }
    };

    const priceFromGame = async () => {
        const entries = selectedEntries();
        if (!entries.length) { notify('Tick at least one item to price.', 'warning'); return; }
        try {
            await post('/price', { entries });
            notify('Pricing from the in-game market — switching to the game…');
            watching = true;
            setRunning(true);
            pollTimer = setTimeout(poll, POLL_MS);
        } catch (error) {
            notify(error.message, 'error');
        }
    };

    const start = async (dryRun) => {
        const entries = selectedEntries();
        if (!entries.length) { notify('Tick at least one item.', 'warning'); return; }
        try {
            await post('/start', { entries, dry_run: dryRun, recheck: $('mlRecheck').checked });
            notify(dryRun ? 'Dry run started — switching to the game…' : 'Listing started — switching to the game…');
            watching = true;
            setRunning(true);
            pollTimer = setTimeout(poll, POLL_MS);
        } catch (error) {
            notify(error.message, 'error');
        }
    };

    const renderCalibration = (data) => {
        $('mlResolution').textContent = data.resolution;
        const cal = data.calibration || { points: {}, lengths: {} };
        const rows = POINT_KEYS.map((key) => {
            const [dx, dy] = cal.points[key] || [0, 0];
            const row = document.createElement('div');
            row.className = 'ml-cal-row';
            row.innerHTML = `<span></span><label>X <input type="number" data-point="${key}" data-axis="0"></label><label>Y <input type="number" data-point="${key}" data-axis="1"></label>`;
            row.firstChild.textContent = key.replaceAll('_', ' ');
            row.querySelector('[data-axis="0"]').value = dx;
            row.querySelector('[data-axis="1"]').value = dy;
            return row;
        }).concat(LENGTH_KEYS.map((key) => {
            const row = document.createElement('div');
            row.className = 'ml-cal-row';
            row.innerHTML = `<span></span><label>± <input type="number" step="0.1" data-length="${key}"></label>`;
            row.firstChild.textContent = key.replaceAll('_', ' ');
            row.querySelector('input').value = cal.lengths[key] || 0;
            return row;
        }));
        $('mlCalibration').replaceChildren(...rows);
    };

    const saveCalibration = async () => {
        const points = {};
        document.querySelectorAll('[data-point]').forEach((input) => {
            const key = input.dataset.point;
            points[key] = points[key] || [0, 0];
            points[key][Number(input.dataset.axis)] = Number(input.value) || 0;
        });
        const lengths = {};
        document.querySelectorAll('[data-length]').forEach((input) => { lengths[input.dataset.length] = Number(input.value) || 0; });
        try {
            await post('/calibration', { points, lengths });
            notify('Calibration saved.', 'success');
        } catch (error) {
            notify(error.message, 'error');
        }
    };

    const hoverTest = async () => {
        try {
            await post('/hover-test');
            watching = true;
            setRunning(true);
            pollTimer = setTimeout(poll, POLL_MS);
        } catch (error) {
            notify(error.message, 'error');
        }
    };

    const runJob = async (path, body, message) => {
        try {
            await post(path, body);
            notify(message);
            watching = true;
            setRunning(true);
            pollTimer = setTimeout(poll, POLL_MS);
        } catch (error) {
            notify(error.message, 'error');
        }
    };

    const init = async () => {
        try {
            await loadCharacters();
            fillRules(await api('/rules'));
            renderCalibration(await api('/calibration'));
            renderHistory();
            await poll();
        } catch (error) {
            notify(error.message, 'error');
        }
        $('mlBuildPlan').addEventListener('click', buildPlan);
        $('mlCharacter').addEventListener('change', () => renderSources(readRules().source_stash_ids));
        $('mlPriceGame').addEventListener('click', priceFromGame);
        $('mlDryRun').addEventListener('click', () => start(true));
        $('mlStart').addEventListener('click', () => start(false));
        $('mlCancel').addEventListener('click', () => post('/cancel').catch((e) => notify(e.message, 'error')));
        $('mlHoverTest').addEventListener('click', hoverTest);
        $('mlSaveCalibration').addEventListener('click', saveCalibration);
        $('mlCollect').addEventListener('click', () => runJob('/collect', {}, 'Collecting sold gold — switching to the game…'));
        $('mlCrawlUpdate').addEventListener('click', () => runJob('/crawl', { pages: 20 }, 'Updating market data…'));
        $('mlCrawlDeep').addEventListener('click', () => runJob('/crawl', { pages: 60, incremental: false },
            'Deep crawl started — this reads a few hundred pages…'));
        $('mlPriceQuery').addEventListener('input', () => {
            clearTimeout(priceQueryTimer);
            priceQueryTimer = setTimeout(searchPrices, 300);
        });
        document.querySelectorAll('.ml-chip[data-undercut]').forEach((chip) => chip.addEventListener('click', () => {
            $('mlUndercut').value = chip.dataset.undercut;
        }));
    };

    window.__pageCleanup = window.__pageCleanup || [];
    window.__pageCleanup.push(() => {
        disposed = true;
        clearTimeout(pollTimer);
        clearTimeout(priceQueryTimer);
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
