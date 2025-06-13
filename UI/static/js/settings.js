// Simplified Settings JavaScript
document.addEventListener('DOMContentLoaded', async () => {
    const interfaceSelect = document.getElementById('interface');
    const sortHotkeyInput = document.getElementById('sortHotkey');
    const cancelHotkeyInput = document.getElementById('cancelHotkey');
    const sortSpeedInput = document.getElementById('sortSpeed');
    const resolutionSelect = document.getElementById('resolution');
    const detectedResolutionSpan = document.querySelector('#detectedResolution');
    const refreshResolutionBtn = document.getElementById('refreshResolution');
    const saveButton = document.getElementById('saveSettings'); const resetButton = document.getElementById('resetSettings');

    let currentSettings = {};

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
            currentSettings = await response.json();

            // Populate form fields
            if (currentSettings.interface) {
                interfaceSelect.value = currentSettings.interface;
            }

            sortHotkeyInput.value = currentSettings.sortHotkey || 'ctrl+alt+s';
            cancelHotkeyInput.value = currentSettings.cancelHotkey || 'ctrl+alt+x';
            sortSpeedInput.value = currentSettings.sortSpeed || 0.2;
            resolutionSelect.value = currentSettings.resolution || 'Auto';
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
                detectedResolutionSpan.textContent = `Detected: ${data.resolution || 'Not detected'}`;
            }
        } catch (error) {
            console.error('Failed to detect resolution:', error);
            if (detectedResolutionSpan) {
                detectedResolutionSpan.textContent = 'Detection failed';
            }
        }
    }    // Enhanced hotkey recording functionality
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
                        setTimeout(() => {
                            stopRecording();
                            input.blur();
                        }, 1000);
                    } else {
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
    async function saveSettings() {
        const newSettings = {
            interface: interfaceSelect.value,
            sortHotkey: sortHotkeyInput.value,
            cancelHotkey: cancelHotkeyInput.value,
            sortSpeed: parseFloat(sortSpeedInput.value),
            resolution: resolutionSelect.value
        };

        // Start save animation
        startSaveAnimation();

        try {
            const response = await fetch('/api/settings', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(newSettings)
            });

            const result = await response.json(); if (result.success) {
                currentSettings = newSettings;
                await showSaveSuccess();
                showNotification('Settings saved successfully!', 'success');
            } else {
                await showSaveError();
                showNotification('Failed to save settings', 'error');
            }
        } catch (error) {
            console.error('Save error:', error);
            await showSaveError();
            showNotification('Error saving settings', 'error');
        }
    }

    // Start save animation
    function startSaveAnimation() {
        saveButton.disabled = true;
        saveButton.classList.add('saving');

        // Add loading animation to button
        const originalContent = saveButton.innerHTML;
        saveButton.setAttribute('data-original-content', originalContent);

        saveButton.innerHTML = `
            <div class="save-loading">
                <div class="loading-spinner"></div>
                <span>Saving...</span>
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
            sortHotkey: 'ctrl+alt+s',
            cancelHotkey: 'ctrl+alt+x',
            sortSpeed: 0.2,
            resolution: 'Auto'
        };

        // Update form fields
        interfaceSelect.value = defaultSettings.interface;
        sortHotkeyInput.value = defaultSettings.sortHotkey;
        cancelHotkeyInput.value = defaultSettings.cancelHotkey;
        sortSpeedInput.value = defaultSettings.sortSpeed; resolutionSelect.value = defaultSettings.resolution; showNotification('Settings reset to defaults', 'success');
    }

    // Event listeners
    saveButton.addEventListener('click', saveSettings);
    resetButton.addEventListener('click', resetSettings);
    refreshResolutionBtn?.addEventListener('click', loadDetectedResolution);

    // Setup hotkey recording
    setupHotkeyRecording(sortHotkeyInput);
    setupHotkeyRecording(cancelHotkeyInput);

    // Form validation
    sortSpeedInput.addEventListener('input', () => {
        const value = parseFloat(sortSpeedInput.value);
        if (value < 0.01) sortSpeedInput.value = 0.01;
        if (value > 0.5) sortSpeedInput.value = 0.5;
    });

    // Initialize
    await loadInterfaces();
    await loadSettings();
    await loadDetectedResolution();
});
