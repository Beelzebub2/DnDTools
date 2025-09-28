// Global script for updating the sidebar capture indicator across all pages
let globalPollingInterval = null;
let lastKnownState = null;
let sidebarIndicatorEl = null;

function resolveSidebarIndicator() {
    if (!sidebarIndicatorEl || !document.body.contains(sidebarIndicatorEl)) {
        sidebarIndicatorEl = document.getElementById('sidebarCaptureIndicator');
    }
    return sidebarIndicatorEl;
}

function applySidebarState(state = {}, options = {}) {
    const indicator = resolveSidebarIndicator();
    const running = Boolean(state && state.running);

    if (indicator) {
        if (options && options.transition === 'stopping' && !running) {
            indicator.classList.add('stopping');
            indicator.classList.remove('active');
        } else if (options && options.transition === 'starting' && running) {
            indicator.classList.remove('stopping');
            indicator.classList.add('active');
        }

        updateSidebarIndicator(indicator, running);
    }

    lastKnownState = running;
    return running;
}

if (typeof window.applyCaptureState !== 'function') {
    window.applyCaptureState = applySidebarState;
}
window.__applySidebarState = applySidebarState;

document.addEventListener('DOMContentLoaded', async () => {
    const sidebarCaptureIndicator = resolveSidebarIndicator();

    if (sidebarCaptureIndicator) {
        try {
            // Wait for pywebview to be ready
            await waitForPywebview();

            // Get initial capture state with timeout
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 2000);

            try {
                const response = await fetch('/api/capture/state', {
                    signal: controller.signal,
                    cache: 'no-cache'
                });
                clearTimeout(timeoutId);
                const state = await response.json();

                applySidebarState(state);

                // Set up optimized polling with exponential backoff on errors
                startOptimizedPolling(sidebarCaptureIndicator);
            } catch (error) {
                clearTimeout(timeoutId);
                console.warn('Initial capture state fetch failed:', error);
                // Continue with default state
                applySidebarState({ running: false });
            }
        } catch (error) {
            console.error('Error initializing sidebar capture indicator:', error);
            // On error, remove all animation classes
            sidebarCaptureIndicator.classList.remove('active', 'stopping');
        }
    }
});

// Helper function to update the sidebar indicator
function updateSidebarIndicator(indicator, isRunning) {
    const target = indicator || resolveSidebarIndicator();
    if (!target) return;

    if (isRunning) {
        target.classList.add('active');
        target.classList.remove('stopping');
    } else {
        target.classList.remove('active', 'stopping');
    }
}

// Utility function to wait for pywebview to be ready with timeout
function waitForPywebview() {
    return new Promise((resolve) => {
        // Set a reasonable timeout to prevent infinite waiting
        const timeout = setTimeout(() => {
            console.warn('PyWebView API not available, continuing without it');
            resolve();
        }, 1000); // Reduced from 2000

        if (window.pywebview && window.pywebview.api) {
            clearTimeout(timeout);
            resolve();
            return;
        }

        const checkInterval = setInterval(() => {
            if (window.pywebview && window.pywebview.api) {
                clearInterval(checkInterval);
                clearTimeout(timeout);
                resolve();
            }
        }, 25); // Reduced from 50 for faster detection

        // Clean up after timeout
        setTimeout(() => {
            clearInterval(checkInterval);
        }, 1000); // Reduced from 2000
    });
}

// Optimized polling function with smart updates
function startOptimizedPolling(indicator) {
    if (globalPollingInterval) {
        clearInterval(globalPollingInterval);
    }

    let errorCount = 0;
    let pollingInterval = 10000; // Start with 10 seconds for better performance

    const resolveIndicator = () => indicator || resolveSidebarIndicator();

    globalPollingInterval = setInterval(async () => {
        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 1500);

            const response = await fetch('/api/capture/state', {
                signal: controller.signal,
                cache: 'no-cache'
            });
            clearTimeout(timeoutId);
            const state = await response.json();

            // Only update if state actually changed
            if (state.running !== lastKnownState) {
                applySidebarState(state);
            }

            // Reset error count and interval on success
            errorCount = 0;
            if (pollingInterval !== 10000) {
                pollingInterval = 10000;
                clearInterval(globalPollingInterval);
                startOptimizedPolling(indicator);
            }
        } catch (error) {
            errorCount++;
            console.warn('Error updating sidebar capture indicator:', error);

            // Implement exponential backoff on errors
            if (errorCount > 3) {
                pollingInterval = Math.min(pollingInterval * 1.5, 60000); // Max 60 seconds
                clearInterval(globalPollingInterval);
                startOptimizedPolling(resolveIndicator());
                errorCount = 0;
            }

            // On error, keep last known state
            if (lastKnownState !== null) {
                applySidebarState({ running: lastKnownState });
            }
        }
    }, pollingInterval);
}

// Clean up polling on page unload
window.addEventListener('beforeunload', () => {
    if (globalPollingInterval) {
        clearInterval(globalPollingInterval);
        globalPollingInterval = null;
    }
});
