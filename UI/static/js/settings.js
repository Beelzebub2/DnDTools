// Simplified Settings JavaScript
document.addEventListener('DOMContentLoaded', async () => {
    const interfaceSelect = document.getElementById('interface');
    const sortHotkeyInput = document.getElementById('sortHotkey');
    const cancelHotkeyInput = document.getElementById('cancelHotkey');
    const sortSpeedInput = document.getElementById('sortSpeed');
    const noDelayCheckbox = document.getElementById('noDelay');
    const resolutionSelect = document.getElementById('resolution');
    const wiresharkPathInput = document.getElementById('wiresharkPath');
    const browseWiresharkButton = document.getElementById('browseWiresharkPath');
    const detectWiresharkButton = document.getElementById('detectWiresharkPath');
    const detectedResolutionSpan = document.querySelector('#detectedResolution');
    const refreshResolutionBtn = document.getElementById('refreshResolution');
    const clearQuestDataButton = document.getElementById('clearQuestData');
    const clearCharacterDataButton = document.getElementById('clearCharacterData');
    const includeDevCheckbox = document.getElementById('includeDevReleases');
    const autoUpdateCheckbox = document.getElementById('autoUpdateEnabled');
    const checkForUpdatesButton = document.getElementById('checkForUpdatesBtn');
    const closeToTrayCheckbox = document.getElementById('closeToTrayEnabled');
    const developerModeCheckbox = document.getElementById('developerMode');
    const feedbackSyncCheckbox = document.getElementById('sortFeedbackSyncEnabled');
    const overlayEnabledCheckbox = document.getElementById('overlayEnabled');
    const overlayHotkeyInput = document.getElementById('overlayHotkey');
    const overlayOpacitySlider = document.getElementById('overlayOpacity');
    const overlayOpacityValue = document.getElementById('overlayOpacityValue');
    const tabMapSelects = [
        document.getElementById('tabMap0'),
        document.getElementById('tabMap1'),
        document.getElementById('tabMap2'),
        document.getElementById('tabMap3'),
        document.getElementById('tabMap4'),
        document.getElementById('tabMap5'),
        document.getElementById('tabMap6'),
        document.getElementById('tabMap7'),
    ];
    const saveButton = document.getElementById('saveSettings');
    const resetButton = document.getElementById('resetSettings');
    const tabButtons = Array.from(document.querySelectorAll('.nav-pill'));
    const panels = Array.from(document.querySelectorAll('.settings-panel'));

    let currentSettings = {};
    let normalizedSettingsSnapshot = null;
    let lastManualSortSpeed = 0.01;
    let isApplyingSettings = false;
    let isDirty = false;
    let changeCheckScheduled = false;
    const AUTOSAVE_DEBOUNCE_MS = 200;
    let autosaveTimerId = null;
    let autoSaveInFlight = false;
    let autoSaveQueued = false;
    const QUEST_PROGRESS_STORAGE_KEY = 'dndtools.questProgress.v1';
    const FIELD_LABELS = {
        interface: 'Network Interface',
        sortHotkey: 'Sort Stash Hotkey',
        cancelHotkey: 'Cancel Sort Hotkey',
        sortSpeed: 'Sort Speed',
        resolution: 'Game Resolution',
        wiresharkPath: 'Wireshark Path',
        includeDevReleases: 'Development Builds Opt-In',
        autoUpdateEnabled: 'Auto Update Check',
        closeToTrayEnabled: 'Close to Tray',
        developerMode: 'Developer Mode',
        sortFeedbackSyncEnabled: 'Global Sort Learning',
        overlayEnabled: 'Game Overlay',
        overlayHotkey: 'Overlay Toggle Hotkey',
        overlayOpacity: 'Overlay Opacity',
        stashTabMapping: 'Stash Tab Mapping'
    };

    function updateSaveButtonState() {
        if (!saveButton) {
            return;
        }

        if (saveButton.classList.contains('saving')) {
            saveButton.disabled = true;
            saveButton.setAttribute('aria-disabled', 'true');
            return;
        }

        const shouldDisable = !isDirty;
        saveButton.disabled = shouldDisable;
        saveButton.setAttribute('aria-disabled', shouldDisable ? 'true' : 'false');
    }

    const beforeUnloadHandler = (event) => {
        // Save on exit popup disabled to prevent user confusion
        return undefined;
    };

    window.addEventListener('beforeunload', beforeUnloadHandler);

    setUnsavedChanges(false);

    function activateTab(tabId, options = {}) {
        if (!tabId || !panels.length || !tabButtons.length) {
            return;
        }

        const { updateHash = true, scrollIntoView = false } = options;

        tabButtons.forEach((button) => {
            const isActive = button.dataset.tab === tabId;
            button.classList.toggle('active', isActive);
            button.setAttribute('aria-selected', isActive ? 'true' : 'false');
            if (isActive && scrollIntoView) {
                button.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
            }
        });

        panels.forEach((panel) => {
            const isActive = panel.dataset.panel === tabId;
            panel.classList.toggle('active', isActive);
            panel.setAttribute('aria-hidden', isActive ? 'false' : 'true');
        });

        if (updateHash) {
            try {
                const newHash = `#${tabId}`;
                if (window.location.hash !== newHash) {
                    history.replaceState(null, '', newHash);
                }
            } catch (error) {
                /* ignore hash errors */
            }
        }
    }

    function getPanelIdFromHash() {
        const hashValue = window.location.hash?.replace('#', '') || '';
        if (!hashValue) {
            return '';
        }
        return panels.some((panel) => panel.dataset.panel === hashValue) ? hashValue : '';
    }

    function handleTabHashChange() {
        const tabId = getPanelIdFromHash();
        if (tabId) {
            activateTab(tabId, { updateHash: false });
        }
    }

    function initializeTabs() {
        if (!tabButtons.length || !panels.length) {
            return;
        }

        tabButtons.forEach((button) => {
            button.addEventListener('click', (event) => {
                event.preventDefault();
                // Force immediate save when switching settings tabs
                if (isDirty) {
                    scheduleAutoSave({ immediate: true });
                }
                const tabId = button.dataset.tab;
                activateTab(tabId, { scrollIntoView: true });
            });
        });

        window.addEventListener('hashchange', handleTabHashChange);

        const hashTab = getPanelIdFromHash();
        const fallbackTab = tabButtons[0]?.dataset.tab;
        activateTab(hashTab || fallbackTab, { updateHash: Boolean(hashTab) });
    }

    initializeTabs();

    function normalizeForComparison(settings = {}) {
        const toNumber = (value) => {
            const numeric = Number(value);
            return Number.isFinite(numeric) ? numeric : 0;
        };

        const normalizeBoolean = (value) => {
            if (typeof value === 'string') {
                const normalized = value.trim().toLowerCase();
                return ['1', 'true', 'yes', 'on'].includes(normalized);
            }
            return Boolean(value);
        };

        return {
            interface: (settings.interface || '').trim(),
            sortHotkey: (settings.sortHotkey || '').trim().toLowerCase(),
            cancelHotkey: (settings.cancelHotkey || '').trim().toLowerCase(),
            sortSpeed: toNumber(settings.sortSpeed),
            resolution: (settings.resolution || 'Auto').trim(),
            wiresharkPath: (settings.wiresharkPath || '').trim(),
            includeDevReleases: normalizeBoolean(settings.includeDevReleases),
            autoUpdateEnabled: normalizeBoolean(
                settings.autoUpdateEnabled === undefined ? true : settings.autoUpdateEnabled
            ),
            closeToTrayEnabled: normalizeBoolean(
                settings.closeToTrayEnabled === undefined ? true : settings.closeToTrayEnabled
            ),
            developerMode: normalizeBoolean(settings.developerMode),
            sortFeedbackSyncEnabled: normalizeBoolean(settings.sortFeedbackSyncEnabled),
            overlayEnabled: normalizeBoolean(settings.overlayEnabled),
            overlayHotkey: (settings.overlayHotkey || '').trim().toLowerCase(),
            overlayOpacity: toNumber(settings.overlayOpacity),
            stashTabMapping: JSON.stringify(settings.stashTabMapping || [0, 0, 0, 0, 0, 0, 0, 0])
        };
    }

    function updateCurrentSettings(settings) {
        currentSettings = {
            interface: settings.interface || '',
            sortHotkey: settings.sortHotkey || 'ctrl+f11',
            cancelHotkey: settings.cancelHotkey || 'ctrl+f12',
            sortSpeed: parseSortSpeed(settings.sortSpeed, 0.2),
            resolution: settings.resolution || 'Auto',
            wiresharkPath: settings.wiresharkPath || '',
            includeDevReleases: Boolean(settings.includeDevReleases),
            autoUpdateEnabled: settings.autoUpdateEnabled !== false,
            closeToTrayEnabled: settings.closeToTrayEnabled !== false,
            developerMode: Boolean(settings.developerMode),
            sortFeedbackSyncEnabled: Boolean(settings.sortFeedbackSyncEnabled),
            overlayEnabled: Boolean(settings.overlayEnabled),
            overlayHotkey: settings.overlayHotkey || 'ctrl+shift+o',
            overlayOpacity: parseFloat(settings.overlayOpacity) || 0.92,
            stashTabMapping: settings.stashTabMapping || [0, 0, 0, 0, 0, 0, 0, 0]
        };
        normalizedSettingsSnapshot = normalizeForComparison(currentSettings);
    }

    function getFormSettings() {
        return {
            interface: interfaceSelect.value,
            sortHotkey: sortHotkeyInput.value,
            cancelHotkey: cancelHotkeyInput.value,
            sortSpeed: noDelayCheckbox?.checked ? 0 : parseSortSpeed(
                sortSpeedInput.value,
                lastManualSortSpeed > 0 ? lastManualSortSpeed : 0.2
            ),
            resolution: resolutionSelect.value,
            wiresharkPath: wiresharkPathInput ? wiresharkPathInput.value : '',
            includeDevReleases: includeDevCheckbox ? includeDevCheckbox.checked : false,
            autoUpdateEnabled: autoUpdateCheckbox ? autoUpdateCheckbox.checked : true,
            closeToTrayEnabled: closeToTrayCheckbox ? closeToTrayCheckbox.checked : true,
            developerMode: developerModeCheckbox ? developerModeCheckbox.checked : false,
            sortFeedbackSyncEnabled: feedbackSyncCheckbox ? feedbackSyncCheckbox.checked : false,
            overlayEnabled: overlayEnabledCheckbox ? overlayEnabledCheckbox.checked : false,
            overlayHotkey: overlayHotkeyInput ? overlayHotkeyInput.value : 'ctrl+shift+o',
            overlayOpacity: overlayOpacitySlider ? parseInt(overlayOpacitySlider.value, 10) / 100 : 0.92,
            stashTabMapping: tabMapSelects.map(el => el ? parseInt(el.value, 10) : 0)
        };
    }

    function clearAutosaveTimer() {
        if (autosaveTimerId) {
            clearTimeout(autosaveTimerId);
            autosaveTimerId = null;
        }
    }

    function scheduleAutoSave(options = {}) {
        const { immediate = false, force = false } = options;

        if (isApplyingSettings) {
            return;
        }

        if (immediate) {
            clearAutosaveTimer();
            void triggerAutoSave({ forceSave: force });
            return;
        }

        if (!isDirty && !force) {
            return;
        }

        clearAutosaveTimer();
        autosaveTimerId = window.setTimeout(() => {
            autosaveTimerId = null;
            void triggerAutoSave({ forceSave: force });
        }, AUTOSAVE_DEBOUNCE_MS);
    }

    async function triggerAutoSave({ forceSave = false } = {}) {
        if (autoSaveInFlight) {
            autoSaveQueued = true;
            return;
        }

        if (!isDirty && !forceSave) {
            return;
        }

        autoSaveInFlight = true;
        try {
            await saveSettings({
                showNotification: true,
                suppressSuccessToast: true,
                showAnimation: false,
                notifyIfUnchanged: false,
                forceSave
            });
        } finally {
            autoSaveInFlight = false;
            if (autoSaveQueued) {
                autoSaveQueued = false;
                scheduleAutoSave();
            }
        }
    }

    function setUnsavedChanges(value) {
        const previousState = isDirty;
        isDirty = Boolean(value);
        window.hasUnsavedChanges = isDirty;
        document.body.classList.toggle('has-unsaved-settings', isDirty);
        if (!isDirty) {
            clearAutosaveTimer();
        } else if (!previousState && !isApplyingSettings) {
            scheduleAutoSave();
        }
        updateSaveButtonState();
    }

    function syncDeveloperModeFlag(enabled) {
        try {
            if (typeof window.setDeveloperModeEnabled === 'function') {
                window.setDeveloperModeEnabled(Boolean(enabled));
            } else {
                window.developerModeEnabled = Boolean(enabled);
            }
        } catch (error) {
            console.warn('Failed to sync developer mode flag', error);
            window.developerModeEnabled = Boolean(enabled);
        }
    }

    function evaluateUnsavedChanges() {
        if (!normalizedSettingsSnapshot) {
            setUnsavedChanges(false);
            return;
        }

        const normalizedForm = normalizeForComparison(getFormSettings());
        const keys = Object.keys(normalizedSettingsSnapshot);
        const hasChanges = keys.some((key) => normalizedForm[key] !== normalizedSettingsSnapshot[key]);
        setUnsavedChanges(hasChanges);
    }

    function scheduleDirtyCheck() {
        if (changeCheckScheduled) {
            return;
        }
        changeCheckScheduled = true;
        requestAnimationFrame(() => {
            changeCheckScheduled = false;
            evaluateUnsavedChanges();
        });
    }

    function runWithApplyingFlag(fn) {
        const previous = isApplyingSettings;
        isApplyingSettings = true;
        try {
            fn();
        } finally {
            isApplyingSettings = previous;
        }
    }

    function navigateTo(href) {
        if (typeof window.navigateWithTransition === 'function') {
            window.navigateWithTransition(href);
        } else {
            window.location.href = href;
        }
    }

    function showUnsavedPrompt(onProceed, context = 'navigation') {
        const titles = {
            navigation: 'Leave Settings?',
            close: 'Exit Settings?'
        };

        const messages = {
            navigation: 'You have unsaved changes. Save them before leaving this page?',
            close: 'You have unsaved changes. Save them before exiting the app?'
        };

        createUnsavedChangesModal({
            title: titles[context] || titles.navigation,
            message: messages[context] || messages.navigation,
            saveLabel: 'Exit and Save',
            discardLabel: 'Exit Anyway',
            cancelLabel: 'Stay Here',
            onSave: async () => {
                const saved = await saveSettings({
                    showNotification: true,
                    suppressSuccessToast: true,
                    showAnimation: false
                });

                if (saved) {
                    setUnsavedChanges(false);
                    if (typeof onProceed === 'function') {
                        onProceed();
                    }
                    return true;
                }

                return false;
            },
            onDiscard: () => {
                setUnsavedChanges(false);
                if (typeof onProceed === 'function') {
                    onProceed();
                }
            },
            onCancel: () => {
                evaluateUnsavedChanges();
            }
        });
    }

    function setupUnsavedChangesGuard() {
        window.unsavedChangesGuard = {
            shouldPrompt: () => false,
            requestNavigation: (href) => {
                navigateTo(href);
            },
            requestClose: (proceed) => {
                if (typeof proceed === 'function') {
                    proceed();
                }
            }
        };
    }

    async function guardedLoad(fn, label) {
        try {
            await fn();
        } catch (error) {
            console.error(`Failed to load ${label}:`, error);
            showNotification(`Failed to load ${label}`, 'warning');
        }
    }

    await guardedLoad(loadInterfaces, 'network interfaces');
    await guardedLoad(loadSettings, 'settings');
    await guardedLoad(loadDetectedResolution, 'resolution detection');

    // Load network interfaces
    async function loadInterfaces() {
        if (!interfaceSelect) {
            throw new Error('Interface select element is missing');
        }

        try {
            const response = await fetch('/api/network_interfaces');
            if (!response.ok) {
                const errorText = await response.text().catch(() => '');
                throw new Error(errorText || `Failed to load interfaces (status ${response.status})`);
            }

            const data = await response.json();
            interfaceSelect.innerHTML = '';

            if (Array.isArray(data.interfaces) && data.interfaces.length > 0) {
                data.interfaces.forEach((iface) => {
                    const option = document.createElement('option');
                    option.value = iface;
                    option.textContent = iface;
                    interfaceSelect.appendChild(option);
                });
            } else {
                setInterfaceFallbackOption('No interfaces found');
            }
        } catch (error) {
            setInterfaceFallbackOption('Unable to load interfaces');
            throw error;
        }
    }

    function setInterfaceFallbackOption(label) {
        if (!interfaceSelect) {
            return;
        }
        interfaceSelect.innerHTML = '';
        const option = document.createElement('option');
        option.value = '';
        option.textContent = label;
        interfaceSelect.appendChild(option);
    }

    // Load current settings
    async function fetchSettingsFromServer() {
        const response = await fetch('/api/settings');
        if (!response.ok) {
            const message = await response.text().catch(() => '');
            throw new Error(message || `Failed to load settings (status ${response.status})`);
        }
        return response.json();
    }

    function applySettingsToForm(settings) {
        if (!settings || typeof settings !== 'object') {
            return null;
        }

        updateCurrentSettings(settings);

        const parsedSpeed = parseSortSpeed(currentSettings.sortSpeed, 0.2);
        if (parsedSpeed > 0) {
            lastManualSortSpeed = parsedSpeed;
        }

        runWithApplyingFlag(() => {
            interfaceSelect.value = currentSettings.interface || '';
            sortHotkeyInput.value = currentSettings.sortHotkey || 'ctrl+f11';
            cancelHotkeyInput.value = currentSettings.cancelHotkey || 'ctrl+f12';
            sortSpeedInput.value = toDisplaySpeed(parsedSpeed);
            resolutionSelect.value = currentSettings.resolution || 'Auto';

            if (noDelayCheckbox) {
                noDelayCheckbox.checked = parsedSpeed <= 0;
            }

            if (wiresharkPathInput) {
                const detectedPath = currentSettings.wiresharkPath || '';
                wiresharkPathInput.value = detectedPath;
                wiresharkPathInput.dataset.defaultValue = detectedPath;
            }

            if (includeDevCheckbox) {
                includeDevCheckbox.checked = Boolean(currentSettings.includeDevReleases);
            }

            if (autoUpdateCheckbox) {
                autoUpdateCheckbox.checked = currentSettings.autoUpdateEnabled !== false;
            }

            if (closeToTrayCheckbox) {
                closeToTrayCheckbox.checked = currentSettings.closeToTrayEnabled !== false;
            }

            if (developerModeCheckbox) {
                developerModeCheckbox.checked = Boolean(currentSettings.developerMode);
            }

            if (feedbackSyncCheckbox) {
                feedbackSyncCheckbox.checked = Boolean(currentSettings.sortFeedbackSyncEnabled);
            }

            if (overlayEnabledCheckbox) {
                overlayEnabledCheckbox.checked = Boolean(currentSettings.overlayEnabled);
            }

            if (overlayHotkeyInput) {
                overlayHotkeyInput.value = currentSettings.overlayHotkey || 'ctrl+shift+o';
            }

            if (overlayOpacitySlider) {
                const opacityPercent = Math.round((currentSettings.overlayOpacity || 0.92) * 100);
                overlayOpacitySlider.value = opacityPercent;
                if (overlayOpacityValue) {
                    overlayOpacityValue.textContent = opacityPercent + '%';
                }
            }

            applyOverlayDependentState();

            // Tab mapping dropdowns (all 8 positions are configurable)
            const mapping = currentSettings.stashTabMapping || [0, 0, 0, 0, 0, 0, 0, 0];
            for (let i = 0; i < 8; i++) {
                if (tabMapSelects[i]) {
                    tabMapSelects[i].value = String(mapping[i] || 0);
                }
            }
        });

        runWithApplyingFlag(() => {
            applyNoDelayUIState();
        });

        syncDeveloperModeFlag(Boolean(currentSettings.developerMode));

        if (typeof window.setCloseToTrayEnabled === 'function') {
            window.setCloseToTrayEnabled(currentSettings.closeToTrayEnabled !== false);
        }

        evaluateUnsavedChanges();

        return { ...currentSettings };
    }

    async function loadSettings(options = {}) {
        const { data: providedData = null, apply = true } = options;

        let data = providedData;
        if (!data) {
            try {
                data = await fetchSettingsFromServer();
            } catch (error) {
                console.error('Failed to load settings:', error);
                showNotification('Failed to load settings', 'error');
                return null;
            }
        }

        if (!apply) {
            return data;
        }

        return applySettingsToForm(data);
    }

    // Load detected resolution
    async function loadDetectedResolution() {
        try {
            const response = await fetch('/api/auto_resolution');
            const data = await response.json();
            if (detectedResolutionSpan) {
                detectedResolutionSpan.textContent = `${data.resolution || 'Not detected'}`;
            }
        } catch (error) {
            console.error('Failed to detect resolution:', error);
            if (detectedResolutionSpan) {
                detectedResolutionSpan.textContent = 'Detection failed';
            }
        }
    }

    async function pickWiresharkPath() {
        if (!browseWiresharkButton || !wiresharkPathInput) {
            return;
        }

        if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.select_wireshark_path) {
            showNotification('Desktop path picker is unavailable. Enter the path manually.', 'warning');
            return;
        }

        browseWiresharkButton.disabled = true;
        browseWiresharkButton.classList.add('loading');

        try {
            const result = await window.pywebview.api.select_wireshark_path();
            if (result && result.success && result.path) {
                wiresharkPathInput.value = result.path;
                scheduleDirtyCheck();
            } else if (result && result.error) {
                showNotification(result.error, 'error');
            }
        } catch (error) {
            console.error('Wireshark picker error:', error);
            showNotification('Failed to select Wireshark path', 'error');
        } finally {
            browseWiresharkButton.disabled = false;
            browseWiresharkButton.classList.remove('loading');
        }
    }

    async function autoDetectWireshark() {
        if (!detectWiresharkButton || !wiresharkPathInput) {
            return;
        }

        if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.detect_wireshark_path) {
            showNotification('Auto-detect requires the desktop app. Enter the path manually.', 'warning');
            return;
        }

        detectWiresharkButton.disabled = true;
        detectWiresharkButton.classList.add('loading');

        try {
            const result = await window.pywebview.api.detect_wireshark_path();
            if (result && result.success && result.path) {
                wiresharkPathInput.value = result.path;
                showNotification('Wireshark installation detected.', 'success');
                scheduleDirtyCheck();
            } else if (result && result.error) {
                showNotification(result.error, 'error');
            } else {
                showNotification('Unable to locate Wireshark automatically.', 'warning');
            }
        } catch (error) {
            console.error('Auto-detect error:', error);
            showNotification('Wireshark auto-detect failed.', 'error');
        } finally {
            detectWiresharkButton.disabled = false;
            detectWiresharkButton.classList.remove('loading');
        }
    }

    function handleClearQuestData() {
        if (!clearQuestDataButton) {
            return;
        }

        createUnsavedChangesModal({
            title: 'Clear quest tracker data?',
            message: 'This will delete cached quest information and your saved quest progress. The tracker will download fresh data next time you open it.',
            bodyText: 'Choose how you want to continue:',
            bodyTips: [
                { icon: 'bookmark_added', text: 'Keep Data retains cached quests and your submitted progress.' },
                { icon: 'delete_sweep', text: 'Clear Data removes cached quests and resets your quest progress.' }
            ],
            saveLabel: 'Clear Data',
            discardLabel: 'Keep Data',
            cancelLabel: 'Cancel',
            onSave: async () => {
                try {
                    const response = await fetch('/api/quests/cache', {
                        method: 'DELETE',
                        headers: {
                            'Content-Type': 'application/json'
                        }
                    });
                    let data = null;
                    try {
                        data = await response.json();
                    } catch (parseError) {
                        /* ignore */
                    }

                    if (!response.ok || (data && data.success === false)) {
                        const message = data && data.error ? data.error : 'Failed to clear quest data';
                        throw new Error(message);
                    }

                    try {
                        window.localStorage?.removeItem(QUEST_PROGRESS_STORAGE_KEY);
                    } catch (storageError) {
                        console.warn('Failed to clear local quest progress', storageError);
                    }

                    try {
                        window.dispatchEvent(new CustomEvent('questDataCleared'));
                    } catch (dispatchError) {
                        console.warn('Failed to dispatch questDataCleared event', dispatchError);
                    }

                    showNotification('Quest tracker cache cleared.', 'success');
                    return true;
                } catch (error) {
                    console.error('Failed to clear quest cache', error);
                    showNotification(error.message || 'Failed to clear quest data', 'error');
                    return false;
                }
            }
        });
    }

    function handleClearCharacterData() {
        if (!clearCharacterDataButton) {
            return;
        }

        createUnsavedChangesModal({
            title: 'Delete all character data?',
            message: 'This will remove every captured character packet and stash snapshot stored on this device. This cannot be undone.',
            bodyText: 'Decide what to do with your captured data:',
            bodyTips: [
                { icon: 'inventory_2', text: 'Keep Data leaves all captured characters and stash data untouched.' },
                { icon: 'delete_forever', text: 'Delete Data removes all captured characters and stash data from this device.' }
            ],
            saveLabel: 'Delete Data',
            discardLabel: 'Keep Data',
            cancelLabel: 'Cancel',
            onSave: async () => {
                try {
                    const response = await fetch('/api/characters/data', {
                        method: 'DELETE',
                        headers: {
                            'Content-Type': 'application/json'
                        }
                    });

                    let data = null;
                    try {
                        data = await response.json();
                    } catch (parseError) {
                        /* ignore */
                    }

                    if (!response.ok || (data && data.success === false)) {
                        const message = data && data.error ? data.error : 'Failed to delete character data';
                        throw new Error(message);
                    }

                    try {
                        window.dispatchEvent(new CustomEvent('characterDataCleared'));
                    } catch (dispatchError) {
                        console.warn('Failed to dispatch characterDataCleared event', dispatchError);
                    }

                    showNotification('All captured character data deleted.', 'success');
                    return true;
                } catch (error) {
                    console.error('Failed to delete character data', error);
                    showNotification(error.message || 'Failed to delete character data', 'error');
                    return false;
                }
            }
        });
    }

    function parseSortSpeed(value, fallback = 0.2) {
        const numeric = parseFloat(value);
        if (!Number.isFinite(numeric)) {
            return fallback;
        }
        return numeric;
    }

    function toDisplaySpeed(value, fallback = 0.2) {
        if (!Number.isFinite(value)) {
            return fallback.toFixed(2);
        }
        return value.toFixed(2);
    }

    function enableNoDelayMode() {
        sortSpeedInput.value = '0.00';
        sortSpeedInput.disabled = true;
        sortSpeedInput.classList.add('input-disabled');
    }

    function disableNoDelayMode() {
        sortSpeedInput.disabled = false;
        sortSpeedInput.classList.remove('input-disabled');
        const restore = lastManualSortSpeed > 0 ? lastManualSortSpeed : 0.2;
        sortSpeedInput.value = toDisplaySpeed(restore);
    }

    function applyNoDelayUIState() {
        if (!noDelayCheckbox) {
            return;
        }
        if (noDelayCheckbox.checked) {
            enableNoDelayMode();
        } else {
            disableNoDelayMode();
        }
    }

    function applyOverlayDependentState() {
        const isEnabled = overlayEnabledCheckbox && overlayEnabledCheckbox.checked;
        document.querySelectorAll('.overlay-dependent-setting').forEach((el) => {
            el.style.opacity = isEnabled ? '1' : '0.45';
            el.style.pointerEvents = isEnabled ? 'auto' : 'none';
        });
    }

    if (overlayEnabledCheckbox) {
        overlayEnabledCheckbox.addEventListener('change', () => {
            applyOverlayDependentState();
        });
    }

    if (overlayOpacitySlider && overlayOpacityValue) {
        overlayOpacitySlider.addEventListener('input', () => {
            overlayOpacityValue.textContent = overlayOpacitySlider.value + '%';
        });
    }

    // Enhanced hotkey recording functionality
    function setupHotkeyRecording(input) {
        let pressedKeys = new Set();
        let isRecording = false;
        let recordingTimeout = null;
        let feedbackElement = null;
        let previousValue = '';  // Stores the value before recording starts

        // Create feedback element
        function createFeedbackElement() {
            if (feedbackElement) return feedbackElement;

            feedbackElement = document.createElement('div');
            feedbackElement.className = 'hotkey-feedback';
            feedbackElement.style.cssText = `
                position: absolute;
                top: 100%;
                left: 0;
                right: 0;
                background: linear-gradient(135deg, var(--accent-gold), var(--accent-brown));
                color: var(--bg-dark);
                padding: 0.5rem;
                border-radius: 0 0 8px 8px;
                font-size: 0.85rem;
                font-weight: 500;
                text-align: center;
                z-index: 1000;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(207, 163, 70, 0.3);
                animation: slideDown 0.2s ease;
            `;

            // Add to parent container
            const parent = input.parentElement;
            if (!parent.style.position || parent.style.position === 'static') {
                parent.style.position = 'relative';
            }
            parent.appendChild(feedbackElement);
            return feedbackElement;
        }

        function removeFeedbackElement() {
            if (feedbackElement) {
                feedbackElement.style.animation = 'slideUp 0.2s ease';
                setTimeout(() => {
                    if (feedbackElement && feedbackElement.parentNode) {
                        feedbackElement.parentNode.removeChild(feedbackElement);
                    }
                    feedbackElement = null;
                }, 200);
            }
        }

        function updateFeedback(text) {
            const feedback = createFeedbackElement();
            feedback.textContent = text;
        }

        const suppressBrowserShortcuts = (event) => {
            if (!isRecording) {
                return;
            }

            // Prevent default browser shortcuts (F-keys, Ctrl+R, etc.) from triggering while recording.
            event.preventDefault();

            // If focus slipped away while recording, redirect it back so we capture correctly.
            if (document.activeElement !== input) {
                input.focus({ preventScroll: true });
            }
        };

        let suppressionAttached = false;

        function startRecording() {
            isRecording = true;
            previousValue = input.value || '';
            pressedKeys.clear();
            input.style.backgroundColor = 'rgba(207, 163, 70, 0.1)';
            input.style.borderColor = 'var(--accent-gold)';
            input.value = '';
            updateFeedback('Press keys... (release all to save)');

            if (!suppressionAttached) {
                window.addEventListener('keydown', suppressBrowserShortcuts, true);
                suppressionAttached = true;
            }
        }

        function stopRecording() {
            isRecording = false;
            input.style.backgroundColor = '';
            input.style.borderColor = '';
            removeFeedbackElement();

            if (suppressionAttached) {
                window.removeEventListener('keydown', suppressBrowserShortcuts, true);
                suppressionAttached = false;
            }

            if (recordingTimeout) {
                clearTimeout(recordingTimeout);
                recordingTimeout = null;
            }
        }

        function updateHotkeyDisplay() {
            if (pressedKeys.size === 0) {
                input.value = '';
                return;
            }

            const modifierOrder = ['ctrl', 'alt', 'shift', 'meta'];
            const modifiers = [];
            const regularKeys = [];

            pressedKeys.forEach(key => {
                if (modifierOrder.includes(key)) {
                    modifiers.push(key);
                } else {
                    regularKeys.push(key);
                }
            });

            // Sort modifiers in standard order
            modifiers.sort((a, b) => modifierOrder.indexOf(a) - modifierOrder.indexOf(b));

            const allKeys = [...modifiers, ...regularKeys];
            input.value = allKeys.join('+');

            // Update feedback
            if (pressedKeys.size > 0) {
                updateFeedback(`${allKeys.join('+').toUpperCase()} - Release all keys to save`);
            }
        }

        // Focus event
        input.addEventListener('focus', (e) => {
            startRecording();
        });

        // Blur event
        input.addEventListener('blur', (e) => {
            // Only stop recording if we're not in the middle of a key combination
            if (!isRecording || pressedKeys.size === 0) {
                // Restore previous value if nothing was recorded
                if (isRecording && (!input.value || input.value === '')) {
                    input.value = previousValue;
                }
                stopRecording();
            }
        });

        // Keydown event
        input.addEventListener('keydown', (e) => {
            if (!isRecording) return;

            e.preventDefault();
            e.stopPropagation();

            // Clear any existing timeout
            if (recordingTimeout) {
                clearTimeout(recordingTimeout);
                recordingTimeout = null;
            }

            // Map key to standard name
            let keyName = e.key.toLowerCase();

            // Handle modifier keys
            if (e.ctrlKey && !pressedKeys.has('ctrl')) pressedKeys.add('ctrl');
            if (e.altKey && !pressedKeys.has('alt')) pressedKeys.add('alt');
            if (e.shiftKey && !pressedKeys.has('shift')) pressedKeys.add('shift');
            if (e.metaKey && !pressedKeys.has('meta')) pressedKeys.add('meta');

            // Handle regular keys (not modifier keys)
            if (!['control', 'alt', 'shift', 'meta'].includes(keyName)) {
                // Special key mappings
                const keyMappings = {
                    ' ': 'space',
                    'arrowup': 'up',
                    'arrowdown': 'down',
                    'arrowleft': 'left',
                    'arrowright': 'right',
                    'escape': 'esc',
                    '+': 'plus',
                    '-': 'minus',
                    '=': 'plus'
                };

                keyName = keyMappings[keyName] || keyName;
                pressedKeys.add(keyName);
            }

            updateHotkeyDisplay();
        });

        // Keyup event - critical for detecting when all keys are released
        input.addEventListener('keyup', (e) => {
            if (!isRecording) return;

            e.preventDefault();
            e.stopPropagation();

            // Start a timeout to check if all keys are released
            if (recordingTimeout) {
                clearTimeout(recordingTimeout);
            }

            recordingTimeout = setTimeout(() => {
                // Check if any modifier keys are still pressed
                const stillPressed = e.ctrlKey || e.altKey || e.shiftKey || e.metaKey;

                if (!stillPressed && pressedKeys.size > 0) {
                    // All keys released, finalize the hotkey
                    const finalHotkey = input.value;
                    if (finalHotkey && finalHotkey.length > 0) {
                        updateFeedback('✓ Hotkey saved!');
                        if (!isApplyingSettings) {
                            scheduleDirtyCheck();
                        }
                        setTimeout(() => {
                            stopRecording();
                            input.blur();
                        }, 1000);
                    } else {
                        if (!isApplyingSettings) {
                            scheduleDirtyCheck();
                        }
                        stopRecording();
                        input.blur();
                    }
                }
            }, 50); // Small delay to ensure all keyup events are processed
        });

        // Handle escape to cancel
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && isRecording) {
                e.preventDefault();
                pressedKeys.clear();
                input.value = previousValue;
                updateFeedback('Cancelled — restored previous hotkey');
                setTimeout(() => {
                    stopRecording();
                    input.blur();
                }, 500);
            }
        });

        // Prevent context menu during recording
        input.addEventListener('contextmenu', (e) => {
            if (isRecording) {
                e.preventDefault();
            }
        });
    }    // Enhanced save settings with animations
    async function saveSettings(options = {}) {
        const {
            showNotification: shouldNotify = true,
            showAnimation = true,
            suppressSuccessToast = false,
            notifyIfUnchanged = true,
            forceSave = false,
            onSuccess,
            onError
        } = options;

        const canUseSaveButton = Boolean(saveButton);
        const shouldAnimateButton = showAnimation && canUseSaveButton;

        const showErrorFeedback = async () => {
            if (shouldAnimateButton) {
                await showSaveError();
            } else if (canUseSaveButton) {
                resetSaveButton();
            }
        };

        const showSuccessFeedback = async () => {
            if (shouldAnimateButton) {
                await showSaveSuccess();
            } else if (canUseSaveButton) {
                resetSaveButton();
            }
        };

        if (!isDirty && !forceSave) {
            if (shouldNotify && notifyIfUnchanged && !suppressSuccessToast) {
                showNotification('All settings are already saved.', 'info');
            }
            updateSaveButtonState();
            return true;
        }

        let sortSpeedValue = noDelayCheckbox?.checked ? 0 : parseSortSpeed(
            sortSpeedInput.value,
            lastManualSortSpeed > 0 ? lastManualSortSpeed : 0.2
        );

        if (sortSpeedValue > 0) {
            sortSpeedValue = Math.min(1.0, Math.max(0.01, sortSpeedValue));
        } else {
            sortSpeedValue = 0;
        }

        const newSettings = {
            interface: interfaceSelect.value,
            sortHotkey: sortHotkeyInput.value,
            cancelHotkey: cancelHotkeyInput.value,
            sortSpeed: sortSpeedValue,
            resolution: resolutionSelect.value,
            wiresharkPath: wiresharkPathInput ? wiresharkPathInput.value : '',
            includeDevReleases: includeDevCheckbox ? includeDevCheckbox.checked : false,
            autoUpdateEnabled: autoUpdateCheckbox ? autoUpdateCheckbox.checked : true,
            closeToTrayEnabled: closeToTrayCheckbox ? closeToTrayCheckbox.checked : true,
            developerMode: developerModeCheckbox ? developerModeCheckbox.checked : false,
            sortFeedbackSyncEnabled: feedbackSyncCheckbox ? feedbackSyncCheckbox.checked : false,
            overlayEnabled: overlayEnabledCheckbox ? overlayEnabledCheckbox.checked : false,
            overlayHotkey: overlayHotkeyInput ? overlayHotkeyInput.value : 'ctrl+shift+o',
            overlayOpacity: overlayOpacitySlider ? parseInt(overlayOpacitySlider.value, 10) / 100 : 0.92,
            stashTabMapping: tabMapSelects.map(el => el ? parseInt(el.value, 10) : 0)
        };

        if (!newSettings.interface) {
            if (shouldNotify) {
                showNotification('Please select a network interface', 'error');
            }
            return false;
        }

        if (!newSettings.sortHotkey || !newSettings.cancelHotkey) {
            if (shouldNotify) {
                showNotification('Please set the sort and cancel hotkeys', 'error');
            }
            return false;
        }

        if (shouldAnimateButton) {
            startSaveAnimation();
        } else if (canUseSaveButton) {
            saveButton.classList.add('saving');
            saveButton.disabled = true;
            saveButton.setAttribute('aria-disabled', 'true');
        }

        let success = false;

        try {
            const response = await fetch('/api/settings', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(newSettings)
            });

            let result = {};
            try {
                result = await response.json();
            } catch (parseError) {
                if (response.ok) {
                    throw parseError;
                }
            }

            if (!response.ok) {
                const message = Array.isArray(result.errors) && result.errors.length
                    ? result.errors.join('\n')
                    : (result.error || `Failed to save settings (status ${response.status})`);
                throw new Error(message);
            }

            if (result.success) {
                const confirmationPayload = await loadSettings({
                    data: result.settings || null,
                    apply: false
                });

                if (!confirmationPayload) {
                    await showErrorFeedback();

                    if (shouldNotify) {
                        showNotification('Unable to confirm that settings were saved. Please try again.', 'warning');
                    }

                    if (typeof onError === 'function') {
                        onError(result);
                    }

                    return false;
                }

                const normalizedExpected = normalizeForComparison(newSettings);
                const normalizedConfirmed = normalizeForComparison(confirmationPayload);
                const mismatchedKeys = Object.keys(normalizedExpected).filter(
                    (key) => normalizedExpected[key] !== normalizedConfirmed[key]
                );

                if (mismatchedKeys.length > 0) {
                    // Apply server response to form anyway to prevent infinite save loops
                    // (the server's normalized values are authoritative)
                    applySettingsToForm(confirmationPayload);

                    await showErrorFeedback();

                    const mismatchSummary = mismatchedKeys
                        .map((key) => FIELD_LABELS[key] || key)
                        .join(', ');

                    if (shouldNotify) {
                        showNotification(`Some settings couldn't be saved: ${mismatchSummary}.`, 'error');
                    }

                    if (typeof onError === 'function') {
                        onError({ ...result, mismatchedKeys });
                    }

                    return false;
                }

                const appliedSettings = applySettingsToForm(confirmationPayload);

                if (Array.isArray(result.warnings)) {
                    result.warnings
                        .filter(Boolean)
                        .forEach((warning) => showNotification(warning, 'warning'));
                }

                await showSuccessFeedback();

                if (!noDelayCheckbox?.checked && typeof appliedSettings?.sortSpeed === 'number' && appliedSettings.sortSpeed > 0) {
                    lastManualSortSpeed = appliedSettings.sortSpeed;
                }

                if (shouldNotify && !suppressSuccessToast) {
                    showNotification('Settings saved successfully!', 'success');
                }

                if (typeof onSuccess === 'function') {
                    onSuccess(result);
                }

                success = true;
            } else {
                await showErrorFeedback();

                const errorMessage = Array.isArray(result.errors) && result.errors.length
                    ? result.errors.join('\n')
                    : 'Failed to save settings';

                if (shouldNotify) {
                    showNotification(errorMessage, 'error');
                }

                if (result.settings) {
                    applySettingsToForm(result.settings);
                }

                if (typeof onError === 'function') {
                    onError(result);
                }
            }
        } catch (error) {
            console.error('Save error:', error);

            await showErrorFeedback();

            if (shouldNotify) {
                showNotification(error?.message || 'Error saving settings', 'error');
            }

            if (typeof onError === 'function') {
                onError(error);
            }
        } finally {
            if (!shouldAnimateButton && canUseSaveButton) {
                saveButton.classList.remove('saving');
                updateSaveButtonState();
            }
        }

        return success;
    }

    // Start save animation
    function startSaveAnimation() {
        if (!saveButton) {
            return;
        }
        saveButton.disabled = true;
        saveButton.setAttribute('aria-disabled', 'true');
        saveButton.classList.add('saving');

        // Add loading animation to button
        const originalContent = saveButton.innerHTML;
        saveButton.setAttribute('data-original-content', originalContent);

        saveButton.innerHTML = `
            <div class="save-loading" role="status" aria-live="polite">
                <svg class="loading-spinner" viewBox="0 0 50 50">
                    <circle class="loading-path" cx="25" cy="25" r="20" fill="none" stroke-width="6"></circle>
                </svg>
                <span class="save-loading-text">Saving</span>
                <span class="saving-dots" aria-hidden="true">...</span>
            </div>
        `;

        // Add pulse effect to the entire settings container
        const container = document.querySelector('.settings-container');
        container.classList.add('saving-state');
    }

    // Show save success animation
    async function showSaveSuccess() {
        if (!saveButton) {
            return;
        }
        return new Promise((resolve) => {
            // Success animation
            saveButton.classList.remove('saving');
            saveButton.classList.add('save-success');

            saveButton.innerHTML = `
                <div class="save-success-content">
                    <span class="material-icons success-icon">check_circle</span>
                    <span>Saved!</span>
                </div>
            `;

            // Add success ripple effect
            createSuccessRipple();

            // Reset after animation
            setTimeout(() => {
                resetSaveButton();
                resolve();
            }, 2000);
        });
    }

    // Show save error animation
    async function showSaveError() {
        if (!saveButton) {
            return;
        }
        return new Promise((resolve) => {
            saveButton.classList.remove('saving');
            saveButton.classList.add('save-error');

            saveButton.innerHTML = `
                <div class="save-error-content">
                    <span class="material-icons error-icon">error</span>
                    <span>Error!</span>
                </div>
            `;

            // Add error shake effect
            saveButton.classList.add('shake');

            setTimeout(() => {
                saveButton.classList.remove('shake');
                resetSaveButton();
                resolve();
            }, 2000);
        });
    }

    // Reset save button to original state
    function resetSaveButton() {
        if (!saveButton) {
            return;
        }
        saveButton.classList.remove('saving', 'save-success', 'save-error');

        const originalContent = saveButton.getAttribute('data-original-content');
        if (originalContent) {
            saveButton.innerHTML = originalContent;
        }

        saveButton.removeAttribute('data-original-content');

        const container = document.querySelector('.settings-container');
        container.classList.remove('saving-state');
        updateSaveButtonState();
    }

    // Create success ripple effect
    function createSuccessRipple() {
        if (!saveButton) {
            return;
        }
        const ripple = document.createElement('div');
        ripple.className = 'success-ripple';

        const rect = saveButton.getBoundingClientRect();
        const size = Math.max(rect.width, rect.height);

        ripple.style.width = ripple.style.height = size + 'px';
        ripple.style.left = (rect.width / 2 - size / 2) + 'px';
        ripple.style.top = (rect.height / 2 - size / 2) + 'px';

        saveButton.appendChild(ripple);

        // Remove ripple after animation
        setTimeout(() => {
            if (ripple.parentNode) {
                ripple.parentNode.removeChild(ripple);
            }
        }, 600);
    }

    function buildDefaultSettingsSnapshot() {
        return {
            interface: '',
            sortHotkey: 'ctrl+f11',
            cancelHotkey: 'ctrl+f12',
            sortSpeed: 0.2,
            resolution: 'Auto',
            wiresharkPath: wiresharkPathInput ? (wiresharkPathInput.dataset.defaultValue || '') : '',
            includeDevReleases: false,
            autoUpdateEnabled: true,
            closeToTrayEnabled: true,
            noDelay: false,
            developerMode: false,
            sortFeedbackSyncEnabled: false
        };
    }

    function applyDefaultSettingsLocally(defaults) {
        runWithApplyingFlag(() => {
            interfaceSelect.value = defaults.interface;
            sortHotkeyInput.value = defaults.sortHotkey;
            cancelHotkeyInput.value = defaults.cancelHotkey;
            sortSpeedInput.value = toDisplaySpeed(defaults.sortSpeed);
            resolutionSelect.value = defaults.resolution;
            if (wiresharkPathInput) {
                wiresharkPathInput.value = defaults.wiresharkPath;
            }
            if (noDelayCheckbox) {
                noDelayCheckbox.checked = Boolean(!defaults.sortSpeed || defaults.noDelay);
            }
            if (includeDevCheckbox) {
                includeDevCheckbox.checked = Boolean(defaults.includeDevReleases);
            }
            if (autoUpdateCheckbox) {
                autoUpdateCheckbox.checked = defaults.autoUpdateEnabled !== false;
            }
            if (closeToTrayCheckbox) {
                closeToTrayCheckbox.checked = defaults.closeToTrayEnabled !== false;
            }

            if (developerModeCheckbox) {
                developerModeCheckbox.checked = Boolean(defaults.developerMode);
            }
        });

        lastManualSortSpeed = defaults.sortSpeed;

        runWithApplyingFlag(() => {
            applyNoDelayUIState();
        });

        if (typeof window.setCloseToTrayEnabled === 'function') {
            window.setCloseToTrayEnabled(defaults.closeToTrayEnabled !== false);
        }

        syncDeveloperModeFlag(Boolean(defaults.developerMode));

        evaluateUnsavedChanges();
    }

    async function applyDefaultsAndPersist() {
        const defaults = buildDefaultSettingsSnapshot();
        applyDefaultSettingsLocally(defaults);

        const saved = await saveSettings({
            showNotification: false,
            showAnimation: false,
            forceSave: true
        });

        if (saved) {
            showNotification('Settings reset to defaults', 'success');
        } else {
            showNotification('Defaults applied locally, but saving failed. Please try again.', 'warning');
        }

        return saved;
    }

    function showResetDefaultsModal() {
        if (typeof createUnsavedChangesModal !== 'function') {
            if (window.confirm('Reset all settings to their recommended defaults?')) {
                void applyDefaultsAndPersist();
            }
            return;
        }

        createUnsavedChangesModal({
            title: 'Reset all settings?',
            message: 'This will instantly restore the recommended defaults and overwrite your current tweaks.',
            bodyText: 'Resetting will:',
            bodyTips: [
                { icon: 'settings_backup_restore', text: 'Restore network interface, Wireshark path, hotkeys, timing, and release channel to their defaults.' },
                { icon: 'cloud_done', text: 'Auto-save the defaults immediately so they persist across restarts.' }
            ],
            saveLabel: 'Reset to Defaults',
            discardLabel: 'Keep Current Settings',
            cancelLabel: 'Cancel',
            onSave: () => applyDefaultsAndPersist(),
            onDiscard: () => {
                showNotification('Kept your current settings.', 'info');
            },
            onCancel: () => {
                evaluateUnsavedChanges();
            }
        });
    }

    // Event listeners
    if (saveButton) {
        saveButton.addEventListener('click', () => {
            void saveSettings();
        });
    }
    resetButton?.addEventListener('click', showResetDefaultsModal);
    refreshResolutionBtn?.addEventListener('click', loadDetectedResolution);
    browseWiresharkButton?.addEventListener('click', pickWiresharkPath);
    detectWiresharkButton?.addEventListener('click', autoDetectWireshark);
    clearQuestDataButton?.addEventListener('click', handleClearQuestData);
    clearCharacterDataButton?.addEventListener('click', handleClearCharacterData);

    // Check for Updates button
    if (checkForUpdatesButton) {
        checkForUpdatesButton.addEventListener('click', async () => {
            checkForUpdatesButton.disabled = true;
            checkForUpdatesButton.classList.add('checking');
            const icon = checkForUpdatesButton.querySelector('.material-icons');
            if (icon) icon.classList.add('spin-icon');

            let outcome = 'success';
            try {
                if (typeof checkForUpdates === 'function') {
                    const data = await checkForUpdates(true);
                    if (!data || !data.updateAvailable) {
                        showNotification('You are on the latest version.', 'success');
                    }
                } else {
                    const response = await fetch('/api/update/check', { cache: 'no-store' });
                    const data = await response.json();
                    if (data && data.updateAvailable) {
                        showNotification(`Update available: v${data.latestVersion}`, 'info');
                        if (typeof maybeShowUpdatePopup === 'function') {
                            maybeShowUpdatePopup(data);
                        }
                    } else {
                        showNotification('You are on the latest version.', 'success');
                    }
                }
            } catch (error) {
                console.error('Manual update check failed:', error);
                showNotification('Update check failed. Try again later.', 'error');
                outcome = 'error';
            } finally {
                checkForUpdatesButton.classList.remove('checking');
                if (icon) icon.classList.remove('spin-icon');

                const flashClass = outcome === 'success' ? 'check-success' : 'check-error';
                checkForUpdatesButton.classList.add(flashClass);
                setTimeout(() => {
                    checkForUpdatesButton.classList.remove(flashClass);
                    checkForUpdatesButton.disabled = false;
                }, 1200);
            }
        });
    }

    // Setup hotkey recording
    setupHotkeyRecording(sortHotkeyInput);
    setupHotkeyRecording(cancelHotkeyInput);
    setupHotkeyRecording(overlayHotkeyInput);

    // Form validation
    noDelayCheckbox?.addEventListener('change', () => {
        if (noDelayCheckbox.checked) {
            const currentValue = parseSortSpeed(
                sortSpeedInput.value,
                lastManualSortSpeed > 0 ? lastManualSortSpeed : 0.2
            );
            if (currentValue > 0) {
                lastManualSortSpeed = currentValue;
            }
        }
        applyNoDelayUIState();
    });

    sortSpeedInput.addEventListener('input', () => {
        let value = parseSortSpeed(
            sortSpeedInput.value,
            lastManualSortSpeed > 0 ? lastManualSortSpeed : 0.2
        );

        if (value <= 0) {
            if (noDelayCheckbox) {
                noDelayCheckbox.checked = true;
                applyNoDelayUIState();
            } else {
                sortSpeedInput.value = toDisplaySpeed(0.01);
                lastManualSortSpeed = 0.01;
            }
            return;
        }

        value = Math.min(1.0, Math.max(0.01, value));
        lastManualSortSpeed = value;
        sortSpeedInput.value = toDisplaySpeed(value);

        if (noDelayCheckbox && noDelayCheckbox.checked) {
            noDelayCheckbox.checked = false;
            applyNoDelayUIState();
        }
    });

    const trackableElements = [
        { element: interfaceSelect, events: ['change'] },
        { element: sortHotkeyInput, events: ['change', 'blur'] },
        { element: cancelHotkeyInput, events: ['change', 'blur'] },
        { element: sortSpeedInput, events: ['input', 'change'] },
        { element: noDelayCheckbox, events: ['change'] },
        { element: resolutionSelect, events: ['change'] },
        { element: wiresharkPathInput, events: ['input', 'change'] },
        { element: includeDevCheckbox, events: ['change'] },
        { element: autoUpdateCheckbox, events: ['change'] },
        { element: closeToTrayCheckbox, events: ['change'] },
        { element: developerModeCheckbox, events: ['change'] },
        { element: feedbackSyncCheckbox, events: ['change'] },
        { element: overlayEnabledCheckbox, events: ['change'] },
        { element: overlayHotkeyInput, events: ['change', 'blur'] },
        { element: overlayOpacitySlider, events: ['change'] },
        ...tabMapSelects.filter(Boolean).map(el => ({ element: el, events: ['change'] }))
    ];

    trackableElements
        .filter(item => item.element)
        .forEach(({ element, events }) => {
            events.forEach(evt => {
                element.addEventListener(evt, () => {
                    if (isApplyingSettings) {
                        return;
                    }
                    scheduleDirtyCheck();
                    scheduleAutoSave();
                });
            });
        });

    // ═══════════════════════════════════════════════════════════
    //  Stash Calibration  (native Win32 overlay via pywebview API)
    // ═══════════════════════════════════════════════════════════
    const calibrateBtn = document.getElementById('calibrateStash');

    async function runCalibrationOverlay() {
        if (!calibrateBtn) return;

        if (!window.pywebview?.api?.open_calibration) {
            showNotification('Calibration requires the desktop app.', 'warning');
            return;
        }

        calibrateBtn.disabled = true;
        calibrateBtn.classList.add('loading');

        try {
            const startResult = await window.pywebview.api.open_calibration();

            if (!startResult?.started) {
                showNotification(startResult?.error || 'Could not start calibration.', 'error');
                return;
            }

            // Poll for result — the overlay runs in a background thread
            const result = await new Promise((resolve) => {
                const poll = setInterval(async () => {
                    try {
                        const status = await window.pywebview.api.calibration_status();
                        if (!status?.running) {
                            clearInterval(poll);
                            resolve(status);
                        }
                    } catch {
                        clearInterval(poll);
                        resolve({ saved: false });
                    }
                }, 500);
            });

            if (result && result.saved) {
                const submitMsg = result.submitted
                    ? ' Data submitted for improvement — thank you!'
                    : '';
                showNotification('Calibration saved.' + submitMsg, 'success');
            }
        } catch (err) {
            console.error('Calibration error:', err);
            showNotification('Calibration failed — see console for details.', 'error');
        } finally {
            calibrateBtn.disabled = false;
            calibrateBtn.classList.remove('loading');
        }
    }

    calibrateBtn?.addEventListener('click', runCalibrationOverlay);

    setupUnsavedChangesGuard();

    // Save immediately when the user switches browser tabs or minimizes the window
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden' && isDirty) {
            scheduleAutoSave({ immediate: true });
        }
    });

    // Also save when the window loses focus (e.g. Alt+Tab)
    window.addEventListener('blur', () => {
        if (isDirty) {
            scheduleAutoSave({ immediate: true });
        }
    });

    window.addEventListener('unload', () => {
        window.unsavedChangesGuard = null;
        window.removeEventListener('beforeunload', beforeUnloadHandler);
    });

    // Initialize in parallel
    // (already handled above)
});
