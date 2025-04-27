window.addEventListener('load', async () => {
    const interfaceSelect = document.getElementById('interface');
    const sortHotkeyInput = document.getElementById('sortHotkey');
    const cancelHotkeyInput = document.getElementById('cancelHotkey');
    const sortSpeedInput = document.getElementById('sortSpeed');
    const sortSpeedValue = document.getElementById('sortSpeedValue');
    const resolutionSelect = document.getElementById('resolution');
    const detectedResolution = document.getElementById('detectedResolution');

    // Track if changes have been made
    let hasUnsavedChanges = false;
    // Track if we're handling navigation ourselves
    let isHandlingNavigation = false;

    // Set the global variable for window close detection
    window.hasUnsavedChanges = false;

    // Load available interfaces
    let interfaces = [];
    try {
        const resp = await fetch('/api/network_interfaces');
        interfaces = (await resp.json()).interfaces || [];
    } catch (e) {
        console.error('Failed to load network interfaces:', e);
    }

    // Load current settings
    let currentSettings = {};
    try {
        const settings = await fetch('/api/settings').then(r => r.json());
        currentSettings = settings;

        // Set current values
        if (settings.sortHotkey) {
            sortHotkeyInput.value = settings.sortHotkey;
        }
        if (settings.cancelHotkey) {
            cancelHotkeyInput.value = settings.cancelHotkey;
        }
        if (settings.sortSpeed) {
            sortSpeedInput.value = settings.sortSpeed;
            sortSpeedValue.textContent = settings.sortSpeed + 's';
        } else {
            sortSpeedInput.value = 0.2;
            sortSpeedValue.textContent = '0.2s';
        }
        if (settings.resolution) {
            resolutionSelect.value = settings.resolution;
        } else {
            resolutionSelect.value = 'Auto';
        }
    } catch (error) {
        console.error('Failed to load settings:', error);
    }

    // Load and display auto-detected resolution
    try {
        const autoRes = await fetch('/api/auto_resolution').then(r => r.json());
        detectedResolution.querySelector('span:last-child').textContent = `Detected: ${autoRes.resolution}`;
    } catch (error) {
        console.error('Failed to load auto-detected resolution:', error);
        detectedResolution.querySelector('span:last-child').textContent = 'Detected: Error loading';
    }

    // Update value display on number input change
    sortSpeedInput.addEventListener('input', () => {
        sortSpeedValue.textContent = sortSpeedInput.value + 's';
        hasUnsavedChanges = true;
        window.hasUnsavedChanges = true;
    });

    // Populate interface select
    interfaceSelect.innerHTML = '';

    // Add a helper message if no interfaces found
    if (interfaces.length === 0) {
        const opt = document.createElement('option');
        opt.value = "";
        opt.textContent = "No interfaces found";
        interfaceSelect.appendChild(opt);

        // Add warning message
        const warning = document.createElement('div');
        warning.className = 'warning-banner';
        warning.innerHTML = '<span class="material-icons">warning</span><span>No network interfaces detected. Check your network connection.</span>';
        interfaceSelect.parentNode.insertBefore(warning, interfaceSelect.nextSibling);
    } else {
        // Add interfaces to dropdown
        interfaces.forEach(name => {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            interfaceSelect.appendChild(opt);
        });

        // Check if current setting exists in available interfaces
        const currentInterface = currentSettings.interface;
        if (currentInterface && interfaces.includes(currentInterface)) {
            interfaceSelect.value = currentInterface;
        } else if (currentInterface) {
            // If current interface doesn't exist, add it with a warning
            const opt = document.createElement('option');
            opt.value = currentInterface;
            opt.textContent = `${currentInterface} (not available)`;
            interfaceSelect.appendChild(opt);
            interfaceSelect.value = currentInterface;

            // Add warning message
            const warning = document.createElement('div');
            warning.className = 'warning-banner';
            warning.innerHTML = `<span class="material-icons">warning</span><span>Interface '${currentInterface}' is not currently available. Consider selecting another interface.</span>`;
            interfaceSelect.parentNode.insertBefore(warning, interfaceSelect.nextSibling);
        }
    }

    // Add change listeners to track unsaved changes
    interfaceSelect.addEventListener('change', () => {
        hasUnsavedChanges = true;
        window.hasUnsavedChanges = true;
    });

    resolutionSelect.addEventListener('change', () => {
        hasUnsavedChanges = true;
        window.hasUnsavedChanges = true;
        showNotTestedWarning();
    });

    sortSpeedInput.addEventListener('input', () => {
        sortSpeedValue.textContent = sortSpeedInput.value + 's';
        hasUnsavedChanges = true;
        window.hasUnsavedChanges = true;
    });

    // Hotkey recording logic
    function setupHotkeyInput(input) {
        let keys = new Set();

        input.addEventListener('focus', () => {
            input.value = '';
            keys.clear();
        });

        input.addEventListener('keydown', (e) => {
            e.preventDefault();

            if (e.key === 'Escape') {
                input.blur();
                return;
            }

            if (e.key === 'Backspace') {
                input.value = '';
                keys.clear();
                return;
            }

            // Don't record modifier keys alone
            if (['Control', 'Alt', 'Shift', 'Meta'].includes(e.key)) {
                return;
            }

            keys.clear();
            if (e.ctrlKey) keys.add('Ctrl');
            if (e.altKey) keys.add('Alt');
            if (e.shiftKey) keys.add('Shift');
            if (e.metaKey) keys.add('Meta');
            keys.add(e.key.length === 1 ? e.key.toUpperCase() : e.key);

            input.value = Array.from(keys).join('+');
            input.blur();
            hasUnsavedChanges = true;
            window.hasUnsavedChanges = true;
        });
    }

    setupHotkeyInput(sortHotkeyInput);
    setupHotkeyInput(cancelHotkeyInput);

    // Function to save settings
    const saveSettings = async () => {
        try {
            const response = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    interface: interfaceSelect.value,
                    sortHotkey: sortHotkeyInput.value || 'Ctrl+Alt+S',
                    cancelHotkey: cancelHotkeyInput.value || 'Ctrl+Alt+X',
                    sortSpeed: parseFloat(sortSpeedInput.value),
                    resolution: resolutionSelect.value
                })
            });

            const result = await response.json();
            if (result.success) {
                showNotification('Settings saved successfully', 'success');
                hasUnsavedChanges = false;
                window.hasUnsavedChanges = false;

                // If we changed interfaces, reload the page after 1 second to apply changes
                if (currentSettings.interface !== interfaceSelect.value) {
                    showNotification('Network interface changed, restarting capture...', 'info');
                    setTimeout(() => {
                        location.reload();
                    }, 1000);
                }
                return true;
            } else {
                showNotification('Failed to save settings', 'error');
                return false;
            }
        } catch (error) {
            console.error('Failed to save settings:', error);
            showNotification('Failed to save settings', 'error');
            return false;
        }
    };

    // Save settings button click handler
    document.getElementById('saveSettings').onclick = saveSettings;

    // Handle navigation within the app by intercepting all link clicks
    document.addEventListener('click', function (e) {
        // Find if the click was on a link or inside a link
        let targetElement = e.target;
        while (targetElement != null) {
            if (targetElement.tagName === 'A') {
                break;
            }
            targetElement = targetElement.parentElement;
        }

        // If it's a link and has an href attribute and is an internal link
        if (targetElement &&
            targetElement.tagName === 'A' &&
            targetElement.getAttribute('href') &&
            !targetElement.getAttribute('href').startsWith('#') &&
            !targetElement.getAttribute('href').startsWith('http')) {

            if (hasUnsavedChanges) {
                e.preventDefault();
                e.stopPropagation();

                isHandlingNavigation = true;
                const targetHref = targetElement.getAttribute('href');

                if (typeof window.createUnsavedChangesModal === 'function') {
                    window.createUnsavedChangesModal(
                        // onSave
                        async () => {
                            const success = await saveSettings();
                            if (success) {
                                window.location.href = targetHref;
                            }
                        },
                        // onDiscard
                        () => {
                            hasUnsavedChanges = false;
                            window.hasUnsavedChanges = false;
                            window.location.href = targetHref;
                        },
                        // onCancel
                        () => {
                            isHandlingNavigation = false;
                        }
                    );
                }
            }
        }
    }, true); // Use capturing to intercept events before they reach the links

    // Override the browser's beforeunload event
    window.addEventListener('beforeunload', function (e) {
        if (hasUnsavedChanges && !isHandlingNavigation) {
            // Modern browsers require returning a value for the confirmation dialog to appear
            e.preventDefault();
            e.returnValue = '';
            return '';
        }
    });

    function showNotTestedWarning() {
        const warningBanner = document.getElementById('resolutionWarning');
        let resVal = resolutionSelect.value;
        let detected = detectedResolution.querySelector('span:last-child').textContent.replace('Detected: ', '');

        if (
            (resVal !== '1920x1080' && resVal !== 'Auto') ||
            (resVal === 'Auto' && detected !== '1920x1080' && detected !== 'Not detected' && detected !== 'Error loading')
        ) {
            warningBanner.style.display = 'flex';
        } else {
            warningBanner.style.display = 'none';
        }
    }

    // Show on page load and after detected resolution loads
    setTimeout(showNotTestedWarning, 600);

    // Also update after detected resolution is loaded
    setTimeout(() => {
        showNotTestedWarning();
    }, 1000);
});
