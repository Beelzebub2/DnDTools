let socket = null;

function updateProgress(progress, status) {
    const progressFill = document.getElementById('progress-fill');
    const statusText = document.getElementById('status-text');

    if (progressFill) {
        progressFill.style.width = `${progress}%`;
    }

    if (statusText && status) {
        statusText.textContent = status;
    }
}

async function checkInstallationStatus() {
    try {
        const response = await fetch('/api/check_npcap');
        const data = await response.json();

        if (data.installed) {
            // Redirect to index if already installed
            window.location.href = '/';
        } else {
            // Start installation
            startInstallation();
        }
    } catch (error) {
        console.error('Error checking installation status:', error);
        updateProgress(0, 'Error checking installation status');
    }
}

function showRestartPopup() {
    // Create popup element
    const popup = document.createElement('div');
    popup.style.position = 'fixed';
    popup.style.top = '50%';
    popup.style.left = '50%';
    popup.style.transform = 'translate(-50%, -50%)';
    popup.style.backgroundColor = '#222';
    popup.style.color = '#e4c869';
    popup.style.padding = '20px';
    popup.style.borderRadius = '10px';
    popup.style.boxShadow = '0 0 20px rgba(0,0,0,0.5)';
    popup.style.zIndex = '99999';
    popup.style.textAlign = 'center';
    popup.style.minWidth = '300px';

    popup.innerHTML = `
        <div style="font-size: 24px; margin-bottom: 10px;">
            <span style="font-size:36px;vertical-align:middle;margin-right:10px;">⚙️</span>
            Restart Required
        </div>
        <p style="margin: 15px 0;">Application will restart to apply changes...</p>
        <div id="restart-countdown" style="font-size: 18px; font-weight: bold;">3</div>
    `;

    document.body.appendChild(popup);

    // Countdown timer
    let count = 3;
    const interval = setInterval(() => {
        count--;
        const countdownElement = document.getElementById('restart-countdown');
        if (countdownElement) {
            countdownElement.textContent = count;
        }
        if (count <= 0) {
            clearInterval(interval);
            // Perform actual restart after countdown
            performRestart();
        }
    }, 1000);
}

async function performRestart() {
    // Try to show restarting message
    const statusText = document.getElementById('status-text');
    if (statusText) {
        statusText.textContent = 'Restarting...';
    }

    try {
        // Try various restart methods in order of preference
        if (window.pywebview && window.pywebview.api) {
            if (typeof window.pywebview.api.restart === 'function') {
                await window.pywebview.api.restart();
                return;
            }
        }

        // Fallback to standard API endpoint
        await fetch('/api/restart', { method: 'POST' });

        // Give the server a moment to initiate restart
        setTimeout(() => {
            // If we're still here after 2 seconds, try to reload
            setTimeout(() => {
                window.location.reload();
            }, 2000);
        }, 500);
    } catch (err) {
        console.error('Error during restart:', err);
        // Last resort: simply reload the page
        window.location.reload();
    }
}

async function restartApp() {
    // First update status
    const statusText = document.getElementById('status-text');
    if (statusText) {
        statusText.textContent = 'Installation complete! Preparing to restart...';
    }

    // Short delay before showing the popup
    await new Promise(res => setTimeout(res, 1000));

    // Show restart popup with countdown
    showRestartPopup();
}

async function startInstallation() {
    try {
        updateProgress(10, 'Starting Npcap installation...');
        const response = await fetch('/api/install_npcap', {
            method: 'POST'
        });
        const data = await response.json();

        if (data.success) {
            updateProgress(100, 'Installation complete!');
            restartApp();
        } else {
            updateProgress(0, data.error || 'Installation failed');
        }
    } catch (error) {
        console.error('Error during installation:', error);
        updateProgress(0, 'Installation failed');
    }
}

// Start the process when the page loads
window.addEventListener('DOMContentLoaded', checkInstallationStatus);