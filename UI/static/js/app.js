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
        const localRes = await fetch('/api/version');
        const localData = await localRes.json();
        const localVersion = localData.version;

        // Fetch latest release info from GitHub API
        const apiUrl = 'https://api.github.com/repos/Beelzebub2/DnDTools/releases/latest';
        const remoteRes = await fetch(apiUrl, { cache: 'no-store' });
        if (!remoteRes.ok) return;
        const releaseData = await remoteRes.json();
        const remoteVersion = (releaseData.tag_name || '').replace(/^v/, '').trim();

        if (remoteVersion && isNewerVersion(remoteVersion, localVersion)) {
            showUpdatePopup(remoteVersion, localVersion, releaseData.html_url);
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
            <a href="${releaseUrl || 'https://github.com/Beelzebub2/DnDTools/releases'}" target="_blank" style="color:#e4c869;text-decoration:underline;font-size:14px;">Download Page</a>
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
        progress.textContent = 'Fetching latest release...';
        try {
            // Get latest release info
            const apiUrl = 'https://api.github.com/repos/Beelzebub2/DnDTools/releases/latest';
            const res = await fetch(apiUrl, { cache: 'no-store' });
            const release = await res.json();
            const asset = (release.assets || []).find(a => a.name === 'DnDTools.exe');
            if (!asset) {
                progress.textContent = 'Could not find DnDTools.exe in the latest release.';
                return;
            }
            progress.textContent = 'Downloading update...';
            // Download the file
            const downloadRes = await fetch(asset.browser_download_url);
            if (!downloadRes.ok) {
                progress.textContent = 'Failed to download update.';
                return;
            }
            const blob = await downloadRes.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'DnDTools_new.exe';
            document.body.appendChild(a);
            a.click();
            setTimeout(() => {
                window.URL.revokeObjectURL(url);
                a.remove();
            }, 1000);
            progress.textContent = 'Update downloaded as DnDTools_new.exe. Attempting to launch updater...';
            // Try to launch the updater if running in pywebview
            if (window.pywebview && window.pywebview.api && window.pywebview.api.launch_updater) {
                try {
                    // Get current exe path from backend
                    const exePath = await window.pywebview.api.get_executable_path();
                    await window.pywebview.api.launch_updater('DnDTools_new.exe', exePath);
                    progress.textContent = 'Updater launched. The app will now close.';
                    setTimeout(() => {
                        if (window.pywebview.api.close_window) window.pywebview.api.close_window();
                    }, 1500);
                } catch (e) {
                    progress.textContent = 'Failed to launch updater automatically. Please close the app and run DnDTools_new.exe manually.';
                }
            } else {
                progress.textContent = 'Update downloaded as DnDTools_new.exe. Please close the app and run DnDTools_new.exe to complete the update.';
            }
        } catch (e) {
            progress.textContent = 'Error downloading update: ' + e;
        }
    };
}

document.addEventListener('DOMContentLoaded', () => {
    checkForUpdates();
});