// Format datetime stamps consistently across the app
function formatDate(isoString) {
    return new Date(isoString).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// Handle API errors consistently
function handleApiError(error, element) {
    console.error('API Error:', error);
    if (element) {
        element.innerHTML = `
            <div class="error-message">
                An error occurred. Please try again later.
            </div>
        `;
    }
}

const GLOBAL_SORT_CANCEL_NOTIFICATION_ID = 'character-sort-cancelled';
const GLOBAL_SORT_CANCEL_MESSAGE = 'Sort canceled. Refresh your character data. If switching tabs doesn\'t update, move any item in the stash and switch tabs again.';
const GLOBAL_SORT_SUCCESS_NOTIFICATION_ID = 'character-sort-success';
const GLOBAL_SORT_SUCCESS_MESSAGE = 'Sort completed! Refresh your character data to see the updated layout. If switching tabs doesn\'t update, move any item in the stash and switch tabs again.';

// ===== Unified Notification System =====
// Single global notification manager. Supports stacking, ids, persistent, close button, timer bar.
(function () {
    const registry = new Map();
    const MAX_VISIBLE = 5;

    function getContainer() {
        let c = document.getElementById('notifications-container');
        if (!c) {
            c = document.createElement('div');
            c.id = 'notifications-container';
            document.body.appendChild(c);
        }
        return c;
    }

    function removeElement(id, el) {
        if (!el) return;
        if (el.classList.contains('notification-exit')) return;
        el.classList.add('notification-exit');
        const onDone = () => {
            el.removeEventListener('animationend', onDone);
            if (el.parentNode) el.parentNode.removeChild(el);
            if (id && registry.has(id)) {
                const stored = registry.get(id);
                if (stored && stored.element === el) registry.delete(id);
            }
            pruneOverflow();
        };
        el.addEventListener('animationend', onDone);
        // Safety net if animation doesn't fire
        setTimeout(onDone, 350);
    }

    function pruneOverflow() {
        const container = getContainer();
        const children = Array.from(container.querySelectorAll('.notification:not(.notification-exit)'));
        while (children.length > MAX_VISIBLE) {
            const oldest = children.shift();
            const oldId = oldest.dataset.notificationId || null;
            const entry = oldId && registry.get(oldId);
            if (entry && entry.timeout) clearTimeout(entry.timeout);
            removeElement(oldId, oldest);
        }
    }

    function scheduleAutoDismiss(id, el, duration) {
        const ms = Number.isFinite(duration) && duration >= 0 ? duration : 3500;
        // Add a shrinking timer bar
        const bar = el.querySelector('.notification-timer');
        if (bar) {
            bar.style.animation = `notifTimerShrink ${ms}ms linear forwards`;
        }
        return setTimeout(() => removeElement(id, el), ms);
    }

    function buildNotificationEl(message, type, id, persistent) {
        const el = document.createElement('div');
        el.className = `notification ${type}`;
        el.setAttribute('role', 'alert');
        el.setAttribute('aria-live', 'assertive');
        el.setAttribute('aria-atomic', 'true');
        if (id) el.dataset.notificationId = id;

        // Text node
        const textSpan = document.createElement('span');
        textSpan.className = 'notification-text';
        textSpan.textContent = message;
        el.appendChild(textSpan);

        // Close button
        const closeBtn = document.createElement('button');
        closeBtn.className = 'notification-close';
        closeBtn.setAttribute('aria-label', 'Dismiss');
        closeBtn.innerHTML = '<span class="material-icons">close</span>';
        closeBtn.addEventListener('click', () => {
            const entry = id && registry.get(id);
            if (entry && entry.timeout) clearTimeout(entry.timeout);
            removeElement(id, el);
        });
        el.appendChild(closeBtn);

        // Timer bar (hidden for persistent)
        if (!persistent) {
            const timer = document.createElement('div');
            timer.className = 'notification-timer';
            el.appendChild(timer);
        }

        return el;
    }

    function showNotification(message, type, options) {
        // Handle overloaded signatures:
        //   showNotification(msg, type)
        //   showNotification(msg, {type, ...})
        //   showNotification(msg, type, {id, persistent, duration})
        if (typeof type === 'object' && type !== null) {
            options = type;
            type = options.type || 'info';
        }
        type = type || 'info';
        const opts = options && typeof options === 'object' ? options : {};
        const id = opts.id || null;
        const persistent = Boolean(opts.persistent);
        const duration = opts.duration != null ? opts.duration : 3500;

        // If an id already exists, update in place
        if (id && registry.has(id)) {
            const existing = registry.get(id);
            if (existing && existing.element) {
                if (existing.timeout) { clearTimeout(existing.timeout); existing.timeout = null; }
                const el = existing.element;
                const textEl = el.querySelector('.notification-text');
                if (textEl) textEl.textContent = message;
                el.className = `notification ${type}`;
                el.classList.remove('notification-exit');
                // Restart entrance animation
                el.style.animation = 'none';
                void el.offsetWidth;
                el.style.animation = '';
                // Rebuild timer bar if needed
                const oldBar = el.querySelector('.notification-timer');
                if (oldBar) oldBar.remove();
                if (!persistent) {
                    const timer = document.createElement('div');
                    timer.className = 'notification-timer';
                    el.appendChild(timer);
                    existing.timeout = scheduleAutoDismiss(id, el, duration);
                }
                existing.persistent = persistent;
                return { id, dismiss: () => dismissNotification(id) };
            }
        }

        const container = getContainer();
        const el = buildNotificationEl(message, type, id, persistent);
        container.appendChild(el);

        let timeout = null;
        if (!persistent) {
            timeout = scheduleAutoDismiss(id, el, duration);
        }

        if (id) {
            registry.set(id, { element: el, timeout, persistent });
        }

        pruneOverflow();
        return { id, dismiss: () => dismissNotification(id) };
    }

    function dismissNotification(id) {
        if (!id || !registry.has(id)) return false;
        const entry = registry.get(id);
        if (entry.timeout) clearTimeout(entry.timeout);
        removeElement(id, entry.element);
        return true;
    }

    // Expose globally
    window.showNotification = showNotification;
    window.dismissNotification = dismissNotification;
})();

(function () {
    const BANNER_ID = 'asset-update-banner';
    let autoDismissTimer = null;

    function ensureBanner() {
        let banner = document.getElementById(BANNER_ID);
        if (banner) {
            return banner;
        }

        banner = document.createElement('div');
        banner.id = BANNER_ID;
        banner.style.position = 'fixed';
        banner.style.bottom = '32px';
        banner.style.right = '32px';
        banner.style.left = 'auto';
        banner.style.width = '320px';
        banner.style.padding = '16px 20px';
        banner.style.background = 'var(--bg-secondary, #21170f)';
        banner.style.color = 'var(--text-primary, #f2e2c2)';
        banner.style.border = '1px solid rgba(226, 188, 123, 0.35)';
        banner.style.borderRadius = '10px';
        banner.style.boxShadow = '0 10px 24px rgba(0, 0, 0, 0.35)';
        banner.style.zIndex = '12000';
        banner.style.backdropFilter = 'blur(6px)';
        banner.innerHTML = `
            <div style="font-weight: 600; letter-spacing: 0.5px; margin-bottom: 6px; font-size: 13px; color: var(--accent-gold, #e7c470);">
                Asset Updates
            </div>
            <div class="asset-update-message" style="font-size: 14px; line-height: 1.4; margin-bottom: 10px;">
                Checking for updates...
            </div>
            <div class="asset-update-progress" style="height: 6px; border-radius: 999px; background: rgba(255, 255, 255, 0.1); overflow: hidden; display: none;">
                <div class="asset-update-progress-fill" style="height: 100%; width: 0%; background: linear-gradient(90deg, #e7c470, #f4dba1);"></div>
            </div>
            <div class="asset-update-actions" style="display: none; justify-content: flex-end; gap: 8px; margin-top: 14px;">
                <button data-action="restart" style="display: none; padding: 8px 14px; border-radius: 6px; border: none; background: var(--accent-gold, #e7c470); color: #1a1411; font-weight: 600; cursor: pointer;">
                    Restart now
                </button>
                <button data-action="dismiss" style="display: none; padding: 8px 14px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.2); background: transparent; color: var(--text-secondary, #c8b491); font-weight: 600; cursor: pointer;">
                    Later
                </button>
            </div>
        `;

        const actions = banner.querySelector('.asset-update-actions');
        actions.addEventListener('click', async (event) => {
            const target = event.target;
            if (!(target instanceof HTMLButtonElement)) {
                return;
            }
            const action = target.dataset.action;

            if (action === 'restart') {
                target.disabled = true;
                target.textContent = 'Restarting...';
                try {
                    await fetch('/api/restart', { method: 'POST' });
                } catch (error) {
                    console.warn('Restart request failed', error);
                    showNotification('Restart failed. Please restart manually.', 'error');
                    target.disabled = false;
                    target.textContent = 'Restart now';
                }
            } else if (action === 'dismiss') {
                hideBanner();
            }
        });

        document.body.appendChild(banner);
        return banner;
    }

    function hideBanner() {
        const banner = document.getElementById(BANNER_ID);
        if (banner && banner.parentNode) {
            banner.parentNode.removeChild(banner);
        }
        if (autoDismissTimer) {
            clearTimeout(autoDismissTimer);
            autoDismissTimer = null;
        }
    }

    window.handleAssetUpdateStatus = function (payload = {}) {
        if (!payload || payload.dismiss) {
            hideBanner();
            return;
        }

        const banner = ensureBanner();
        const messageEl = banner.querySelector('.asset-update-message');
        if (messageEl && typeof payload.message === 'string') {
            messageEl.textContent = payload.message;
        }

        banner.dataset.status = payload.status || '';

        const progressWrap = banner.querySelector('.asset-update-progress');
        const progressFill = banner.querySelector('.asset-update-progress-fill');
        if (progressWrap && progressFill && typeof payload.progress === 'number' && Number.isFinite(payload.progress)) {
            const pct = Math.max(0, Math.min(1, payload.progress));
            progressWrap.style.display = 'block';
            progressFill.style.width = `${Math.round(pct * 100)}%`;
        } else if (progressWrap) {
            progressWrap.style.display = 'none';
        }

        const actions = banner.querySelector('.asset-update-actions');
        const restartBtn = banner.querySelector('button[data-action="restart"]');
        const dismissBtn = banner.querySelector('button[data-action="dismiss"]');
        const needsRestart = Boolean(payload.promptRestart);
        const allowDismiss = Boolean(payload.allowDismiss || payload.promptRestart);

        if (actions && restartBtn && dismissBtn) {
            actions.style.display = needsRestart || allowDismiss ? 'flex' : 'none';
            restartBtn.style.display = needsRestart ? 'inline-flex' : 'none';
            restartBtn.disabled = false;
            restartBtn.textContent = 'Restart now';
            dismissBtn.style.display = allowDismiss ? 'inline-flex' : 'none';
        }

        if (autoDismissTimer) {
            clearTimeout(autoDismissTimer);
            autoDismissTimer = null;
        }
        if (payload.autoDismiss) {
            autoDismissTimer = setTimeout(hideBanner, 4500);
        }
    };
})();

window.addEventListener('sortCancelled', (event) => {
    try {
        const detail = event && typeof event.detail === 'object' ? event.detail : null;
        const message = detail && typeof detail.message === 'string' && detail.message.trim()
            ? detail.message
            : GLOBAL_SORT_CANCEL_MESSAGE;

        showNotification(message, 'warning', {
            id: GLOBAL_SORT_CANCEL_NOTIFICATION_ID,
            persistent: true
        });
    } catch (err) {
        console.error('Failed to display sort cancellation notification', err);
    }
});

window.addEventListener('sortCompleted', (event) => {
    try {
        const detail = event && typeof event.detail === 'object' ? event.detail : null;
        const message = detail && typeof detail.message === 'string' && detail.message.trim()
            ? detail.message
            : GLOBAL_SORT_SUCCESS_MESSAGE;

        showNotification(message, 'info', {
            id: GLOBAL_SORT_SUCCESS_NOTIFICATION_ID,
            persistent: true
        });
    } catch (err) {
        console.error('Failed to display sort completion notification', err);
    }
});

// Format number values consistently across the app
function formatNumber(num) {
    return new Intl.NumberFormat().format(num);
}

const UPDATE_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000; // 24 hours
const UPDATE_LAST_CHECK_KEY = 'dndtools:update:lastCheck';
const UPDATE_RESULT_KEY = 'dndtools:update:lastResult';
const UPDATE_DISMISSED_VERSION_KEY = 'dndtools:update:dismissedVersion';
const UPDATE_SESSION_CHECKED_KEY = 'dndtools:update:sessionChecked';

function safeStorageGet(storage, key) {
    try {
        return storage.getItem(key);
    } catch (err) {
        console.warn('Storage get failed', err);
        return null;
    }
}

function safeStorageSet(storage, key, value) {
    try {
        storage.setItem(key, value);
    } catch (err) {
        console.warn('Storage set failed', err);
    }
}

function safeStorageRemove(storage, key) {
    try {
        storage.removeItem(key);
    } catch (err) {
        console.warn('Storage remove failed', err);
    }
}

function normalizeVersionTag(value = '') {
    return (value || '').toString().replace(/^v/i, '').trim();
}

// Loading state helper
function setLoading(element, isLoading) {
    if (isLoading) {
        element.classList.add('loading');
        element.dataset.originalText = element.textContent;
        element.textContent = 'Loading...';
        element.disabled = true;
    } else {
        element.classList.remove('loading');
        element.textContent = element.dataset.originalText;
        element.disabled = false;
        delete element.dataset.originalText;
    }
}

// Listen for background initialization completion
window.addEventListener('backgroundInitDone', () => {
    showNotification('Data loaded!', 'success');
    const spinner = document.getElementById('loading-spinner');
    if (spinner) spinner.style.display = 'none';
});

// Version check and update notification
function maybeShowCachedUpdate() {
    const cached = safeStorageGet(window.localStorage, UPDATE_RESULT_KEY);
    if (!cached) {
        return;
    }

    try {
        const data = JSON.parse(cached);
        maybeShowUpdatePopup(data, { fromCache: true });
    } catch (error) {
        console.warn('Failed to parse cached update info', error);
        safeStorageRemove(window.localStorage, UPDATE_RESULT_KEY);
    }
}

function maybeShowUpdatePopup(data, { fromCache = false } = {}) {
    if (!data || !data.updateAvailable) {
        if (!fromCache) {
            safeStorageRemove(window.localStorage, UPDATE_RESULT_KEY);
            safeStorageRemove(window.localStorage, UPDATE_DISMISSED_VERSION_KEY);
        }
        return;
    }

    const remoteVersionRaw = data.latestVersion || '';
    const localVersionRaw = data.currentVersion || '';
    const remoteVersionNormalized = normalizeVersionTag(remoteVersionRaw);
    if (!remoteVersionNormalized) {
        return;
    }

    const dismissedVersion = safeStorageGet(window.localStorage, UPDATE_DISMISSED_VERSION_KEY);
    if (dismissedVersion && dismissedVersion === remoteVersionNormalized) {
        return;
    }

    if (document.getElementById('update-popup')) {
        return;
    }

    const releaseUrl = data.downloadUrl || 'https://github.com/Beelzebub2/DnDTools/releases/latest';
    const notes = data.notes || '';

    showUpdatePopup(
        normalizeVersionTag(remoteVersionRaw) || remoteVersionRaw,
        normalizeVersionTag(localVersionRaw) || localVersionRaw,
        releaseUrl,
        notes,
        remoteVersionNormalized,
        {
            channel: data.effectiveChannel || data.channel || 'stable',
            releaseTag: data.releaseTag || ''
        }
    );

    // Mark this version as shown immediately so the popup does not
    // reappear on every tab switch (full page reloads destroy the DOM).
    safeStorageSet(window.localStorage, UPDATE_DISMISSED_VERSION_KEY, remoteVersionNormalized);
}

async function checkForUpdates(force = false) {
    // When not forced (i.e. automatic check), respect the autoUpdateEnabled setting
    if (!force) {
        try {
            const settingsResp = await fetch('/api/settings');
            if (settingsResp.ok) {
                const settings = await settingsResp.json();
                if (settings.autoUpdateEnabled === false) {
                    return;
                }
            }
        } catch (_) {
            // If we can't read settings, proceed with the check anyway
        }
    }

    const now = Date.now();
    const sessionChecked = safeStorageGet(window.sessionStorage, UPDATE_SESSION_CHECKED_KEY) === '1';
    const lastCheck = Number(safeStorageGet(window.localStorage, UPDATE_LAST_CHECK_KEY) || '0');

    if (!force && sessionChecked) {
        maybeShowCachedUpdate();
        return;
    }

    if (!force && lastCheck && now - lastCheck < UPDATE_CHECK_INTERVAL_MS) {
        safeStorageSet(window.sessionStorage, UPDATE_SESSION_CHECKED_KEY, '1');
        maybeShowCachedUpdate();
        return;
    }

    let responseOk = false;
    let data = null;

    try {
        const response = await fetch('/api/update/check', { cache: 'no-store' });

        try {
            data = await response.json();
        } catch (parseError) {
            console.warn('Update check response was not valid JSON', parseError);
        }

        responseOk = !!data && response.ok;

        if (!responseOk && data && data.error) {
            console.warn('Update check failed:', data.error);
        }

        if (responseOk) {
            safeStorageSet(window.localStorage, UPDATE_RESULT_KEY, JSON.stringify(data));
            maybeShowUpdatePopup(data);
        }
    } catch (error) {
        console.warn('Automatic update check failed', error);
    } finally {
        safeStorageSet(window.sessionStorage, UPDATE_SESSION_CHECKED_KEY, '1');
        safeStorageSet(window.localStorage, UPDATE_LAST_CHECK_KEY, String(now));

        if (!responseOk) {
            maybeShowCachedUpdate();
        }
    }

    return data;
}

// Version comparison now handled by utils.js
// Remove duplicate function that's now in utils.js

function showUpdatePopup(remoteVersion, localVersion, releaseUrl, notes = '', trackingVersion = null, options = {}) {
    // Remove any existing popup
    const existing = document.getElementById('update-popup');
    if (existing) existing.remove();

    // Inject styles once
    if (!document.getElementById('update-popup-styles')) {
        const style = document.createElement('style');
        style.id = 'update-popup-styles';
        style.textContent = `
            @keyframes updateSlideIn {
                from { transform: translateY(16px); opacity: 0; }
                to   { transform: translateY(0);    opacity: 1; }
            }
            @keyframes updateSlideOut {
                from { transform: translateY(0);    opacity: 1; }
                to   { transform: translateY(16px); opacity: 0; }
            }
            #update-popup {
                position: fixed;
                bottom: 24px;
                right: 24px;
                z-index: 99999;
                width: 340px;
                background: linear-gradient(135deg, rgba(20,20,20,0.98), rgba(11,11,11,0.98));
                border: 1px solid var(--border-color, #2a2a2a);
                border-radius: 12px;
                box-shadow: 0 12px 40px rgba(0,0,0,0.55), 0 0 0 1px rgba(207,163,70,0.08);
                backdrop-filter: blur(16px);
                animation: updateSlideIn 0.35s cubic-bezier(0.22,1,0.36,1) forwards;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                overflow: hidden;
            }
            #update-popup.closing {
                animation: updateSlideOut 0.25s cubic-bezier(0.4,0,1,1) forwards;
            }
            .upd-header {
                display: flex;
                align-items: center;
                gap: 10px;
                padding: 14px 16px 12px;
                border-bottom: 1px solid var(--border-color, #2a2a2a);
            }
            .upd-icon {
                display: flex;
                align-items: center;
                justify-content: center;
                width: 32px; height: 32px;
                border-radius: 8px;
                background: rgba(207,163,70,0.12);
                color: var(--accent-gold, #cfa346);
                flex-shrink: 0;
            }
            .upd-icon .material-icons { font-size: 18px; }
            .upd-title {
                flex: 1;
                font-size: 13.5px;
                font-weight: 600;
                color: var(--text-primary, #e6e6e6);
                letter-spacing: 0.01em;
            }
            .upd-close {
                background: none; border: none; cursor: pointer;
                color: var(--text-secondary, #888);
                padding: 4px; border-radius: 6px;
                display: flex; align-items: center; justify-content: center;
                transition: color .15s, background .15s;
            }
            .upd-close:hover {
                color: var(--text-primary, #e6e6e6);
                background: rgba(255,255,255,0.06);
            }
            .upd-close .material-icons { font-size: 18px; }
            .upd-body { padding: 14px 16px; }
            .upd-versions {
                display: flex;
                align-items: center;
                gap: 8px;
                font-size: 13px;
                color: var(--text-secondary, #888);
                margin-bottom: 12px;
            }
            .upd-ver {
                font-family: 'Consolas', 'SF Mono', monospace;
                font-size: 12.5px;
                padding: 3px 8px;
                border-radius: 6px;
                font-weight: 500;
            }
            .upd-ver-old {
                background: rgba(255,255,255,0.05);
                color: var(--text-secondary, #888);
            }
            .upd-ver-new {
                background: rgba(207,163,70,0.12);
                color: var(--accent-gold, #cfa346);
            }
            .upd-arrow {
                color: var(--text-secondary, #555);
                font-size: 16px;
                display: flex;
            }
            .upd-badge {
                display: inline-flex;
                align-items: center;
                padding: 2px 8px;
                border-radius: 999px;
                font-size: 10.5px;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                margin-left: auto;
            }
            .upd-badge.stable {
                background: rgba(106,173,86,0.15);
                color: #7fd17d;
            }
            .upd-badge.dev {
                background: rgba(255,128,0,0.12);
                color: #ffb46e;
            }
            .upd-notes {
                margin-top: 10px;
                padding: 10px 12px;
                background: rgba(255,255,255,0.025);
                border-radius: 8px;
                border: 1px solid rgba(255,255,255,0.04);
                font-size: 12.5px;
                line-height: 1.55;
                color: var(--text-secondary, #999);
                max-height: 120px;
                overflow-y: auto;
                white-space: pre-line;
            }
            .upd-notes::-webkit-scrollbar { width: 4px; }
            .upd-notes::-webkit-scrollbar-thumb {
                background: rgba(255,255,255,0.1);
                border-radius: 4px;
            }
            .upd-actions {
                display: flex;
                gap: 8px;
                padding: 0 16px 14px;
            }
            .upd-btn {
                flex: 1;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                gap: 6px;
                padding: 9px 14px;
                border-radius: 8px;
                font-size: 12.5px;
                font-weight: 600;
                cursor: pointer;
                text-decoration: none;
                transition: background .15s, box-shadow .15s, transform .1s;
                border: none;
            }
            .upd-btn-primary {
                background: var(--accent-gold, #cfa346);
                color: #0b0b0b;
                box-shadow: 0 2px 8px rgba(207,163,70,0.2);
            }
            .upd-btn-primary:hover {
                background: #ddb04e;
                box-shadow: 0 4px 16px rgba(207,163,70,0.3);
                transform: translateY(-1px);
            }
            .upd-btn-secondary {
                background: rgba(255,255,255,0.05);
                color: var(--text-secondary, #999);
                border: 1px solid var(--border-color, #2a2a2a);
            }
            .upd-btn-secondary:hover {
                background: rgba(255,255,255,0.08);
                color: var(--text-primary, #e6e6e6);
            }
            .upd-btn .material-icons { font-size: 15px; }
        `;
        document.head.appendChild(style);
    }

    const channel = (options.channel || 'stable').toString().toLowerCase();
    const badgeClass = channel === 'dev' ? 'upd-badge dev' : 'upd-badge stable';
    const badgeLabel = channel === 'dev' ? 'DEV' : 'STABLE';

    const popup = document.createElement('div');
    popup.id = 'update-popup';
    popup.innerHTML = `
        <div class="upd-header">
            <div class="upd-icon"><span class="material-icons">upgrade</span></div>
            <span class="upd-title">Update available</span>
            <span class="${badgeClass}">${badgeLabel}</span>
            <button class="upd-close" title="Dismiss"><span class="material-icons">close</span></button>
        </div>
        <div class="upd-body">
            <div class="upd-versions">
                <span class="upd-ver upd-ver-old">v${localVersion}</span>
                <span class="upd-arrow material-icons">arrow_forward</span>
                <span class="upd-ver upd-ver-new">v${remoteVersion}</span>
            </div>
            ${notes ? `<div class="upd-notes">${notes}</div>` : ''}
        </div>
        <div class="upd-actions">
            <button class="upd-btn upd-btn-primary" id="trigger-auto-update" type="button">
                <span class="material-icons">bolt</span>
                Install
            </button>
            <a href="${releaseUrl}" target="_blank" class="upd-btn upd-btn-secondary">
                <span class="material-icons">open_in_new</span>
                Download
            </a>
        </div>
    `;
    document.body.appendChild(popup);

    const closeBtn = popup.querySelector('.upd-close');
    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            popup.classList.add('closing');
            popup.addEventListener('animationend', () => popup.remove(), { once: true });
            if (trackingVersion) {
                safeStorageSet(window.localStorage, UPDATE_DISMISSED_VERSION_KEY, trackingVersion);
            }
        });
    }

    const autoUpdateBtn = popup.querySelector('#trigger-auto-update');
    if (autoUpdateBtn) {
        autoUpdateBtn.addEventListener('click', () => startAutomaticUpdate(autoUpdateBtn, releaseUrl, trackingVersion));
    }

    const manualBtn = popup.querySelector('a.upd-btn-secondary');
    if (manualBtn && trackingVersion) {
        manualBtn.addEventListener('click', () => safeStorageSet(window.localStorage, UPDATE_DISMISSED_VERSION_KEY, trackingVersion), { once: true });
    }
}

async function startAutomaticUpdate(button, fallbackUrl, trackingVersion = null) {
    if (!button) {
        return;
    }

    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = 'Starting update...';

    try {
        const response = await fetch('/api/update/apply', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            const error = data.error || 'Unable to start automatic update.';
            showNotification(error, 'error');
            button.disabled = false;
            button.textContent = originalText;
            return;
        }

        showNotification('Update is installing. The app will close shortly.', 'success');
        setTimeout(() => {
            const popup = document.getElementById('update-popup');
            if (popup) {
                popup.remove();
            }
            if (trackingVersion) {
                safeStorageSet(window.localStorage, UPDATE_DISMISSED_VERSION_KEY, trackingVersion);
            }
        }, 1000);
    } catch (error) {
        console.error('Failed to start automatic update:', error);
        showNotification('Automatic update failed to start. Opening download page...', 'error');
        window.open(fallbackUrl, '_blank', 'noopener');
        button.disabled = false;
        button.textContent = originalText;
    }
}

// Helper to fetch market price via backend proxy to avoid CORS
async function fetchMarketPrice(itemId) {
    try {
        const response = await fetch(`/api/market/price/${itemId}`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        return await response.json();
    } catch (error) {
        console.error('Failed to fetch market price:', error);
        return { success: false, error: error.message };
    }
}

async function checkFullscreenMode() {
    const DISMISS_KEY = 'fullscreen-warning-dismissed-at';
    const COOLDOWN_MS = 2 * 60 * 60 * 1000; // 2 hours

    try {
        // Skip if the user dismissed the notification recently
        const dismissedAt = Number(localStorage.getItem(DISMISS_KEY) || 0);
        if (dismissedAt && Date.now() - dismissedAt < COOLDOWN_MS) return;

        const resp = await fetch('/api/window_mode');
        if (!resp.ok) return;
        const data = await resp.json();
        // mode 0 = exclusive fullscreen — warn the user
        if (data.mode === 0) {
            showNotification(
                'Dark and Darker is running in Exclusive Fullscreen. This can cause focus delays that interfere with sorting. Please switch to Borderless Windowed in your game settings for the best experience.',
                'warning',
                { id: 'fullscreen-warning', persistent: true }
            );
            // Record dismissal timestamp when user closes the notification
            const el = document.querySelector('[data-notification-id="fullscreen-warning"]');
            if (el) {
                const closeBtn = el.querySelector('.notification-close');
                if (closeBtn) {
                    closeBtn.addEventListener('click', () => {
                        try { localStorage.setItem(DISMISS_KEY, String(Date.now())); } catch (_) { }
                    }, { once: true });
                }
            }
        }
    } catch (e) {
        // Silently ignore — game may not be running or settings unreadable
    }
}

document.addEventListener('DOMContentLoaded', () => {
    checkForUpdates();
    checkFullscreenMode();
});