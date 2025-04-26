window.addEventListener('load', async () => {
    const interfaceSelect = document.getElementById('interface');
    const sortHotkeyInput = document.getElementById('sortHotkey');
    const cancelHotkeyInput = document.getElementById('cancelHotkey');
    const sortSpeedInput = document.getElementById('sortSpeed');
    const sortSpeedValue = document.getElementById('sortSpeedValue');

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
    } catch (error) {
        console.error('Failed to load settings:', error);
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
                    sortSpeed: parseFloat(sortSpeedInput.value)
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
});

// Remove this function as it's now in app.js
// function showNotification(message, type) { ... }
