// Format datetime stamps consistently across the app
function formatDate(isoString) {
    return new Date(isoString).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// Handle API errors consistently
function handleApiError(error, element) {
    console.error('API Error:', error);
    if (element) {
        element.innerHTML = `
            <div class="error-message">
                An error occurred. Please try again later.
            </div>
        `;
    }
}

// Global notification function
function showNotification(message, type = 'error') {
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.textContent = message;

    // Add inline styling to position the notification below the topbar
    notification.style.position = 'fixed';
    notification.style.top = '60px'; // Position below the topbar
    notification.style.right = '20px';
    notification.style.zIndex = '9999';
    notification.style.padding = '12px 20px';
    notification.style.borderRadius = '4px';
    notification.style.boxShadow = '0 2px 10px rgba(0, 0, 0, 0.2)';
    notification.style.animation = 'slideIn 0.3s ease-out forwards';

    document.body.appendChild(notification);

    setTimeout(() => {
        notification.classList.add('fade-out');
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}

// Format number values consistently across the app
function formatNumber(num) {
    return new Intl.NumberFormat().format(num);
}

// Loading state helper
function setLoading(element, isLoading) {
    if (isLoading) {
        element.classList.add('loading');
        element.dataset.originalText = element.textContent;
        element.textContent = 'Loading...';
        element.disabled = true;
    } else {
        element.classList.remove('loading');
        element.textContent = element.dataset.originalText;
        element.disabled = false;
        delete element.dataset.originalText;
    }
}

// Add active class to current navigation link
document.addEventListener('DOMContentLoaded', () => {
    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-link').forEach(link => {
        if (link.getAttribute('href') === currentPath) {
            link.classList.add('active');
        }
    });

    // Handle navigation transitions
    const links = document.querySelectorAll('.nav-link');
    links.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const content = document.querySelector('.content');
            content.style.opacity = '0';
            content.style.transform = 'translateY(5px)';            // Reduced delay to 50ms and only apply if the transition is visible
            requestAnimationFrame(() => {
                setTimeout(() => {
                    window.location.href = link.href;
                }, 50); // Reduced from 80ms
            });
        });
    });

    // Listen for background initialization completion
    window.addEventListener('backgroundInitDone', () => {
        showNotification('Data loaded!', 'success');
        // Example: hide a loading spinner if you have one
        const spinner = document.getElementById('loading-spinner');
        if (spinner) spinner.style.display = 'none';
        // You can also trigger data refresh or enable UI here
    });
});

// Version check and update notification
async function checkForUpdates() {
    try {
        // Fetch local version
        const localRes = await fetch('/api/local_version');
        const localData = await localRes.json();
        const localVersion = localData.version;

        // Fetch latest online version
        const remoteRes = await fetch('/api/version');
        const remoteData = await remoteRes.json();
        const remoteVersion = (remoteData.version || '').replace(/^v/, '').trim();
        const releaseUrl = remoteData.release_url || 'https://github.com/Beelzebub2/DnDTools/releases/latest';

        if (remoteVersion && isNewerVersion(remoteVersion, localVersion)) {
            showUpdatePopup(remoteVersion, localVersion, releaseUrl);
        }
    } catch (e) {
        // Silently ignore update check errors
    }
}

function isNewerVersion(remote, local) {
    // Simple semver compare: '1.2.3' > '1.2.2'
    const r = remote.split('.').map(Number);
    const l = local.split('.').map(Number);
    for (let i = 0; i < Math.max(r.length, l.length); i++) {
        if ((r[i] || 0) > (l[i] || 0)) return true;
        if ((r[i] || 0) < (l[i] || 0)) return false;
    }
    return false;
}

function showUpdatePopup(remoteVersion, localVersion, releaseUrl) {
    // Remove any existing popup
    const existing = document.getElementById('update-popup');
    if (existing) existing.remove();
    const popup = document.createElement('div');
    popup.id = 'update-popup';
    popup.style.position = 'fixed';
    popup.style.bottom = '30px';
    popup.style.right = '30px';
    popup.style.background = '#222';
    popup.style.color = '#e4c869';
    popup.style.padding = '20px 28px';
    popup.style.borderRadius = '10px';
    popup.style.boxShadow = '0 2px 12px #000a';
    popup.style.zIndex = '99999';
    popup.innerHTML = `
        <div style="display:flex;align-items:center;gap:12px;">
            <span class="material-icons" style="font-size:28px;color:#e4c869;">system_update_alt</span>
            <div>
                <b>New Update Available!</b><br>
                <span style="font-size:13px;">Current: v${localVersion} &nbsp; Latest: v${remoteVersion}</span>
            </div>
        </div>
        <div style="margin-top:10px;text-align:right;">
            <a href="${releaseUrl}" target="_blank" style="color:#e4c869;text-decoration:underline;font-size:14px;">Release Page</a>
            <button id="update-now-btn" style="margin-left:10px;background:#e4c869;color:#222;border:none;padding:6px 16px;border-radius:5px;cursor:pointer;font-size:14px;">Update Now</button>
            <button id="close-update-popup" style="margin-left:10px;background:none;border:none;color:#aaa;font-size:16px;cursor:pointer;">✕</button>
        </div>
        <div id="update-progress" style="margin-top:10px;font-size:13px;display:none;"></div>
    `;
    document.body.appendChild(popup);
    document.getElementById('close-update-popup').onclick = () => popup.remove();

    document.getElementById('update-now-btn').onclick = async () => {
        const progress = document.getElementById('update-progress');
        progress.style.display = 'block';

        // Show initial loading message
        progress.textContent = 'Checking for update...';
        progress.style.color = '#e4c869';

        if (window.pywebview && window.pywebview.api && window.pywebview.api.download_and_launch_update) {
            // Use the built-in update function if available (desktop app mode)
            progress.textContent = 'Downloading and launching updater...';
            try {
                const result = await window.pywebview.api.download_and_launch_update();
                if (result && result.success) {
                    progress.textContent = 'Updater launched. This app will now close.';
                    progress.style.color = '#4CAF50';
                } else {
                    progress.textContent = 'Update failed: ' + (result && result.error ? result.error : 'Unknown error');
                    progress.style.color = '#f44336';
                    console.error('Update failed with result:', result);
                }
            } catch (e) {
                progress.textContent = 'Update failed: ' + e;
                progress.style.color = '#f44336';
                console.error('Exception during update:', e);
            }
        } else {
            // Manual download mode
            try {
                // Try to fetch the update - this will trigger a download if successful
                progress.textContent = 'Downloading update...';

                const response = await fetch('/api/download_update');

                if (!response.ok) {
                    let errorMsg = 'Failed to download update';

                    // Try to get more detailed error message from response
                    try {
                        const errorData = await response.json();
                        errorMsg = errorData.error || errorMsg;
                        console.error('Update failed with details:', errorData);
                    } catch (jsonErr) {
                        console.error('Could not parse error response', jsonErr);
                    }

                    progress.textContent = `Update failed: ${errorMsg}`;
                    progress.style.color = '#f44336';
                    return;
                }

                // If we reach here, the download started successfully
                progress.innerHTML = `
                    <div style="text-align: left; margin-top: 8px;">
                        <div style="margin-bottom: 6px; color: #4CAF50;">✓ Update download started</div>
                        <div style="margin-top: 8px; font-size: 12px; color: #aaa;">
                            After download completes:
                            <ol style="margin-top: 4px; padding-left: 20px;">
                                <li>Close this application</li>
                                <li>Run DnDTools_new.exe</li>
                                <li>The new version will automatically replace the old one</li>
                            </ol>
                        </div>
                    </div>
                `;
            } catch (e) {
                progress.textContent = `Network error: ${e.message}`;
                progress.style.color = '#f44336';
                console.error('Fetch error during update:', e);
            }
        }
    };
}

// Helper to fetch market price via backend proxy to avoid CORS
async function fetchMarketPrice(itemId) {
    try {
        const response = await fetch(`/api/market/price/${itemId}`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        return await response.json();
    } catch (error) {
        console.error('Failed to fetch market price:', error);
        return { success: false, error: error.message };
    }
}

document.addEventListener('DOMContentLoaded', () => {
    checkForUpdates();
});