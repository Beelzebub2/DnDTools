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
    popup.style.background = 'var(--bg-secondary, #241c17)';
    popup.style.color = 'var(--text-primary, #e4c869)';
    popup.style.padding = '24px 32px';
    popup.style.borderRadius = '8px';
    popup.style.border = '1px solid var(--border-color, #392e24)';
    popup.style.boxShadow = '0 8px 32px rgba(0, 0, 0, 0.6), 0 2px 8px rgba(228, 200, 105, 0.1)';
    popup.style.zIndex = '99999';
    popup.style.maxWidth = '380px';
    popup.style.minWidth = '320px';
    popup.style.backdropFilter = 'blur(8px)';
    popup.style.animation = 'slideInFromRight 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
    
    // Add animation keyframes if not already defined
    if (!document.getElementById('update-popup-styles')) {
        const style = document.createElement('style');
        style.id = 'update-popup-styles';
        style.textContent = `
            @keyframes slideInFromRight {
                from {
                    transform: translateX(100%);
                    opacity: 0;
                }
                to {
                    transform: translateX(0);
                    opacity: 1;
                }
            }
            
            .update-popup-btn {
                background: var(--accent-gold, #e4c869);
                color: #1a1412;
                border: none;
                padding: 10px 20px;
                border-radius: 6px;
                cursor: pointer;
                font-weight: 600;
                font-size: 14px;
                text-decoration: none;
                display: inline-flex;
                align-items: center;
                gap: 8px;
                transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
                box-shadow: 0 2px 8px rgba(228, 200, 105, 0.2);
            }
            
            .update-popup-btn:hover {
                transform: translateY(-2px);
                box-shadow: 0 4px 16px rgba(228, 200, 105, 0.3);
                background: #f0d478;
            }
            
            .update-popup-close {
                background: transparent;
                border: 1px solid var(--border-color, #392e24);
                color: var(--text-secondary, #a89a6c);
                padding: 8px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 16px;
                line-height: 1;
                transition: all 0.2s ease;
                width: 32px;
                height: 32px;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            
            .update-popup-close:hover {
                background: rgba(255, 255, 255, 0.05);
                color: var(--text-primary, #e4c869);
                border-color: var(--accent-gold, #e4c869);
            }
        `;
        document.head.appendChild(style);
    }
    
    popup.innerHTML = `
        <div style="display: flex; align-items: flex-start; gap: 16px; margin-bottom: 16px;">
            <div style="
                background: linear-gradient(135deg, var(--accent-gold, #e4c869), #f0d478);
                border-radius: 50%;
                padding: 12px;
                box-shadow: 0 4px 12px rgba(228, 200, 105, 0.3);
            ">
                <span class="material-icons" style="font-size: 24px; color: #1a1412;">system_update_alt</span>
            </div>
            <div style="flex: 1;">
                <h3 style="
                    color: var(--accent-gold, #e4c869);
                    font-size: 18px;
                    font-weight: 600;
                    margin: 0 0 8px 0;
                    letter-spacing: 0.5px;
                ">Update Available!</h3>
                <div style="
                    font-size: 14px;
                    color: var(--text-secondary, #a89a6c);
                    line-height: 1.4;
                ">
                    <div style="margin-bottom: 4px;">
                        <span style="color: var(--text-primary, #e4c869);">Current:</span> v${localVersion}
                    </div>
                    <div>
                        <span style="color: var(--text-primary, #e4c869);">Latest:</span> 
                        <span style="color: var(--accent-gold, #e4c869); font-weight: 600;">v${remoteVersion}</span>
                    </div>
                </div>
            </div>
            <button id="close-update-popup" class="update-popup-close" title="Close">✕</button>
        </div>
        <div style="
            display: flex;
            justify-content: flex-end;
            padding-top: 12px;
            border-top: 1px solid var(--border-color, #392e24);
        ">
            <a href="${releaseUrl}" target="_blank" class="update-popup-btn">
                <span class="material-icons" style="font-size: 18px;">open_in_new</span>
                View Release
            </a>
        </div>
    `;
    document.body.appendChild(popup);
    document.getElementById('close-update-popup').onclick = () => popup.remove();
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