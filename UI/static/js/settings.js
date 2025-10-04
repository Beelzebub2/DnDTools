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
    const saveButton = document.getElementById('saveSettings'); const resetButton = document.getElementById('resetSettings');

    let currentSettings = {};
    let normalizedSettingsSnapshot = null;
    let lastManualSortSpeed = 0.01;
    let isApplyingSettings = false;
    let isDirty = false;
    let changeCheckScheduled = false;
    const QUEST_PROGRESS_STORAGE_KEY = 'dndtools.questProgress.v1';

    const beforeUnloadHandler = (event) => {
        if (!isDirty) {
            return undefined;
        }
        event.preventDefault();
        event.returnValue = '';
        return '';
    };

    window.addEventListener('beforeunload', beforeUnloadHandler);

    setUnsavedChanges(false);

    function normalizeForComparison(settings = {}) {
        const toNumber = (value) => {
            const numeric = Number(value);
            return Number.isFinite(numeric) ? numeric : 0;
        };

        return {
            interface: (settings.interface || '').trim(),
            sortHotkey: (settings.sortHotkey || '').trim().toLowerCase(),
            cancelHotkey: (settings.cancelHotkey || '').trim().toLowerCase(),
            sortSpeed: toNumber(settings.sortSpeed),
            resolution: (settings.resolution || 'Auto').trim(),
            wiresharkPath: (settings.wiresharkPath || '').trim()
        };
    }

    function updateCurrentSettings(settings) {
        currentSettings = {
            interface: settings.interface || '',
            sortHotkey: settings.sortHotkey || 'ctrl+f11',
            cancelHotkey: settings.cancelHotkey || 'ctrl+f12',
            sortSpeed: parseSortSpeed(settings.sortSpeed, 0.2),
            resolution: settings.resolution || 'Auto',
            wiresharkPath: settings.wiresharkPath || ''
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
            wiresharkPath: wiresharkPathInput ? wiresharkPathInput.value : ''
        };
    }

    function setUnsavedChanges(value) {
        isDirty = Boolean(value);
        window.hasUnsavedChanges = isDirty;
        document.body.classList.toggle('has-unsaved-settings', isDirty);
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
            shouldPrompt: () => isDirty,
            requestNavigation: (href) => {
                showUnsavedPrompt(() => navigateTo(href), 'navigation');
            },
            requestClose: (proceed) => {
                showUnsavedPrompt(() => {
                    if (typeof proceed === 'function') {
                        proceed();
                    }
                }, 'close');
            }
        };
    }

    // Load data sequentially to ensure interfaces are loaded before settings
    try {
        await loadInterfaces();
        await loadSettings();
        await loadDetectedResolution();
    } catch (error) {
        console.error('Error during settings initialization:', error);
        showNotification('Some settings failed to load', 'warning');
    }

    // Load network interfaces
    async function loadInterfaces() {
        try {
            const response = await fetch('/api/network_interfaces');
            const data = await response.json();
            interfaceSelect.innerHTML = '';

            if (data.interfaces && data.interfaces.length > 0) {
                data.interfaces.forEach(iface => {
                    const option = document.createElement('option');
                    option.value = iface;
                    option.textContent = iface;
                    interfaceSelect.appendChild(option);
                });
            } else {
                const option = document.createElement('option');
                option.value = '';
                option.textContent = 'No interfaces found';
                interfaceSelect.appendChild(option);
            }
        } catch (error) {
            console.error('Failed to load interfaces:', error);
            showNotification('Failed to load network interfaces', 'error');
        }
    }

    // Load current settings
    async function loadSettings() {
        try {
            const response = await fetch('/api/settings');
            const data = await response.json();

            updateCurrentSettings(data);

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
            });

            runWithApplyingFlag(() => {
                applyNoDelayUIState();
            });

            evaluateUnsavedChanges();
        } catch (error) {
            console.error('Failed to load settings:', error);
            showNotification('Failed to load settings', 'error');
        }
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
            saveLabel: 'Clear Data',
            discardLabel: 'Keep Data',
            cancelLabel: 'Cancel',
            onSave: async () => {
                if (typeof setLoading === 'function') {
                    setLoading(clearQuestDataButton, true);
                }
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
                } finally {
                    if (typeof setLoading === 'function') {
                        setLoading(clearQuestDataButton, false);
                    }
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

    // Enhanced hotkey recording functionality
    function setupHotkeyRecording(input) {
        let pressedKeys = new Set();
        let isRecording = false;
        let recordingTimeout = null;
        let feedbackElement = null;

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
                box-shadow: 0 4px 12px rgba(228, 200, 105, 0.3);
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

        function startRecording() {
            isRecording = true;
            pressedKeys.clear();
            input.style.backgroundColor = 'rgba(228, 200, 105, 0.1)';
            input.style.borderColor = 'var(--accent-gold)';
            input.value = '';
            updateFeedback('Press keys... (release all to save)');
        }

        function stopRecording() {
            isRecording = false;
            input.style.backgroundColor = '';
            input.style.borderColor = '';
            removeFeedbackElement();

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
                    'escape': 'esc'
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
                input.value = '';
                updateFeedback('Cancelled');
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
            onSuccess,
            onError
        } = options;

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
            wiresharkPath: wiresharkPathInput ? wiresharkPathInput.value : ''
        };

        if (!newSettings.interface) {
            if (shouldNotify) {
                showNotification('Please select a network interface', 'error');
            }
            return false;
        }

        if (!newSettings.sortHotkey || !newSettings.cancelHotkey) {
            if (shouldNotify) {
                showNotification('Please set both hotkeys', 'error');
            }
            return false;
        }

        if (showAnimation) {
            startSaveAnimation();
        } else {
            saveButton.disabled = true;
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

            const result = await response.json();

            if (result.success) {
                updateCurrentSettings(newSettings);

                if (!noDelayCheckbox?.checked && sortSpeedValue > 0) {
                    lastManualSortSpeed = sortSpeedValue;
                }

                if (wiresharkPathInput) {
                    wiresharkPathInput.dataset.defaultValue = wiresharkPathInput.value;
                }

                if (showAnimation) {
                    await showSaveSuccess();
                } else {
                    saveButton.disabled = false;
                }

                if (shouldNotify && !suppressSuccessToast) {
                    showNotification('Settings saved successfully!', 'success');
                }

                setUnsavedChanges(false);
                evaluateUnsavedChanges();

                if (typeof onSuccess === 'function') {
                    onSuccess(result);
                }

                success = true;
            } else {
                if (showAnimation) {
                    await showSaveError();
                } else {
                    saveButton.disabled = false;
                }

                if (shouldNotify) {
                    showNotification('Failed to save settings', 'error');
                }

                if (typeof onError === 'function') {
                    onError(result);
                }
            }
        } catch (error) {
            console.error('Save error:', error);

            if (showAnimation) {
                await showSaveError();
            } else {
                saveButton.disabled = false;
            }

            if (shouldNotify) {
                showNotification('Error saving settings', 'error');
            }

            if (typeof onError === 'function') {
                onError(error);
            }
        }

        return success;
    }

    // Start save animation
    function startSaveAnimation() {
        saveButton.disabled = true;
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
        saveButton.disabled = false;
        saveButton.classList.remove('saving', 'save-success', 'save-error');

        const originalContent = saveButton.getAttribute('data-original-content');
        if (originalContent) {
            saveButton.innerHTML = originalContent;
        }

        const container = document.querySelector('.settings-container');
        container.classList.remove('saving-state');
    }

    // Create success ripple effect
    function createSuccessRipple() {
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

    // Reset settings
    async function resetSettings() {
        if (!confirm('Are you sure you want to reset all settings to defaults?')) {
            return;
        }

        const defaultSettings = {
            interface: '',
            sortHotkey: 'ctrl+f11',
            cancelHotkey: 'ctrl+f12',
            sortSpeed: 0.2,
            resolution: 'Auto',
            wiresharkPath: wiresharkPathInput ? (wiresharkPathInput.dataset.defaultValue || '') : ''
        };

        runWithApplyingFlag(() => {
            interfaceSelect.value = defaultSettings.interface;
            sortHotkeyInput.value = defaultSettings.sortHotkey;
            cancelHotkeyInput.value = defaultSettings.cancelHotkey;
            sortSpeedInput.value = toDisplaySpeed(defaultSettings.sortSpeed);
            resolutionSelect.value = defaultSettings.resolution;
            if (wiresharkPathInput) {
                wiresharkPathInput.value = defaultSettings.wiresharkPath;
            }
            if (noDelayCheckbox) {
                noDelayCheckbox.checked = false;
            }
        });

        lastManualSortSpeed = defaultSettings.sortSpeed;

        runWithApplyingFlag(() => {
            applyNoDelayUIState();
        });

        evaluateUnsavedChanges();

        showNotification('Settings reset to defaults', 'success');
    }

    // Event listeners
    saveButton.addEventListener('click', () => {
        void saveSettings();
    });
    resetButton.addEventListener('click', resetSettings);
    refreshResolutionBtn?.addEventListener('click', loadDetectedResolution);
    browseWiresharkButton?.addEventListener('click', pickWiresharkPath);
    detectWiresharkButton?.addEventListener('click', autoDetectWireshark);
    clearQuestDataButton?.addEventListener('click', handleClearQuestData);

    // Setup hotkey recording
    setupHotkeyRecording(sortHotkeyInput);
    setupHotkeyRecording(cancelHotkeyInput);

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
        { element: wiresharkPathInput, events: ['input', 'change'] }
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
                });
            });
        });

    setupUnsavedChangesGuard();

    window.addEventListener('unload', () => {
        window.unsavedChangesGuard = null;
        window.removeEventListener('beforeunload', beforeUnloadHandler);
    });

    // Initialize in parallel
    // (already handled above)
});
