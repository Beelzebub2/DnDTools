/**
 * Common utility functions used across the application
 * This centralizes common functionality to reduce code duplication
 */

/**
 * Format date strings consistently across the application
 * @param {string} dateString - ISO date string or other date format
 * @returns {string} Formatted date string
 */
function formatDate(dateString) {
    if (!dateString) return 'Unknown';
    try {
        const date = new Date(dateString);
        if (isNaN(date.getTime())) return dateString;

        return date.toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
    } catch (e) {
        console.warn('Error formatting date:', e);
        return dateString;
    }
}

/**
 * Format datetime stamps with time included
 * @param {string} isoString - ISO datetime string
 * @returns {string} Formatted datetime string
 */
function formatDateTime(isoString) {
    if (!isoString) return 'Unknown';
    try {
        const date = new Date(isoString);
        if (isNaN(date.getTime())) return isoString;

        return date.toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch (e) {
        console.warn('Error formatting datetime:', e);
        return isoString;
    }
}

/**
 * Handle API errors consistently across the application
 * @param {Error|string} error - The error object or message
 * @param {HTMLElement} element - Element to display error in (optional)
 * @param {string} customMessage - Custom error message (optional)
 */
function handleApiError(error, element = null, customMessage = null) {
    const errorMessage = customMessage || 'An error occurred. Please try again later.';
    console.error('API Error:', error);

    if (element) {
        element.innerHTML = `
            <div class="error-state">
                <span class="material-icons">error_outline</span>
                <h3>Error Loading Data</h3>
                <p>${errorMessage}</p>
            </div>`;
    }
}

/**
 * Show notification to user
 * @param {string} message - Message to display
 * @param {string} type - Type of notification ('success', 'error', 'info', 'warning')
 * @param {number} duration - Duration in milliseconds (default: 5000)
 */
function showNotification(message, type = 'info', duration = 5000) {
    // Remove any existing notifications
    const existingNotifications = document.querySelectorAll('.app-notification');
    existingNotifications.forEach(n => n.remove());

    const notification = document.createElement('div');
    notification.className = `app-notification notification-${type}`;

    const iconMap = {
        'success': 'check_circle',
        'error': 'error',
        'warning': 'warning',
        'info': 'info'
    };

    notification.innerHTML = `
        <span class="material-icons">${iconMap[type] || 'info'}</span>
        <span class="notification-message">${message}</span>
        <button class="notification-close" onclick="this.parentElement.remove()">
            <span class="material-icons">close</span>
        </button>
    `;

    document.body.appendChild(notification);

    // Auto-remove after duration
    setTimeout(() => {
        if (notification.parentElement) {
            notification.remove();
        }
    }, duration);
}

/**
 * Format number values consistently
 * @param {number} num - Number to format
 * @returns {string} Formatted number string
 */
function formatNumber(num) {
    if (typeof num !== 'number') return num;
    return num.toLocaleString();
}

/**
 * Set loading state for an element
 * @param {HTMLElement} element - Element to set loading state for
 * @param {boolean} isLoading - Whether to show loading state
 */
function setLoading(element, isLoading) {
    if (!element) return;

    if (isLoading) {
        element.classList.add('loading');
        element.innerHTML = `
            <div class="loading-spinner">
                <div class="spinner"></div>
                <p>Loading...</p>
            </div>`;
    } else {
        element.classList.remove('loading');
    }
}

/**
 * Debounce function to limit how often a function can be called
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in milliseconds
 * @returns {Function} Debounced function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Validate if a string is a valid JSON
 * @param {string} str - String to validate
 * @returns {boolean} True if valid JSON
 */
function isValidJSON(str) {
    try {
        JSON.parse(str);
        return true;
    } catch (e) {
        return false;
    }
}

/**
 * Escape HTML to prevent XSS attacks
 * @param {string} unsafe - Unsafe string
 * @returns {string} HTML-escaped string
 */
function escapeHtml(unsafe) {
    const div = document.createElement('div');
    div.textContent = unsafe;
    return div.innerHTML;
}

/**
 * Compare two version strings (semver-like)
 * @param {string} version1 - First version
 * @param {string} version2 - Second version
 * @returns {number} -1 if v1 < v2, 0 if equal, 1 if v1 > v2
 */
function compareVersions(version1, version2) {
    const v1parts = version1.split('.').map(Number);
    const v2parts = version2.split('.').map(Number);
    const maxLength = Math.max(v1parts.length, v2parts.length);

    for (let i = 0; i < maxLength; i++) {
        const v1part = v1parts[i] || 0;
        const v2part = v2parts[i] || 0;

        if (v1part < v2part) return -1;
        if (v1part > v2part) return 1;
    }

    return 0;
}

/**
 * Check if a newer version is available
 * @param {string} remoteVersion - Remote version string
 * @param {string} localVersion - Local version string
 * @returns {boolean} True if remote version is newer
 */
function isNewerVersion(remoteVersion, localVersion) {
    return compareVersions(remoteVersion, localVersion) > 0;
}

// Export functions for modules (if using ES6 modules)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        formatDate,
        formatDateTime,
        handleApiError,
        showNotification,
        formatNumber,
        setLoading,
        debounce,
        isValidJSON,
        escapeHtml,
        compareVersions,
        isNewerVersion
    };
}