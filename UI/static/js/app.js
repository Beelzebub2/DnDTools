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
function showNotification(message, type) {
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.textContent = message;
    document.body.appendChild(notification);
    setTimeout(() => notification.remove(), 3000);
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
});