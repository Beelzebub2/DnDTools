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
            content.style.transform = 'translateY(5px)';
            // Reduced delay to 100ms and only apply if the transition is visible
            requestAnimationFrame(() => {
                setTimeout(() => {
                    window.location.href = link.href;
                }, 80);
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
        if (window.pywebview && window.pywebview.api && window.pywebview.api.download_and_launch_update) {
            progress.textContent = 'Downloading and launching updater...';
            try {
                const result = await window.pywebview.api.download_and_launch_update();
                if (result && result.success) {
                    progress.textContent = 'Updater launched. This app will now close.';
                } else {
                    progress.textContent = 'Update failed: ' + (result && result.error ? result.error : 'Unknown error');
                }
            } catch (e) {
                progress.textContent = 'Update failed: ' + e;
            }
        } else {
            progress.innerHTML = `
                <div style="text-align: left; margin-top: 8px;">
                    <div style="margin-bottom: 6px;">Click the button below to download the update:</div>
                    <a href="/api/download_update" download="DnDTools_new.exe" style="display:inline-block;background:#e4c869;color:#222;padding:8px 18px;border-radius:5px;text-decoration:none;font-weight:bold;margin-top:8px;">Download Update</a>
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
        }
    };
}

document.addEventListener('DOMContentLoaded', () => {
    checkForUpdates();
});