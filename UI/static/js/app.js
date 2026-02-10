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

window.navigateWithTransition = function (href) {
    const content = document.querySelector('.content');
    if (content) {
        content.style.opacity = '0';
        content.style.transform = 'translateY(5px)';
        requestAnimationFrame(() => {
            setTimeout(() => {
                window.location.href = href;
            }, 50);
        });
    } else {
        window.location.href = href;
    }
};

// Add active class to current navigation link
document.addEventListener('DOMContentLoaded', () => {
    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-link').forEach(link => {
        if (link.getAttribute('href') === currentPath) {
            link.classList.add('active');
        }
    });

    // Handle navigation transitions
    const links = document.querySelectorAll('.nav-link');
    links.forEach(link => {
        link.addEventListener('click', (e) => {
            if (link.classList.contains('disabled')) {
                e.preventDefault();
                return;
            }

            const guard = window.unsavedChangesGuard;
            const shouldPrompt = guard && typeof guard.shouldPrompt === 'function' && guard.shouldPrompt();
            if (shouldPrompt && typeof guard.requestNavigation === 'function') {
                e.preventDefault();
                guard.requestNavigation(link.href);
                return;
            }

            e.preventDefault();
            window.navigateWithTransition(link.href);
        });
    });

    // Listen for background initialization completion
    window.addEventListener('backgroundInitDone', () => {
        showNotification('Data loaded!', 'success');
        // Example: hide a loading spinner if you have one
        const spinner = document.getElementById('loading-spinner');
        if (spinner) spinner.style.display = 'none';
        // You can also trigger data refresh or enable UI here
    });
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
}

async function checkForUpdates(force = false) {
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
}

// Version comparison now handled by utils.js
// Remove duplicate function that's now in utils.js

function showUpdatePopup(remoteVersion, localVersion, releaseUrl, notes = '', trackingVersion = null, options = {}) {
    // Remove any existing popup
    const existing = document.getElementById('update-popup');
    if (existing) existing.remove();
    const popup = document.createElement('div');
    popup.id = 'update-popup';
    popup.style.position = 'fixed';
    popup.style.bottom = '30px';
    popup.style.right = '30px';
    popup.style.background = 'var(--bg-secondary, #241c17)';
    popup.style.color = 'var(--text-primary, #e4c869)';
    popup.style.padding = '24px 32px';
    popup.style.borderRadius = '8px';
    popup.style.border = '1px solid var(--border-color, #392e24)';
    popup.style.boxShadow = '0 8px 32px rgba(0, 0, 0, 0.6), 0 2px 8px rgba(228, 200, 105, 0.1)';
    popup.style.zIndex = '99999';
    popup.style.maxWidth = '380px';
    popup.style.minWidth = '320px';
    popup.style.backdropFilter = 'blur(8px)';
    popup.style.animation = 'slideInFromRight 0.3s cubic-bezier(0.4, 0, 0.2, 1)';

    // Add animation keyframes if not already defined
    if (!document.getElementById('update-popup-styles')) {
        const style = document.createElement('style');
        style.id = 'update-popup-styles';
        style.textContent = `
            @keyframes slideInFromRight {
                from {
                    transform: translateX(100%);
                    opacity: 0;
                }
                to {
                    transform: translateX(0);
                    opacity: 1;
                }
            }
            
            .update-popup-btn {
                background: var(--accent-gold, #e4c869);
                color: #1a1412;
                border: none;
                padding: 10px 20px;
                border-radius: 6px;
                cursor: pointer;
                font-weight: 600;
                font-size: 14px;
                text-decoration: none;
                display: inline-flex;
                align-items: center;
                gap: 8px;
                transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
                box-shadow: 0 2px 8px rgba(228, 200, 105, 0.2);
            }
            
            .update-popup-btn:hover {
                transform: translateY(-2px);
                box-shadow: 0 4px 16px rgba(228, 200, 105, 0.3);
                background: #f0d478;
            }
            
            .update-popup-close {
                background: transparent;
                border: 1px solid var(--border-color, #392e24);
                color: var(--text-secondary, #a89a6c);
                padding: 8px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 16px;
                line-height: 1;
                transition: all 0.2s ease;
                width: 32px;
                height: 32px;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            
            .update-popup-close:hover {
                background: rgba(255, 255, 255, 0.05);
                color: var(--text-primary, #e4c869);
                border-color: var(--accent-gold, #e4c869);
            }

            .update-badge {
                display: inline-flex;
                align-items: center;
                gap: 6px;
                padding: 2px 10px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: 600;
                margin-left: 8px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }

            .update-badge.stable {
                background: rgba(106, 173, 86, 0.2);
                color: #8fe18d;
                border: 1px solid rgba(143, 225, 141, 0.5);
            }

            .update-badge.dev {
                background: rgba(255, 128, 0, 0.15);
                color: #ffb46e;
                border: 1px solid rgba(255, 180, 110, 0.4);
            }
        `;
        document.head.appendChild(style);
    }

    const channel = (options.channel || 'stable').toString().toLowerCase();
    const releaseTag = options.releaseTag || '';
    const badgeClass = channel === 'dev' ? 'update-badge dev' : 'update-badge stable';
    const badgeLabel = channel === 'dev' ? 'Test build' : 'Stable release';
    const releaseTagSnippet = releaseTag && !releaseTag.startsWith('Release')
        ? `<div style="margin-top: 4px; font-size: 12px; color: var(--text-secondary, #988c65);">Tag: ${releaseTag}</div>`
        : '';

    popup.innerHTML = `
        <div style="display: flex; align-items: flex-start; gap: 16px; margin-bottom: 16px;">
            <div style="
                background: linear-gradient(135deg, var(--accent-gold, #e4c869), #f0d478);
                border-radius: 50%;
                padding: 12px;
                box-shadow: 0 4px 12px rgba(228, 200, 105, 0.3);
            ">
                <span class="material-icons" style="font-size: 24px; color: #1a1412;">system_update_alt</span>
            </div>
            <div style="flex: 1;">
                <h3 style="
                    color: var(--accent-gold, #e4c869);
                    font-size: 18px;
                    font-weight: 600;
                    margin: 0 0 8px 0;
                    letter-spacing: 0.5px;
                ">Update Available!</h3>
                <div style="
                    font-size: 14px;
                    color: var(--text-secondary, #a89a6c);
                    line-height: 1.4;
                ">
                    <div style="margin-bottom: 4px;">
                        <span style="color: var(--text-primary, #e4c869);">Current:</span> v${localVersion}
                    </div>
                    <div>
                        <span style="color: var(--text-primary, #e4c869);">Latest:</span> 
                        <span style="color: var(--accent-gold, #e4c869); font-weight: 600;">v${remoteVersion}</span>
                        <span class="${badgeClass}">${badgeLabel}</span>
                    </div>
                    ${releaseTagSnippet}
                </div>
                ${notes ? `<div style="margin-top: 12px; padding: 12px; background: rgba(255, 255, 255, 0.04); border-radius: 6px; border: 1px solid rgba(228, 200, 105, 0.2); font-size: 13px; line-height: 1.6; color: var(--text-secondary, #c0b18a); white-space: pre-line;">${notes}</div>` : ''}
            </div>
            <button id="close-update-popup" class="update-popup-close" title="Close">✕</button>
        </div>
        <div style="
            display: flex;
            justify-content: flex-end;
            gap: 12px;
            padding-top: 12px;
            border-top: 1px solid var(--border-color, #392e24);
        ">
            <button class="update-popup-btn" id="trigger-auto-update" type="button">
                <span class="material-icons" style="font-size: 18px;">bolt</span>
                Install update
            </button>
            <a href="${releaseUrl}" target="_blank" class="update-popup-btn">
                <span class="material-icons" style="font-size: 18px;">open_in_new</span>
                Download manually
            </a>
        </div>
    `;
    document.body.appendChild(popup);

    const closeBtn = popup.querySelector('.update-popup-close');
    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            popup.remove();
            if (trackingVersion) {
                safeStorageSet(window.localStorage, UPDATE_DISMISSED_VERSION_KEY, trackingVersion);
            }
        });
    }

    const autoUpdateBtn = popup.querySelector('#trigger-auto-update');
    if (autoUpdateBtn) {
        autoUpdateBtn.addEventListener('click', () => startAutomaticUpdate(autoUpdateBtn, releaseUrl, trackingVersion));
    }

    const manualBtn = popup.querySelector('a.update-popup-btn');
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

document.addEventListener('DOMContentLoaded', () => {
    checkForUpdates();
});