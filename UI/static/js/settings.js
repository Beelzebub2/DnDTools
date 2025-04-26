window.addEventListener('load', async () => {
    const interfaceSelect = document.getElementById('interface');
    const sortHotkeyInput = document.getElementById('sortHotkey');
    const cancelHotkeyInput = document.getElementById('cancelHotkey');
    const sortSpeedInput = document.getElementById('sortSpeed');
    const sortSpeedValue = document.getElementById('sortSpeedValue');
    const resolutionSelect = document.getElementById('resolution');
    const detectedResolution = document.getElementById('detectedResolution');

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
        if (settings.interface) {
            interfaceSelect.value = settings.interface;
        }
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
    });

    // Populate interface select
    interfaceSelect.innerHTML = '';
    interfaces.forEach(name => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        if (name === currentSettings.interface) opt.selected = true;
        interfaceSelect.appendChild(opt);
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
        });
    }

    setupHotkeyInput(sortHotkeyInput);
    setupHotkeyInput(cancelHotkeyInput);

    // Save settings
    document.getElementById('saveSettings').onclick = async () => {
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
            } else {
                showNotification('Failed to save settings', 'error');
            }
        } catch (error) {
            console.error('Failed to save settings:', error);
            showNotification('Failed to save settings', 'error');
        }
    };
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

    resolutionSelect.addEventListener('change', showNotTestedWarning);

    // Show on page load and after detected resolution loads
    setTimeout(showNotTestedWarning, 600);

    // Also update after detected resolution is loaded
    setTimeout(() => {
        showNotTestedWarning();
    }, 1000);
});
