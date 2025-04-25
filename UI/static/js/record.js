document.addEventListener('DOMContentLoaded', () => {
    const captureSwitch = document.getElementById('captureSwitch');
    const switchLabel = document.getElementById('switchLabel');
    const status = document.getElementById('captureStatus');
    const characterSelect = document.querySelector('.character-select');
    const characterGrid = document.getElementById('characterGrid');
    let pollingInterval = null;

    async function loadCharacters() {
        try {
            const response = await fetch('/api/characters');
            const characters = await response.json();
            characterGrid.innerHTML = '';
            characters.forEach(char => {
                const card = document.createElement('div');
                card.className = 'character-card';
                card.innerHTML = `
                    <div class="character-name">${char.nickname}</div>
                    <div class="character-info">
                        <div>Class: ${char.class}</div>
                        <div>Level: ${char.level}</div>
                    </div>
                `;
                card.onclick = () => window.location.href = `/character/${char.id}`;
                characterGrid.appendChild(card);
            });
            if (characters.length > 0) {
                characterSelect.style.display = 'block';
            }
        } catch (error) {
            console.error('Failed to load characters:', error);
            showNotification('Failed to load characters', 'error');
        }
    }

    function startPolling() {
        if (pollingInterval) clearInterval(pollingInterval);
        pollingInterval = setInterval(loadCharacters, 2000);
    }

    function stopPolling() {
        if (pollingInterval) {
            clearInterval(pollingInterval);
            pollingInterval = null;
        }
    }

    async function updateCaptureState(isRunning) {
        try {
            // Update UI to show processing state
            switchLabel.textContent = isRunning ? 'Starting...' : 'Stopping...';
            status.textContent = isRunning ? 'Starting capture...' : 'Stopping capture...';
            status.className = 'status-text ' + (isRunning ? 'starting' : 'stopping');
            captureSwitch.disabled = true;  // Prevent further clicks while processing

            const endpoint = isRunning ? '/api/capture/switch/start' : '/api/capture/switch/stop';
            const response = await fetch(endpoint, { method: 'POST' });
            const result = await response.json();

            if (!result.success) {
                throw new Error('Operation failed');
            }

            // Verify the state actually changed by checking the server repeatedly
            await verifyState(isRunning);

            // Update UI with successful state
            captureSwitch.checked = isRunning;
            captureSwitch.disabled = false;
            switchLabel.textContent = isRunning ? 'Stop Capture' : 'Start Capture';
            status.textContent = isRunning ? 'Capturing...' : 'Capture Off';
            status.className = isRunning ? 'status-text capturing' : 'status-text';

            if (isRunning) {
                startPolling();
                showNotification('Capture started', 'success');
            } else {
                stopPolling();
                showNotification('Capture stopped', 'info');
            }
        } catch (error) {
            console.error('Failed to update capture state:', error);
            // Revert UI to previous state
            captureSwitch.checked = !isRunning;
            captureSwitch.disabled = false;
            switchLabel.textContent = !isRunning ? 'Stop Capture' : 'Start Capture';
            status.textContent = 'Capture error';
            status.className = 'status-text error';
            showNotification(`Failed to ${isRunning ? 'start' : 'stop'} capture`, 'error');
        }
    }

    async function verifyState(expectedState) {
        const maxAttempts = 5;
        const delayMs = 500;

        for (let i = 0; i < maxAttempts; i++) {
            try {
                const resp = await fetch('/api/capture/state');
                const state = await resp.json();

                if (state.running === expectedState) {
                    return true;
                }

                await new Promise(resolve => setTimeout(resolve, delayMs));
            } catch (error) {
                console.warn('Error verifying state, retrying...', error);
            }
        }
        throw new Error('Failed to verify capture state change');
    }

    // Wait for pywebview to be ready
    function waitForPywebview() {
        return new Promise((resolve) => {
            if (window.pywebview && window.pywebview.api) {
                resolve();
                return;
            }

            const checkInterval = setInterval(() => {
                if (window.pywebview && window.pywebview.api) {
                    clearInterval(checkInterval);
                    resolve();
                }
            }, 100);
        });
    }

    async function restartCapture() {
        try {
            switchLabel.textContent = 'Restarting...';
            status.textContent = 'Restarting capture...';
            status.className = 'status-text restarting';
            captureSwitch.disabled = true;

            await waitForPywebview();
            const response = await fetch('/api/capture/switch/restart', { method: 'POST' });
            const result = await response.json();

            if (!result.success) {
                throw new Error('Restart failed');
            }

            // Verify the state is running
            await verifyState(true);

            // Update UI with running state
            captureSwitch.checked = true;
            captureSwitch.disabled = false;
            switchLabel.textContent = 'Stop Capture';
            status.textContent = 'Capturing...';
            status.className = 'status-text capturing';
            startPolling();

        } catch (error) {
            console.error('Failed to restart capture:', error);
            captureSwitch.checked = false;
            captureSwitch.disabled = false;
            switchLabel.textContent = 'Start Capture';
            status.textContent = 'Capture error';
            status.className = 'status-text error';
            showNotification('Failed to restart capture', 'error');
        }
    }

    // Handle switch toggle
    captureSwitch.addEventListener('change', () => {
        updateCaptureState(captureSwitch.checked);
    });

    // Initialize state on page load
    async function init() {
        try {
            await waitForPywebview();
            const resp = await fetch('/api/capture/state');
            const state = await resp.json();

            // Just update UI to reflect current running state, no restart here
            captureSwitch.checked = state.running;
            switchLabel.textContent = state.running ? 'Stop Capture' : 'Start Capture';
            status.textContent = state.running ? 'Capturing...' : 'Capture Off';
            status.className = state.running ? 'status-text capturing' : 'status-text';

            if (state.running) {
                startPolling();
            }

            // Load initial character list
            await loadCharacters();
        } catch (error) {
            console.error('Failed to get initial capture state:', error);
            // Default to off state
            captureSwitch.checked = false;
            switchLabel.textContent = 'Start Capture';
            status.textContent = 'Capture Off';
            status.className = 'status-text';
            showNotification('Failed to get capture state', 'error');
        }
    }

    init();
});
