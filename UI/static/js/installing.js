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

async function restartApp() {
    // Show notification
    const statusText = document.getElementById('status-text');
    if (statusText) {
        statusText.textContent = 'Restarting app to apply...';
    }
    // Wait 3 seconds
    await new Promise(res => setTimeout(res, 3000));
    // Call backend to restart
    fetch('/api/restart', { method: 'POST' });
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