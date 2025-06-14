// Global script for updating the sidebar capture indicator across all pages
document.addEventListener('DOMContentLoaded', async () => {
    // Try to find the sidebar capture indicator
    const sidebarCaptureIndicator = document.getElementById('sidebarCaptureIndicator');

    if (sidebarCaptureIndicator) {
        try {
            // Wait for pywebview to be ready
            await waitForPywebview();

            // Get initial capture state
            const response = await fetch('/api/capture/state');
            const state = await response.json();

            // Update sidebar pulse indicator state
            updateSidebarIndicator(sidebarCaptureIndicator, state.running);

            // Set up polling to update the indicator every 3 seconds
            setInterval(async () => {
                try {
                    const response = await fetch('/api/capture/state');
                    const state = await response.json();

                    // Update indicator classes
                    updateSidebarIndicator(sidebarCaptureIndicator, state.running);
                } catch (error) {
                    console.error('Error updating sidebar capture indicator:', error);
                    // On error, remove all animation classes
                    sidebarCaptureIndicator.classList.remove('active', 'stopping');
                }
            }, 3000);
        } catch (error) {
            console.error('Error initializing sidebar capture indicator:', error);
            // On error, remove all animation classes
            sidebarCaptureIndicator.classList.remove('active', 'stopping');
        }
    }
});

// Helper function to update the sidebar indicator
function updateSidebarIndicator(indicator, isRunning) {
    if (isRunning) {
        indicator.classList.add('active');
        indicator.classList.remove('stopping');
    } else {
        indicator.classList.remove('active', 'stopping');
    }
}

// Utility function to wait for pywebview to be ready
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
