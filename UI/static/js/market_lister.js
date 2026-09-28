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

    const api = async (path, options = {}, base = API) => {
        const response = await fetch(base + path, {
            headers: { 'Content-Type': 'application/json' }, ...options,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
        return data;
    };
    const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

    // Item Worth (value model) lives under /api/worth, not /api/market-lister.
    const WORTH_API = '/api/worth';
    const worthApi = (path, options = {}) => api(path, options, WORTH_API);

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
        allow_stacks: $('mlAllowStacks').checked,
        price_source: $('mlPriceSource').value,
        source_stash_ids: [...document.querySelectorAll('.mlSource:checked')].map((c) => c.value),
    });

    const fillRules = (rules) => {
        $('mlMinRarity').value = String(rules.min_rarity);
        $('mlMinPrice').value = rules.min_price;
        $('mlUndercut').value = rules.undercut_pct;
        $('mlMaxItems').value = rules.max_items_per_run;
        $('mlAllowStacks').checked = Boolean(rules.allow_stacks);
        $('mlPriceSource').value = rules.price_source || 'live';
        $('mlRecheck').checked = $('mlPriceSource').value === 'live';  // only live pricing searches in game
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

    const insightList = (title, lines) => {
        const block = document.createElement('div');
        block.append(text('h3', title));
        const ul = document.createElement('ul');
        ul.replaceChildren(...(lines.length ? lines : ['Not enough data yet.']).map((line) => text('li', line)));
        block.append(ul);
        return block;
    };

    const analyzeMarket = async () => {
        $('mlAnalyze').disabled = true;
        try {
            const { report } = await post('/analyze');
            const stats = Object.entries(report.stat_premiums || {});
            const pct = (v) => `${v > 0 ? '+' : ''}${v}%`;
            $('mlInsights').replaceChildren(
                text('p', `${report.listings} listings across ${report.items} items analysed.`, 'ml-muted'),
                insightList('Rolls that add the most value (worst → best roll, same item)',
                    stats.slice(0, 8).map(([s, p]) => `${s}: ${pct(p.per_quality)} (n=${p.support})`)),
                insightList('Rolls that add the least', stats.slice(-5).map(([s, p]) => `${s}: ${pct(p.per_quality)}`)),
                insightList('Rolls worth more together (pairs seen on 10+ listings raise prices)', (report.pair_synergies || [])
                    .filter((p) => p.synergy > 0).slice(0, 10)
                    .map((p) => `${p.pair}: ${pct(p.synergy)} (n=${p.support})`)),
                insightList('Good rolls (top 30% of range) vs price', Object.entries(report.good_roll_counts || {})
                    .map(([k, v]) => `${k} good roll(s): ${pct(v.median_uplift)} (n=${v.support})`)),
                insightList('Rarity price steps', Object.entries(report.rarity_steps || {})
                    .map(([step, v]) => `${step}: ×${v.median_ratio} (${v.archetypes} items)`)),
                insightList('Listed below what a merchant pays', (report.below_vendor || [])
                    .map((d) => `${d.name}: ${d.price}g (merchant pays ${d.vendor}g, +${d.gain}g)`)),
            );
            $('mlInsights').hidden = false;
            if (report.worth) renderWorthAccuracy(report.worth);
        } catch (error) {
            notify(error.message, 'error');
        } finally {
            $('mlAnalyze').disabled = false;
        }
    };

    // --- Item value model (Item Worth) card ---
    const worthPct = (v) => `${v}%`;

    const formatWorthSummary = (listings, evaluation) => {
        const n = listings || 0;
        if (!evaluation || evaluation.model_mdape === undefined) {
            return `Trained on ${n} listing(s) so far — not enough held-out data yet to measure accuracy.`;
        }
        return `Trained on ${n} listings — typical error ${worthPct(evaluation.model_mdape)} `
            + `(${worthPct(evaluation.model_within_25)} within ±25%); simple item-median guess: `
            + `${worthPct(evaluation.baseline_mdape)} (${worthPct(evaluation.baseline_within_25)} within ±25%).`;
    };

    // Shape from GET /api/worth/model: {success, trained, listings?, evaluation?}
    const renderWorthInfo = (data) => {
        $('mlWorthStatus').textContent = data && data.trained
            ? formatWorthSummary(data.listings, data.evaluation)
            : 'Not trained yet — press "Train now" once you have market data saved.';
    };

    // Shape from POST /api/worth/train's `accuracy`, and from /analyze's `report.worth`:
    // either the evaluation dict (+ listings), or {trained: 0[, error]} when there was nothing to train on.
    const renderWorthAccuracy = (accuracy) => {
        if (!accuracy || accuracy.model_mdape === undefined) {
            $('mlWorthStatus').textContent = (accuracy && accuracy.error)
                ? `Training failed: ${accuracy.error}`
                : 'Not enough market data saved yet — crawl the market first, then train.';
            return;
        }
        $('mlWorthStatus').textContent = formatWorthSummary(accuracy.listings, accuracy);
    };

    const loadWorthInfo = async () => {
        try {
            renderWorthInfo(await worthApi('/model'));
        } catch (error) {
            $('mlWorthStatus').textContent = 'Item value model status unavailable.';
        }
    };

    const trainWorthModel = async () => {
        $('mlWorthTrain').disabled = true;
        const previousStatus = $('mlWorthStatus').textContent;
        $('mlWorthStatus').textContent = 'Training the item value model (about 15s)…';
        try {
            const data = await worthApi('/train', { method: 'POST', body: JSON.stringify({}) });
            renderWorthAccuracy(data.accuracy);
            notify('Item value model trained.', 'success');
        } catch (error) {
            $('mlWorthStatus').textContent = previousStatus;
            notify(error.message, 'error');
        } finally {
            $('mlWorthTrain').disabled = false;
        }
    };

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
            const label = entry.quantity > 1 ? `${entry.name} ×${entry.quantity}` : entry.name;
            nameCell.append(text('span', entry.flag ? `⚠️ ${label}` : label, entry.flag ? 'ml-flagged' : ''));
            if (entry.quantity > 1) nameCell.append(text('div', 'Whole stack; the price is for all of it.', 'ml-muted ml-small'));
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
        renderMerchant();
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
        $('mlMerchantSell').disabled = running;
        $('mlMerchantDryRun').disabled = running;
        $('mlCancel').hidden = !running;
    };

    const RESULT_LABELS = { not_taken: 'not taken', dry_run: 'dry run' };
    const isMerchantMode = (mode) => String(mode || '').startsWith('merchant');

    const renderResults = (status) => {
        const target = isMerchantMode(status.mode) ? 'mlMerchantResults' : 'mlResults';
        $(target).replaceChildren(...status.results.map((r) => text('li',
            `${r.name}: ${RESULT_LABELS[r.status] || r.status}${r.message ? ` — ${r.message}` : ''}`, `ml-result-${r.status}`)));
    };

    // --- Sell to merchant: skipped items a merchant pays more for (or not worth listing) ---
    let merchantRows = [];

    const checkedMerchantRows = () => [...document.querySelectorAll('.mlMerchantInclude:checked')]
        .map((box) => merchantRows[Number(box.dataset.index)]);

    const updateMerchantTotal = () => {
        const rows = checkedMerchantRows();
        const total = rows.reduce((sum, row) => sum + row.value, 0);
        $('mlMerchantTotal').textContent = `${rows.length} item(s) ticked — the merchant pays ${total}g.`;
    };

    const merchantRow = (row, index) => {
        const tr = document.createElement('tr');
        tr.dataset.uid = row.unique_id;
        const include = document.createElement('input');
        include.type = 'checkbox';
        include.className = 'mlMerchantInclude';
        include.dataset.index = index;
        include.checked = row.value > 0;  // selling for 0g only clears the slot: opt in
        include.addEventListener('change', updateMerchantTotal);
        const name = row.quantity > 1 ? `${row.name} ×${row.quantity}` : row.name;
        const cells = [include, text('span', name), text('span', STASH_NAMES[row.stash_id] || row.stash_id),
            text('span', `${row.value}g`)];
        tr.replaceChildren(...cells.map((c) => { const td = document.createElement('td'); td.append(c); return td; }));
        return tr;
    };

    const renderMerchant = async () => {
        const ids = plan.skipped.filter((s) => s.merchant && s.unique_id).map((s) => s.unique_id);
        merchantRows = [];
        $('mlMerchantCard').hidden = true;
        if (!ids.length) return;
        try {
            const data = await post('/merchant-plan', { character_id: $('mlCharacter').value, unique_ids: ids });
            merchantRows = data.entries;
            $('mlMerchantName').textContent = data.merchant;
            $('mlMerchantWarnings').replaceChildren(...data.warnings.map((w) => text('li', w)));
            $('mlMerchantRows').replaceChildren(...merchantRows.map(merchantRow));
            $('mlMerchantResults').replaceChildren();
            updateMerchantTotal();
            $('mlMerchantCard').hidden = merchantRows.length === 0;
        } catch (error) {
            notify(error.message, 'error');
        }
    };

    const markMerchantSold = (results) => {
        results.filter((r) => r.status === 'sold').forEach((r) => {
            const row = document.querySelector(`#mlMerchantRows tr[data-uid="${CSS.escape(r.unique_id)}"]`);
            if (!row) return;
            const box = row.querySelector('.mlMerchantInclude');
            box.checked = false;
            box.disabled = true;
            row.classList.add('ml-muted');
        });
        updateMerchantTotal();
    };

    const sellToMerchant = async (dryRun) => {
        const ids = checkedMerchantRows().map((row) => row.unique_id);
        if (!ids.length) { notify('Tick at least one item to sell.', 'warning'); return; }
        try {
            await post('/merchant-sell', { character_id: $('mlCharacter').value, unique_ids: ids, dry_run: dryRun });
            notify(dryRun ? 'Merchant dry run started — switching to the game…' : 'Selling to the merchant — switching to the game…');
            watching = true;
            setRunning(true);
            pollTimer = setTimeout(poll, POLL_MS);
        } catch (error) {
            notify(error.message, 'error');
        }
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
                if (status.mode === 'merchant') markMerchantSold(status.results);
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
        loadWorthInfo();
        $('mlBuildPlan').addEventListener('click', buildPlan);
        $('mlCharacter').addEventListener('change', () => renderSources(readRules().source_stash_ids));
        $('mlPriceGame').addEventListener('click', priceFromGame);
        $('mlPriceSource').addEventListener('change', () => {
            $('mlRecheck').checked = $('mlPriceSource').value === 'live';
        });
        $('mlDryRun').addEventListener('click', () => start(true));
        $('mlStart').addEventListener('click', () => start(false));
        $('mlMerchantDryRun').addEventListener('click', () => sellToMerchant(true));
        $('mlMerchantSell').addEventListener('click', () => sellToMerchant(false));
        $('mlCancel').addEventListener('click', () => post('/cancel').catch((e) => notify(e.message, 'error')));
        $('mlHoverTest').addEventListener('click', hoverTest);
        $('mlSaveCalibration').addEventListener('click', saveCalibration);
        $('mlCollect').addEventListener('click', () => runJob('/collect', {}, 'Collecting sold gold — switching to the game…'));
        $('mlCrawlUpdate').addEventListener('click', () => runJob('/crawl', { pages: 20 }, 'Updating market data…'));
        $('mlCrawlDeep').addEventListener('click', () => runJob('/crawl', { pages: 60, incremental: false },
            'Deep crawl started — this reads a few hundred pages…'));
        $('mlAnalyze').addEventListener('click', analyzeMarket);
        $('mlWorthTrain').addEventListener('click', trainWorthModel);
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
